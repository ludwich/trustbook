from __future__ import annotations

import json

import httpx
import pytest

from app.capabilities.adapters.anthropic import AnthropicAdapter
from app.capabilities.adapters.azure_openai import AzureOpenAIAdapter
from app.capabilities.adapters.base import AdapterError, SearchResult
from app.capabilities.adapters.factory import (
    build_chat_adapter,
    build_search_adapter,
    build_stt_adapter,
)
from app.capabilities.adapters.mock import MockChatAdapter
from app.capabilities.adapters.openai_compatible import OpenAICompatibleAdapter
from app.capabilities.adapters.search import (
    BRAVE_URL,
    TAVILY_URL,
    BraveAdapter,
    SearxngAdapter,
    TavilyAdapter,
    clean_snippet,
    demote_homepages,
    is_homepage,
)
from app.capabilities.adapters.stt_openai import OpenAITranscriptionAdapter
from app.capabilities.http import OutboundHttp, RequestLogRepository
from app.capabilities.registry import EndpointConfig
from tests.fakes import Router, chat_reply

AZURE = "https://res.openai.azure.com"


def test_azure_url_and_header(http: OutboundHttp, router: Router) -> None:
    router.json("POST", AZURE, chat_reply("OK"))
    adapter = AzureOpenAIAdapter(
        http,
        capability="chat",
        base_url=AZURE,
        deployment="gpt dep",
        api_version="2024-10-21",
        api_key="azure-key-123456",
    )
    assert adapter.chat([{"role": "user", "content": "Hi"}]).text == "OK"
    request = router.requests[0]
    assert str(request.url) == (
        f"{AZURE}/openai/deployments/gpt%20dep/chat/completions?api-version=2024-10-21"
    )
    assert request.headers["api-key"] == "azure-key-123456"
    assert "authorization" not in request.headers
    assert "model" not in json.loads(request.content)
    assert adapter.list_models() == []


def test_anthropic_messages(http: OutboundHttp, router: Router) -> None:
    reply = {
        "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "O"},
            {"type": "text", "text": "K"},
        ],
        "usage": {"input_tokens": 9, "output_tokens": 2},
    }
    router.json("POST", "https://api.anthropic.com/v1/messages", reply)
    adapter = AnthropicAdapter(
        http,
        capability="chat",
        base_url="https://api.anthropic.com",
        model="claude-x",
        api_key="ak-123456789012",
    )
    result = adapter.chat(
        [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}],
        temperature=0,
        max_tokens=256,
        json_schema={"type": "object"},
        seed=42,  # the Messages API has no seed: ignored
    )
    assert (result.text, result.reasoning, result.usage.prompt_tokens) == ("OK", "hmm", 9)
    request = router.requests[0]
    assert request.headers["x-api-key"] == "ak-123456789012"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert body["system"].startswith("Be brief.")
    assert "JSON Schema" in body["system"]
    assert body["max_tokens"] == 256
    assert "seed" not in body


def test_anthropic_models_and_bad_shape(http: OutboundHttp, router: Router) -> None:
    router.json(
        "GET", "https://api.anthropic.com/v1/models", {"data": [{"id": "c2"}, {"id": "c1"}]}
    )
    router.json("POST", "https://api.anthropic.com/v1/messages", {"oops": 1})
    adapter = AnthropicAdapter(
        http, capability="chat", base_url="https://api.anthropic.com", model="m", api_key="k"
    )
    assert adapter.list_models() == ["c1", "c2"]
    with pytest.raises(AdapterError, match="Anthropic format"):
        adapter.chat([{"role": "user", "content": "Hi"}])


def test_searxng(http: OutboundHttp, router: Router) -> None:
    data = {
        "results": [
            {"title": "<b>Trust</b>book", "url": "https://a", "content": "One &amp; two"},
            {"title": "", "url": "https://b"},
            {"title": "no url"},
            {"title": "C", "url": "https://c"},
        ]
    }
    router.json("GET", "http://searxng:8080/search", data)
    results = SearxngAdapter(http, base_url="http://searxng:8080/").search("trustbook", 2)
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("Trustbook", "https://a", "One & two"),
        ("https://b", "https://b", ""),
    ]
    assert router.requests[0].url.params["format"] == "json"
    assert router.requests[0].url.params["q"] == "trustbook"
    assert router.requests[0].url.params["language"] == "en"


