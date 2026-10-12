"""The shared API client: SSE parsing, errors and requests (no network: MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from trustbook_cli.client import (
    ApiError,
    ApiUnreachableError,
    TrustbookClient,
    TrustbookError,
    error_detail,
    find_space,
    iter_sse,
    norm_text,
    request_json,
)


def test_iter_sse_multiline_comments_crlf_and_unterminated() -> None:
    lines = [": hi", "event: sentence\r\n", "data: a", "data:b", "", "data: last"]
    events = list(iter_sse(lines))
    assert [(e.event, e.data) for e in events] == [("sentence", "a\nb"), ("message", "last")]


def test_norm_text_and_find_space() -> None:
    assert norm_text("  Quickstart \u2014 Requests  ") == "quickstart - requests"
    spaces = [{"id": "s1", "slug": "smoke", "name": "Smoke Test"}, {"id": "s2", "name": "x"}]
    assert find_space(spaces, "s2") == spaces[1]
    assert find_space(spaces, "smoke") == spaces[0]
    assert find_space(spaces, "smoke  TEST") == spaces[0]
    assert find_space(spaces, "nope") is None


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(base_url="http://tb.test", transport=handler)


def test_errors_carry_status_and_flattened_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v":
            body = {"detail": [{"loc": ["body", "name"], "msg": "too short"}, {"msg": "bad"}]}
            return httpx.Response(422, json=body)
        if request.url.path == "/api/text":
            return httpx.Response(500, text="boom")
        if request.url.path == "/api/empty":
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "Space not found."})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(ApiError) as caught:
        request_json(client, "GET", "/api/x", "read x")
    assert str(caught.value) == "read x failed: HTTP 404: Space not found."
    assert caught.value.http_status == 404
    with pytest.raises(ApiError, match="name: too short; bad"):
        request_json(client, "POST", "/api/v", "create")
    assert error_detail(client.get("/api/text")) == "boom"
    assert request_json(client, "DELETE", "/api/empty", "delete", empty_ok=True) is None
    with pytest.raises(ApiError, match="not JSON"):
        request_json(client, "DELETE", "/api/empty", "delete")


def test_unreachable_is_an_api_error_without_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ApiUnreachableError) as caught:
        request_json(_client(httpx.MockTransport(handler)), "GET", "/api/spaces", "list spaces")
    assert caught.value.http_status is None
    assert isinstance(caught.value, ApiError) and isinstance(caught.value, TrustbookError)


def test_unexpected_shapes_are_errors() -> None:
    api = TrustbookClient(
        client=_client(httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    )
    with pytest.raises(ApiError, match="expected a list"):
        api.spaces()
    with pytest.raises(ApiError, match="unexpected response"):
        api.gate_log("s1", 10)
