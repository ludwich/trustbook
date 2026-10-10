from __future__ import annotations

import json

import httpx
import pytest

from app.capabilities.adapters.base import AdapterError, raise_for_status, send
from app.capabilities.adapters.openai_compatible import OpenAICompatibleAdapter
from app.capabilities.http import OutboundHttp, RequestLogRepository
from tests.fakes import Router, chat_reply

BASE = "http://llm.local:8080/v1"
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def _adapter(http: OutboundHttp, key: str | None = None) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        http, capability="chat", base_url=BASE + "/", model="m1", api_key=key
    )


def test_chat_request_and_result(
    http: OutboundHttp, router: Router, request_log: RequestLogRepository
) -> None:
    router.json("POST", f"{BASE}/chat/completions", chat_reply("Hello"))
    result = _adapter(http, "sk-test-0000a1b2").chat(
        [{"role": "user", "content": "Hi"}], temperature=0, max_tokens=256
    )
    assert result.text == "Hello"
    assert result.usage.prompt_tokens == 11
    request = router.requests[0]
    assert request.headers["authorization"] == "Bearer sk-test-0000a1b2"
    body = json.loads(request.content)
    assert body == {
        "model": "m1",
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0,
        "max_tokens": 256,
        "stream": False,
    }
    assert request_log.recent()[0].tokens_sent == 11


def test_no_auth_header_without_key(http: OutboundHttp, router: Router) -> None:
    router.json("POST", BASE, chat_reply())
    _adapter(http).chat([{"role": "user", "content": "Hi"}])
    assert "authorization" not in router.requests[0].headers


def test_reasoning_content(http: OutboundHttp, router: Router) -> None:
    router.json("POST", BASE, chat_reply(None, reasoning_content="thinking..."))  # type: ignore[arg-type]
    result = _adapter(http).chat([{"role": "user", "content": "Hi"}])
    assert result.text == ""
    assert result.reasoning == "thinking..."


def test_list_content_parts(http: OutboundHttp, router: Router) -> None:
    data = chat_reply()
    data["choices"][0]["message"]["content"] = [{"type": "text", "text": "A"}, {"text": "B"}]
    router.json("POST", BASE, data)
    assert _adapter(http).chat([{"role": "user", "content": "Hi"}]).text == "AB"


def test_json_schema_supported(http: OutboundHttp, router: Router) -> None:
    router.json("POST", BASE, chat_reply('{"a": "x"}'))
    adapter = _adapter(http)
    result = adapter.chat([{"role": "user", "content": "Hi"}], json_schema=SCHEMA)
    assert result.text == '{"a": "x"}'
    body = router.last_json()
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert adapter.json_mode == "json_schema"


def test_json_schema_falls_back_to_json_object(http: OutboundHttp, router: Router) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "json_schema not supported"}})
        return httpx.Response(200, json=chat_reply('{"a": "y"}'))

    router.add("POST", BASE, handler)
    adapter = _adapter(http)
    result = adapter.chat([{"role": "user", "content": "Hi"}], json_schema=SCHEMA)
    assert result.text == '{"a": "y"}'
    body = router.last_json()
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "JSON Schema" in body["messages"][0]["content"]
    assert adapter.json_mode == "json_object"
    # The downgrade is remembered: the next call goes straight to json_object.
    adapter.chat([{"role": "user", "content": "Again"}], json_schema=SCHEMA)
    assert len(router.requests) == 3


def test_json_falls_back_to_prompt(http: OutboundHttp, router: Router) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "response_format" in json.loads(request.content):
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(200, json=chat_reply("{}"))

    router.add("POST", BASE, handler)
    adapter = _adapter(http)
    adapter.chat([{"role": "user", "content": "Hi"}], json_schema=SCHEMA)
    assert adapter.json_mode == "prompt"
    assert "response_format" not in router.last_json()


def test_json_no_fallback_on_auth_error(http: OutboundHttp, router: Router) -> None:
    router.json("POST", BASE, {"error": "nope"}, status=401)
    with pytest.raises(AdapterError) as info:
        _adapter(http).chat([{"role": "user", "content": "Hi"}], json_schema=SCHEMA)
    assert info.value.kind == "auth"
    assert len(router.requests) == 1


