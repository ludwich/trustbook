"""Anthropic Messages API adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

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
from app.capabilities.adapters.openai_compatible import schema_instruction
from app.capabilities.http import OutboundHttp

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter:
    """``POST {base}/v1/messages`` and ``GET {base}/v1/models``."""

    def __init__(
        self,
        http: OutboundHttp,
        *,
        capability: str,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout: float = 60.0,
    ) -> None:
        self._http = http
        self._capability = capability
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key or None
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
        think: bool = True,
    ) -> ChatResult:
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        if json_schema is not None:
            system_parts.append(schema_instruction(json_schema))
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in messages
                if m["role"] != "system"
            ],
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        request = httpx.Request(
            "POST", f"{self._base_url}/v1/messages", headers=self._headers(), json=body
        )
        with self._http.client(self._capability, timeout=self._timeout) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response, what="Anthropic endpoint")
        blocks = data.get("content") if isinstance(data, dict) else None
        if not isinstance(blocks, list):
            raise AdapterError(
                "bad_response", "The endpoint replied, but not in the Anthropic format."
            )
        text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        thinking = "".join(
            str(b.get("thinking", "")) for b in blocks if b.get("type") == "thinking"
        )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = as_int(usage.get("input_tokens"))
        self._http.record_tokens(response, prompt_tokens)
        return ChatResult(
            text=text,
            usage=Usage(prompt_tokens, as_int(usage.get("output_tokens"))),
            reasoning=thinking or None,
        )

    def list_models(self) -> list[str]:
        request = httpx.Request("GET", f"{self._base_url}/v1/models", headers=self._headers())
        with self._http.client(self._capability, timeout=30) as client:
            response = send(client, request, secret=self._api_key)
        data = parse_json(response)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise AdapterError("bad_response", "The endpoint did not return a model list.")
        return sorted({str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")})
