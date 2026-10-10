from __future__ import annotations

import httpx

from app.capabilities.http import (
    EXT_EXCERPT,
    EXT_TOKENS,
    OutboundHttp,
    RequestLogRepository,
    describe_size,
    space_context,
)
from app.db.models import RequestLog
from tests.fakes import Router


def _ok(_r: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"usage": {"prompt_tokens": 5}})


def test_post_is_logged_without_headers(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.add("POST", "https://api.example.com/", _ok)
    body = {"messages": [{"role": "user", "content": "x" * 900}]}
    with http.client("chat") as client:
        client.post(
            "https://api.example.com/v1/chat/completions",
            json=body,
            headers={"Authorization": "Bearer sk-supersecret-key-123"},
        )
    [row] = request_log.recent()
    assert row.capability == "chat"
    assert row.endpoint_host == "api.example.com"
    assert row.space_id is None
    assert row.bytes_sent == len(router.requests[0].content)
    assert len(row.excerpt) == 500
    assert row.excerpt.startswith('{"messages"')
    assert "sk-supersecret" not in row.excerpt
    assert "Authorization" not in row.excerpt


def test_get_logs_query_with_secrets_redacted(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.add("GET", "http://searxng:8080/", _ok)
    with http.client("web_search") as client:
        client.get("http://searxng:8080/search", params={"q": "trustbook", "api_key": "zzz"})
    [row] = request_log.recent()
    assert row.bytes_sent == 0
    assert row.excerpt.startswith("GET /search?q=trustbook")
    assert "zzz" not in row.excerpt
    assert "api_key=%5Bredacted%5D" in row.excerpt


def test_multipart_is_described(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.add("POST", "http://stt/", _ok)
    with http.client("stt") as client:
        client.post("http://stt/v1/audio/transcriptions", files={"file": ("a.wav", b"\0" * 40_000)})
    [row] = request_log.recent()
    assert row.excerpt.startswith("file upload, ")
    assert row.bytes_sent > 40_000
    assert "\x00" not in row.excerpt


def test_excerpt_and_tokens_override(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.add("POST", "http://stt/", _ok)
    with http.client("stt") as client:
        request = client.build_request(
            "POST",
            "http://stt/x",
            content=b"abc",
            extensions={EXT_EXCERPT: "audio file, 32 KB", EXT_TOKENS: 7},
        )
        client.send(request)
    [row] = request_log.recent()
    assert row.excerpt == "audio file, 32 KB"
    assert row.tokens_sent == 7


def test_space_context_and_record_tokens(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.add("POST", "http://local/", _ok)
    with space_context("s1", "ACME"), http.client("chat") as client:
        response = client.post("http://local/v1/chat/completions", json={"a": 1})
    http.record_tokens(response, 42)
    http.record_tokens(response, None)
    [row] = request_log.recent()
    assert (row.space_id, row.space_name, row.tokens_sent) == ("s1", "ACME", 42)
    with http.client("chat") as client:
        client.post("http://local/v1/chat/completions", json={"a": 1})
    assert request_log.recent()[0].space_id is None


def test_repository_paging_clear_prune(request_log: RequestLogRepository) -> None:
    for i in range(5):
        request_log.add(
            RequestLog(timestamp="t", capability="chat", endpoint_host=f"h{i}", excerpt="")
        )
    rows = request_log.recent(limit=2)
    assert [r.endpoint_host for r in rows] == ["h4", "h3"]
    older = request_log.recent(limit=10, before_id=rows[-1].id)
    assert [r.endpoint_host for r in older] == ["h2", "h1", "h0"]
    request_log.prune(keep=2)
    assert [r.endpoint_host for r in request_log.recent()] == ["h4", "h3"]
    request_log.set_tokens(9999, 3)  # unknown row is ignored
    request_log.clear()
    assert request_log.recent() == []


def test_describe_size() -> None:
    assert describe_size(10) == "10 bytes"
    assert describe_size(32 * 1024) == "32 KB"
    assert describe_size(3 * 1024 * 1024) == "3.0 MB"
