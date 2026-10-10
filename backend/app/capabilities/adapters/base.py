"""Shared adapter types, interfaces and plain-English error mapping."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict

import httpx

ErrorKind = Literal[
    "unreachable",
    "timeout",
    "auth",
    "not_found",
    "rate_limited",
    "bad_request",
    "server_error",
    "redirect",
    "bad_response",
    "not_configured",
]


class AdapterError(Exception):
    """An endpoint call failed. ``message`` is plain English and safe to show (no secrets)."""

    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ChatResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    reasoning: str | None = None


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    duration_s: float | None = None


class ChatAdapter(Protocol):
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
        think: bool = True,
    ) -> ChatResult: ...

    def list_models(self) -> list[str]: ...


class SearchAdapter(Protocol):
    def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...


class TranscriptionAdapter(Protocol):
    def transcribe(self, audio: bytes, filename: str = "audio.wav") -> TranscriptionResult: ...

    def list_models(self) -> list[str]: ...


def _short_detail(response: httpx.Response, secret: str | None) -> str:
    """A short, secret-free reason extracted from an error body."""
    text = ""
    try:
        data: Any = json.loads(response.content)
        if isinstance(data, dict):
            err = data.get("error", data.get("detail", data.get("message")))
            if isinstance(err, dict):
                err = err.get("message") or err.get("type")
            if err is not None:
                text = str(err)
    except ValueError:
        text = response.content[:300].decode("utf-8", "replace")
    text = " ".join(text.split())
    if secret:
        text = text.replace(secret, "[key]")
    return text[:200]


def raise_for_status(response: httpx.Response, *, secret: str | None = None) -> None:
    """Raise :class:`AdapterError` with a plain-English message for non-2xx responses."""
    status = response.status_code
    if 200 <= status < 300:
        return
    detail = _short_detail(response, secret)
    suffix = f": {detail}" if detail else "."
    if 300 <= status < 400:
        target = response.headers.get("location", "another address")
        raise AdapterError(
            "redirect",
            f"The endpoint redirected to {target}. Use that address instead.",
            status=status,
        )
    if status in (401, 403):
        raise AdapterError("auth", f"The API key was rejected ({status}).", status=status)
    if status == 404:
        raise AdapterError(
            "not_found",
            f"Not found (404). Check the address and the model or deployment name{suffix}",
            status=status,
        )
    if status == 429:
        raise AdapterError(
            "rate_limited",
            "The endpoint is limiting requests (429). Wait a minute and try again.",
            status=status,
        )
    if status >= 500:
        raise AdapterError(
            "server_error", f"The endpoint had an internal error ({status}){suffix}", status=status
        )
    raise AdapterError(
        "bad_request", f"The endpoint rejected the request ({status}){suffix}", status=status
    )


def send(
    client: httpx.Client, request: httpx.Request, *, secret: str | None = None
) -> httpx.Response:
    """Send ``request``; turn transport problems and HTTP errors into :class:`AdapterError`."""
    host = request.url.host
    try:
        response = client.send(request)
    except httpx.TimeoutException as exc:
        timeout = client.timeout.read or client.timeout.connect
        seconds = f" within {int(timeout)} s" if timeout else ""
        raise AdapterError("timeout", f"No reply{seconds}.") from exc
    except httpx.ConnectError as exc:
        reason = str(exc).lower()
        if "certificate" in reason or "ssl" in reason:
            raise AdapterError(
                "unreachable", f"Secure connection to {host} failed (certificate problem)."
            ) from exc
        raise AdapterError(
            "unreachable",
            f"Cannot reach {host}. Check the address and that the server is running.",
        ) from exc
    except httpx.TransportError as exc:
        raise AdapterError("unreachable", f"Connection to {host} failed.") from exc
    raise_for_status(response, secret=secret)
    return response


def parse_json(response: httpx.Response, *, what: str = "endpoint") -> Any:
    """Parse JSON or raise a plain-English :class:`AdapterError`."""
    try:
        return json.loads(response.content)
    except ValueError as exc:
        raise AdapterError(
            "bad_response",
            f"The {what} replied, but not with JSON. Check that the address is correct.",
        ) from exc


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
