"""OpenAI-compatible chat adapter (OpenAI, Ollama, llama-server, vLLM, LM Studio, …)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

import httpx

from app.capabilities.adapters.base import (
    AdapterError,
    ChatMessage,
    ChatResult,
    Usage,
    as_int,
    parse_json,
    send,
)
from app.capabilities.http import OutboundHttp

JsonMode = Literal["json_schema", "json_object", "prompt"]
_JSON_MODES: tuple[JsonMode, ...] = ("json_schema", "json_object", "prompt")


def schema_instruction(schema: dict[str, Any]) -> str:
    """System instruction used when the endpoint cannot enforce a JSON schema itself."""
    return (
        "Reply with only one JSON object, no other text. It must match this JSON Schema:\n"
        + json.dumps(schema, separators=(",", ":"))
    )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return "".join(str(p) for p in parts)
    return ""


class OpenAICompatibleAdapter:
    """``POST {base}/chat/completions`` and ``GET {base}/models``."""

    def __init__(
        self,
        http: OutboundHttp,
        *,
        capability: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._http = http
        self._capability = capability
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key or None
        self._timeout = timeout
        self._json_mode: JsonMode = "json_schema"

    # --- endpoint specifics (overridden by Azure) ------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _chat_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _base_body(self) -> dict[str, Any]:
        return {"model": self._model}

    # --- public interface ------------------------------------------------------------------

    @property
    def json_mode(self) -> JsonMode:
        """How structured output is requested; downgrades when the endpoint refuses."""
        return self._json_mode

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Send a chat completion.

        With ``json_schema`` the structured-output mode falls back from ``json_schema`` to
        ``json_object`` to a prompt-only instruction when the endpoint rejects it (400).
        """
        if json_schema is None:
            return self._complete(self._body(messages, temperature, max_tokens))
        start = _JSON_MODES.index(self._json_mode)
        last_error: AdapterError | None = None
        for mode in _JSON_MODES[start:]:
            body = self._json_body(messages, temperature, max_tokens, json_schema, mode)
            try:
                result = self._complete(body)
            except AdapterError as exc:
                if exc.kind != "bad_request" or mode == "prompt":
                    raise
                last_error = exc
                continue
            self._json_mode = mode
            return result
        raise last_error or AdapterError("bad_request", "The endpoint rejected JSON output.")

    def list_models(self) -> list[str]:
        """Model ids from ``GET {base}/models``."""
        request = httpx.Request("GET", f"{self._base_url}/models", headers=self._headers())
        with self._http.client(self._capability, timeout=30) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response)
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise AdapterError("bad_response", "The endpoint did not return a model list.")
        ids = {str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")}
        return sorted(ids)

    # --- internals -------------------------------------------------------------------------

    def _body(
        self, messages: Sequence[ChatMessage], temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        body = self._base_body()
        body.update(
            {
                "messages": [dict(m) for m in messages],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
        )
        return body

    def _json_body(
        self,
        messages: Sequence[ChatMessage],
        temperature: float,
        max_tokens: int,
        schema: dict[str, Any],
        mode: JsonMode,
    ) -> dict[str, Any]:
        if mode == "json_schema":
            body = self._body(messages, temperature, max_tokens)
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
            return body
        instructed: list[ChatMessage] = [
            {"role": "system", "content": schema_instruction(schema)},
            *messages,
        ]
        body = self._body(instructed, temperature, max_tokens)
        if mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def _complete(self, body: dict[str, Any]) -> ChatResult:
        """POST the body, retrying once per known parameter incompatibility."""
        for _ in range(3):
            try:
                return self._post(body)
            except AdapterError as exc:
                adjusted = _compat_adjust(body, exc)
                if adjusted is None:
                    raise
                body = adjusted
        return self._post(body)

    def _post(self, body: dict[str, Any]) -> ChatResult:
        request = httpx.Request("POST", self._chat_url(), headers=self._headers(), json=body)
        with self._http.client(self._capability, timeout=self._timeout) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response, what="chat endpoint")
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterError(
                "bad_response",
                "The endpoint replied, but not in the OpenAI chat format.",
            ) from exc
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = as_int(usage.get("prompt_tokens"))
        self._http.record_tokens(response, prompt_tokens)
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        return ChatResult(
            text=_message_text(message.get("content")),
            usage=Usage(prompt_tokens, as_int(usage.get("completion_tokens"))),
            reasoning=str(reasoning) if reasoning else None,
        )


def _compat_adjust(body: dict[str, Any], exc: AdapterError) -> dict[str, Any] | None:
    """Adjust the body for endpoints that reject a parameter (e.g. newer OpenAI models)."""
    if exc.kind != "bad_request":
        return None
    reason = exc.message.lower()
    if "max_completion_tokens" in reason and "max_tokens" in body:
        adjusted = dict(body)
        adjusted["max_completion_tokens"] = adjusted.pop("max_tokens")
        return adjusted
    if "temperature" in reason and "temperature" in body:
        adjusted = dict(body)
        del adjusted["temperature"]
        return adjusted
    return None