@pytest.mark.parametrize(
    ("url", "bare"),
    [
        ("https://www.microsoft.com", True),
        ("https://www.microsoft.com/", True),
        ("https://www.microsoft.com/sv-se/", True),
        ("https://www.microsoft.com/en-us", True),
        ("https://example.org/en/", True),
        ("https://example.org/de/index.html", True),
        ("https://example.org/?q=1", True),
        ("https://requests.readthedocs.io/en/latest/user/advanced/", False),
        ("https://example.org/docs", False),
        ("https://example.org/en-us/docs/x", False),
        ("https://example.org/blog/post.html", False),
    ],
)
def test_is_homepage(url: str, bare: bool) -> None:
    assert is_homepage(url) is bare


def test_homepages_demoted_only_for_specific_queries() -> None:
    results = [
        SearchResult(title="Home", url="https://www.microsoft.com/sv-se/", snippet=""),
        SearchResult(title="A", url="https://a.example/docs/a", snippet=""),
        SearchResult(title="Root", url="https://b.example", snippet=""),
        SearchResult(title="C", url="https://c.example/c", snippet=""),
    ]
    assert [r.title for r in demote_homepages("set session base url", results)] == [
        "A",
        "C",
        "Home",
        "Root",
    ]
    assert [r.title for r in demote_homepages("microsoft teams", results)] == [
        "Home",
        "A",
        "Root",
        "C",
    ]


def test_searxng_demotes_homepages_before_the_limit(http: OutboundHttp, router: Router) -> None:
    data = {
        "results": [
            {"title": "Home", "url": "https://www.microsoft.com/sv-se/"},
            {"title": "Docs", "url": "https://learn.microsoft.com/en-us/graph/overview"},
            {"title": "Root", "url": "https://example.com/"},
            {"title": "Guide", "url": "https://example.com/guide"},
        ]
    }
    router.json("GET", "http://searxng:8080/search", data)
    adapter = SearxngAdapter(http, base_url="http://searxng:8080")
    results = adapter.search("microsoft graph api overview", 2)
    assert [r.title for r in results] == ["Docs", "Guide"]


def test_brave_and_tavily_demote_homepages(http: OutboundHttp, router: Router) -> None:
    items = [
        {"title": "Home", "url": "https://t.example/", "content": "", "description": ""},
        {"title": "Page", "url": "https://t.example/page", "content": "", "description": ""},
    ]
    router.json("GET", BRAVE_URL, {"web": {"results": items}})
    router.json("POST", TAVILY_URL, {"results": items})
    brave = BraveAdapter(http, api_key="brave-key-123456").search("three word query", 5)
    tavily = TavilyAdapter(http, api_key="tvly-secret-123456").search("three word query", 5)
    assert [r.title for r in brave] == [r.title for r in tavily] == ["Page", "Home"]


def test_searxng_json_disabled(http: OutboundHttp, router: Router) -> None:
    router.add("GET", "http://searxng:8080/", lambda _r: httpx.Response(403, text="Forbidden"))
    with pytest.raises(AdapterError, match="json format"):
        SearxngAdapter(http, base_url="http://searxng:8080").search("x")


def test_searxng_bad_shape(http: OutboundHttp, router: Router) -> None:
    router.json("GET", "http://searxng:8080/", {"results": "x"})
    with pytest.raises(AdapterError, match="result list"):
        SearxngAdapter(http, base_url="http://searxng:8080").search("x")


def test_brave(http: OutboundHttp, router: Router) -> None:
    router.json(
        "GET",
        BRAVE_URL,
        {
            "web": {
                "results": [{"title": "T", "url": "https://t", "description": "<strong>d</strong>"}]
            }
        },
    )
    results = BraveAdapter(http, api_key="brave-key-123456").search("q", 50)
    assert results[0].snippet == "d"
    request = router.requests[0]
    assert request.headers["x-subscription-token"] == "brave-key-123456"
    assert request.url.params["count"] == "20"


