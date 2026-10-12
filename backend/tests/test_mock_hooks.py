"""Test hooks (only with TRUSTBOOK_MOCK_LLM=1): TB-UNSUPPORTED, the mock code judge, mock search."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.capabilities.adapters.base import AdapterError
from app.capabilities.adapters.factory import build_search_adapter
from app.capabilities.adapters.mock import (
    UNSUPPORTED_SENTENCE,
    MockChatAdapter,
    MockSearchAdapter,
)
from app.capabilities.bundled import ProviderRegistry
from app.capabilities.http import OutboundHttp
from app.capabilities.registry import CapabilityRegistry, ConfigError
from app.capabilities.schemas import CapabilityConfigUpdate as Update
from app.chat.pipeline import Answered, answer
from app.chat.prompt import ANSWER_SCHEMA, answer_messages
from app.config import Paths, SettingsStore
from app.grounding.code_judge import CodeJudge, judge_messages, prepare
from app.grounding.verify import Verifier
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.fakes import FakeGrounding

PROSE = RetrievedChunk(
    "k1", "s1", "Guide", "Proxies are configured with the proxies argument.", {}, 12, 0.9
)
CODE = RetrievedChunk(
    "k2",
    "s1",
    "requests",
    "File: src/requests/adapters.py (lines 10-12)\n"
    "DEFAULT_POOLSIZE = 10\n"
    "DEFAULT_RETRIES = 0\n"
    "DEFAULT_POOL_TIMEOUT = None",
    {"file": "src/requests/adapters.py", "start_line": 10, "end_line": 12},
    20,
    0.9,
)


def test_tb_unsupported_adds_a_failing_sentence_and_the_answer_is_partially_verified() -> None:
    mock = MockChatAdapter()
    question = "How are proxies configured? TB-UNSUPPORTED"
    reply = json.loads(
        mock.chat(answer_messages(question, [PROSE]), json_schema=ANSWER_SCHEMA).text
    )
    assert reply["sentences"][-1] == {"text": UNSUPPORTED_SENTENCE, "chunk_ids": ["c1"]}
    without = json.loads(
        mock.chat(
            answer_messages("How are proxies configured?", [PROSE]), json_schema=ANSWER_SCHEMA
        ).text
    )
    assert UNSUPPORTED_SENTENCE not in [s["text"] for s in without["sentences"]]

    found = Retrieval(chunks=[PROSE], ranked=[PROSE], source_ids=["s1"])
    outcome = answer(
        question,
        retrieve=lambda _q: found,
        chat_adapter=MockChatAdapter,
        refuse_threshold=0.15,
        verifier=Verifier(FakeGrounding().score, threshold=0.5),
    )
    assert isinstance(outcome, Answered)
    assert outcome.badge == "partially_verified"
    assert outcome.removed == [UNSUPPORTED_SENTENCE]


def test_mock_judge_supports_two_shared_words_and_names_the_lines() -> None:
    sentences: list[tuple[str, list[RetrievedChunk]]] = [
        ("DEFAULT_POOLSIZE is 10 and DEFAULT_RETRIES is 0.", [CODE]),
        ("Retries are unlimited.", [CODE]),
    ]
    chunks, statements = prepare(sentences)
    reply = MockChatAdapter().chat(
        judge_messages(list(chunks.values()), statements),
        json_schema={"properties": {"verdicts": {}}},
    )
    data = json.loads(reply.text)
    assert data["verdicts"][0] == {
        "index": 1,
        "supported": True,
        "evidence": [{"chunk": "c1", "start_line": 10, "end_line": 12}],  # the whole cited chunk
    }
    assert data["verdicts"][1]["supported"] is False  # no word shared
    # Through the real acceptance rules: the first passes, the second fails.
    outcomes = CodeJudge(MockChatAdapter)(sentences)
    assert outcomes is not None and [o.accepted for o in outcomes] == [True, False]


def registry_for(paths: Paths) -> CapabilityRegistry:
    return CapabilityRegistry(paths, SettingsStore(paths), ProviderRegistry(paths.models_dir))


def test_mock_search_provider_is_offered_only_in_mock_mode(
    mock_paths: Paths, no_mock_paths: Paths
) -> None:
    mock = registry_for(mock_paths)
    assert "mock" in [o.value for o in mock.get("web_search").providers]
    search = mock.update("web_search", Update(provider="mock"))
    assert (search.status, search.locality) == ("ready", "local")
    assert search.config.provider == "mock"
    real = registry_for(no_mock_paths)
    assert "mock" not in [o.value for o in real.get("web_search").providers]
    field = next(f for f in real.get("web_search").fields if f.name == "provider")
    assert "mock" not in [o.value for o in field.options or []]
    with pytest.raises(ConfigError):
        real.update("web_search", Update(provider="mock"))
    # A setting left over from a test run does not work without mock mode.
    assert real.get("web_search").status == "not_configured"


def test_mock_search_adapter_needs_the_environment_flag(
    monkeypatch: pytest.MonkeyPatch, http: OutboundHttp
) -> None:
    monkeypatch.delenv("TRUSTBOOK_MOCK_LLM", raising=False)
    with pytest.raises(AdapterError):
        build_search_adapter("mock", None, http, searxng_url="http://searxng:8080")
    monkeypatch.setenv("TRUSTBOOK_MOCK_LLM", "1")
    adapter = build_search_adapter("mock", None, http, searxng_url="http://searxng:8080")
    assert isinstance(adapter, MockSearchAdapter)


def test_mock_search_results_demote_homepages_for_specific_queries() -> None:
    adapter = MockSearchAdapter()
    specific = [r.url for r in adapter.search("AB-731 certificate exam", 10)]
    assert specific == [
        "https://docs.example.com/certificates/ab-731",
        "https://learn.example.com/exams/ab-731",
        "https://www.example.com/sv-se/",
        "https://example.org/",
    ]
    assert [r.url for r in adapter.search("example", 2)] == [
        "https://www.example.com/sv-se/",
        "https://example.org/",
    ]


def test_mock_web_search_through_the_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRUSTBOOK_MOCK_LLM", "1")
    put = client.put("/api/capabilities/web_search", json={"provider": "mock"})
    assert put.status_code == 200, put.text
    assert (put.json()["status"], put.json()["config"]["provider"]) == ("ready", "mock")
    tested = client.post("/api/capabilities/web_search/test", json={})
    assert tested.status_code == 200 and tested.json()["ok"] is True, tested.text
    assert client.get("/api/capabilities/web_search").json()["status"] == "ready"
    space: dict[str, Any] = client.post("/api/spaces", json={"name": "Hooks"}).json()
    response = client.post(
        f"/api/spaces/{space['id']}/web-search", json={"query": "AB-731 exam skills"}
    )
    assert response.status_code == 200
    urls = [r["url"] for r in response.json()["results"]]
    assert urls[:2] == [
        "https://docs.example.com/certificates/ab-731",
        "https://learn.example.com/exams/ab-731",
    ]


def test_mock_answers_never_quote_a_code_chunk_header() -> None:
    from app.capabilities.adapters.mock import grounded_answer
    from app.chat.prompt import PromptChunk

    chunk = PromptChunk(
        "c1",
        "pool.py",
        "File: pool.py · make_pool (lines 354-356)\ndef make_pool(size=DEFAULT_POOLSIZE):\n"
        '    """Create a pool. The default pool size is DEFAULT_POOLSIZE, which is 10."""',
    )
    reply = grounded_answer([chunk], "What is the default pool size in make_pool?")
    assert reply["sentences"]
    assert all("354" not in s["text"] and "File:" not in s["text"] for s in reply["sentences"])