def test_max_completion_tokens_compat(http: OutboundHttp, router: Router) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "max_tokens" in body:
            return httpx.Response(
                400,
                json={"error": {"message": "Use 'max_completion_tokens' instead of max_tokens."}},
            )
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported temperature"}})
        return httpx.Response(200, json=chat_reply("OK"))

    router.add("POST", BASE, handler)
    assert _adapter(http).chat([{"role": "user", "content": "Hi"}], temperature=0).text == "OK"
    body = router.last_json()
    assert body["max_completion_tokens"] == 1024
    assert "temperature" not in body


def test_list_models(http: OutboundHttp, router: Router) -> None:
    router.json("GET", f"{BASE}/models", {"data": [{"id": "b"}, {"id": "a"}, {"x": 1}]})
    assert _adapter(http).list_models() == ["a", "b"]


def test_list_models_bad_shape(http: OutboundHttp, router: Router) -> None:
    router.json("GET", f"{BASE}/models", {"data": "nope"})
    with pytest.raises(AdapterError, match="model list"):
        _adapter(http).list_models()


def test_bad_chat_shape(http: OutboundHttp, router: Router) -> None:
    router.json("POST", BASE, {"unexpected": True})
    with pytest.raises(AdapterError, match="OpenAI chat format"):
        _adapter(http).chat([{"role": "user", "content": "Hi"}])


@pytest.mark.parametrize(
    ("status", "kind", "fragment"),
    [
        (401, "auth", "API key was rejected (401)"),
        (403, "auth", "rejected (403)"),
        (404, "not_found", "Not found (404)"),
        (429, "rate_limited", "429"),
        (500, "server_error", "internal error (500)"),
        (422, "bad_request", "rejected the request (422)"),
        (307, "redirect", "redirected to https://other/"),
    ],
)
def test_status_mapping(status: int, kind: str, fragment: str) -> None:
    response = httpx.Response(
        status,
        json={"error": {"message": "model sk-secret-123 not found"}},
        headers={"location": "https://other/"},
    )
    with pytest.raises(AdapterError) as info:
        raise_for_status(response, secret="sk-secret-123")
    assert info.value.kind == kind
    assert fragment in info.value.message
    assert "sk-secret-123" not in info.value.message


def test_non_json_error_detail() -> None:
    with pytest.raises(AdapterError, match="Bad Gateway"):
        raise_for_status(httpx.Response(502, text="<h1>Bad Gateway</h1>"))


def _send_with(handler: object, timeout: float = 30.0) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    with httpx.Client(transport=transport, timeout=timeout) as client:
        send(client, httpx.Request("GET", "http://host.example:1/x"))


def test_timeout_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(AdapterError, match=r"No reply within 30 s\."):
        _send_with(handler)


def test_connect_error_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    with pytest.raises(AdapterError, match=r"Cannot reach host\.example"):
        _send_with(handler)


def test_certificate_error_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]", request=request)

    with pytest.raises(AdapterError, match="certificate"):
        _send_with(handler)


def test_other_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("bad", request=request)

    with pytest.raises(AdapterError, match=r"Connection to host\.example failed"):
        _send_with(handler)


def test_non_json_success(http: OutboundHttp, router: Router) -> None:
    router.add("POST", BASE, lambda _r: httpx.Response(200, text="<html>"))
    with pytest.raises(AdapterError, match="not with JSON"):
        _adapter(http).chat([{"role": "user", "content": "Hi"}])


def test_think_false_asks_local_servers_to_skip_thinking(
    http: OutboundHttp, router: Router
) -> None:
    router.json("POST", BASE, chat_reply('{"a": "x"}'))
    _adapter(http).chat([{"role": "user", "content": "Hi"}], json_schema=SCHEMA, think=False)
    assert router.last_json()["chat_template_kwargs"] == {"enable_thinking": False}
    _adapter(http).chat([{"role": "user", "content": "Hi"}])
    assert "chat_template_kwargs" not in router.last_json()


def test_endpoints_rejecting_the_thinking_hint_get_it_again_without(
    http: OutboundHttp, router: Router
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "chat_template_kwargs" in json.loads(request.content):
            return httpx.Response(
                400, json={"error": {"message": "Unrecognized request argument supplied"}}
            )
        return httpx.Response(200, json=chat_reply('{"a": "x"}'))

    router.add("POST", BASE, handler)
    adapter = _adapter(http)
    reply = adapter.chat([{"role": "user", "content": "Hi"}], json_schema=SCHEMA, think=False)
    assert reply.text == '{"a": "x"}'
    assert adapter.json_mode == "json_schema"  # the hint was dropped, not the JSON schema