def test_tavily_key_only_in_header(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.json(
        "POST", TAVILY_URL, {"results": [{"title": "T", "url": "https://t", "content": "c"}]}
    )
    results = TavilyAdapter(http, api_key="tvly-secret-123456").search("q", 3)
    assert results[0].snippet == "c"
    request = router.requests[0]
    assert request.headers["authorization"] == "Bearer tvly-secret-123456"
    assert b"tvly-secret" not in request.content
    assert json.loads(request.content) == {"query": "q", "max_results": 3, "search_depth": "basic"}
    assert "tvly-secret" not in request_log.recent()[0].excerpt


def test_stt_multipart(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.json(
        "POST", "http://stt.local/v1/audio/transcriptions", {"text": " hello ", "duration": 1}
    )
    adapter = OpenAITranscriptionAdapter(
        http, capability="stt", base_url="http://stt.local/v1", model="whisper-1", api_key="k" * 20
    )
    result = adapter.transcribe(b"\0" * 32 * 1024, "a.wav")
    assert (result.text, result.duration_s) == ("hello", 1.0)
    request = router.requests[0]
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="model"' in request.content
    assert b"whisper-1" in request.content
    assert request_log.recent()[0].excerpt == "audio file, 32 KB"


def test_stt_bad_reply_and_models(http: OutboundHttp, router: Router) -> None:
    router.json("POST", "http://stt.local/v1/audio/transcriptions", {"nope": 1})
    router.json("GET", "http://stt.local/v1/models", {"data": [{"id": "whisper-1"}]})
    adapter = OpenAITranscriptionAdapter(
        http, capability="stt", base_url="http://stt.local/v1", model="whisper-1"
    )
    with pytest.raises(AdapterError, match="transcript"):
        adapter.transcribe(b"x")
    assert adapter.list_models() == ["whisper-1"]


def test_mock_adapter_is_deterministic() -> None:
    mock = MockChatAdapter()
    assert mock.chat([{"role": "user", "content": "Reply with OK"}]).text == "OK"
    a = mock.chat([{"role": "user", "content": "What is up?"}]).text
    assert a == mock.chat([{"role": "user", "content": "What is up?"}]).text
    assert a.startswith("Mock answer ")
    as_json = json.loads(mock.chat([{"role": "user", "content": "x"}], json_schema={}).text)
    assert as_json["mock"] is True
    assert mock.list_models() == ["mock"]


def test_factory(http: OutboundHttp) -> None:
    def endpoint(adapter: str, **kw: str | None) -> EndpointConfig:
        values: dict[str, str | None] = {"base_url": "http://x/v1", "model": "m"}
        values.update(kw)
        return EndpointConfig(capability="chat", adapter=adapter, **values)  # type: ignore[arg-type]

    assert isinstance(build_chat_adapter(endpoint("mock", base_url=None), http), MockChatAdapter)
    assert isinstance(
        build_chat_adapter(endpoint("openai_compatible"), http), OpenAICompatibleAdapter
    )
    assert isinstance(build_chat_adapter(endpoint("anthropic"), http), AnthropicAdapter)
    assert isinstance(
        build_chat_adapter(endpoint("azure_openai", api_version="v"), http), AzureOpenAIAdapter
    )
    with pytest.raises(AdapterError, match="API version"):
        build_chat_adapter(endpoint("azure_openai"), http)
    with pytest.raises(AdapterError, match="model name"):
        build_chat_adapter(endpoint("openai_compatible", model=None), http)
    assert isinstance(
        build_stt_adapter(endpoint("openai_compatible"), http), OpenAITranscriptionAdapter
    )
    assert isinstance(
        build_search_adapter("searxng", None, http, searxng_url="http://s"), SearxngAdapter
    )
    assert isinstance(
        build_search_adapter("brave", "k", http, searxng_url="http://s"), BraveAdapter
    )
    assert isinstance(
        build_search_adapter("tavily", "k", http, searxng_url="http://s"), TavilyAdapter
    )
    with pytest.raises(AdapterError, match="Tavily API key"):
        build_search_adapter("tavily", None, http, searxng_url="http://s")


def test_clean_snippet() -> None:
    assert clean_snippet("<em>a</em>  &lt;b&gt;\n c") == "a <b> c"
    assert clean_snippet(None) == ""
