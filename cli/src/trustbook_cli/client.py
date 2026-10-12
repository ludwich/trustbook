"""HTTP client for the Trustbook API: requests, errors and Server-Sent Events.

Shared by the ``trustbook`` CLI and the eval runner (``eval/runner.py``, D-086). Only the public
HTTP API is used (docs/ARCHITECTURE.md), so nothing here can bypass grounding or refusal.
"""

from __future__ import annotations

import codecs
import html
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8484"
# An answer can spend minutes checking sentences without sending an event (slow endpoints).
STREAM_READ_TIMEOUT_S = 900.0
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class TrustbookError(Exception):
    """Invalid input or an unusable API answer.

    ``created``: sources a ``sources add`` with several items had already added when it failed
    (reported in the error document so they are not added again); None otherwise.
    """

    created: list[dict[str, Any]] | None = None


class ApiError(TrustbookError):
    """An API call failed: an HTTP error status, or a response that is not JSON.

    ``http_status`` is the response's status code (None: no response, or a 2xx that was unusable).
    """

    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class ApiUnreachableError(ApiError):
    """The Trustbook API cannot be reached."""


# ---------------------------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: str


_EOL = re.compile(r"\r\n|\r|\n")


def sse_lines(chunks: Iterable[bytes]) -> Iterator[str]:
    """Split a UTF-8 byte stream into lines at CRLF, LF or CR only: the SSE line endings.

    httpx's ``iter_lines`` uses ``str.splitlines``, which also breaks at U+2028, U+2029, U+0085,
    form feeds and the like, so an event whose data contains one would be cut in half. Here those
    stay inside their line. A CRLF split across two chunks is one line ending; a leading BOM is
    dropped; invalid UTF-8 becomes U+FFFD. Lines are yielded without their line ending.
    """
    decoder = codecs.getincrementaldecoder("utf-8-sig")(errors="replace")
    buffer = ""
    for chunk in chunks:
        buffer += decoder.decode(chunk)
        start = 0
        for match in _EOL.finditer(buffer):
            # A CR at the end may be the first half of a CRLF: wait for the next chunk.
            if match.group() == "\r" and match.end() == len(buffer):
                break
            yield buffer[start : match.start()]
            start = match.end()
        buffer = buffer[start:]
    buffer += decoder.decode(b"", final=True)
    lines = _EOL.split(buffer)
    if lines and lines[-1] == "":
        lines.pop()
    yield from lines


def iter_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Parse a ``text/event-stream`` (WHATWG rules, minus ``id``/``retry`` which are unused).

    ``lines``: from :func:`sse_lines` (not httpx's ``iter_lines``, which splits at more than the
    SSE line endings).
    Data lines are joined with ``\\n``; comment lines (``:``) are ignored; an event without a
    blank line before the end of the stream is still delivered.
    """
    event = ""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield SseEvent(event or "message", "\n".join(data))
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield SseEvent(event or "message", "\n".join(data))


# ---------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------


_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def norm_text(value: str) -> str:
    """Case-, whitespace-, entity- and dash-insensitive form used for titles and headings."""
    value = unicodedata.normalize("NFKC", html.unescape(value)).translate(_DASHES)
    value = value.replace("¶", "").strip().strip("#").strip()
    return " ".join(value.casefold().split())


def find_space(spaces: Iterable[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Match ``--space`` by id, then slug, then name (case-insensitive)."""
    spaces = list(spaces)
    for field_name in ("id", "slug"):
        for s in spaces:
            if str(s.get(field_name, "")) == key:
                return s
    for s in spaces:
        if norm_text(str(s.get("name", ""))) == norm_text(key):
            return s
    return None


# ---------------------------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------------------------


def error_detail(resp: httpx.Response) -> str:
    """The API's ``detail`` (FastAPI validation lists flattened), or the start of the body."""
    detail = resp.text[:300]
    try:
        body = resp.json()
    except ValueError:
        return detail
    if not isinstance(body, dict) or "detail" not in body:
        return detail
    raw = body["detail"]
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("msg"), str):
                loc = item.get("loc")
                where = ".".join(str(p) for p in loc[1:]) if isinstance(loc, list) else ""
                parts.append(f"{where}: {item['msg']}" if where else item["msg"])
        if parts:
            return "; ".join(parts)
    return str(raw)


def read_json(resp: httpx.Response, what: str, *, empty_ok: bool = False) -> Any:
    """The response's JSON body; :class:`ApiError` on an error status or a body that isn't JSON.

    ``empty_ok``: a ``204 No Content`` (or an empty body) gives None instead of an error.
    """
    if resp.status_code >= 400:
        raise ApiError(
            f"{what} failed: HTTP {resp.status_code}: {error_detail(resp)}", resp.status_code
        )
    if empty_ok and (resp.status_code == 204 or not resp.content):
        return None
    try:
        return resp.json()
    except ValueError as exc:
        raise ApiError(f"{what}: response is not JSON") from exc


def request_json(
    client: httpx.Client, method: str, url: str, what: str, *, empty_ok: bool = False, **kwargs: Any
) -> Any:
    """Send one request and return its JSON body (see :func:`read_json`).

    :raises ApiUnreachableError: no connection (refused, DNS, timeout).
    :raises ApiError: an error status or an unusable body.
    """
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(f"{what}: {type(exc).__name__}: {exc}") from exc
    return read_json(resp, what, empty_ok=empty_ok)


class TrustbookClient:
    """The routes the CLI uses, on one ``httpx.Client`` (relative ``/api/...`` paths).

    ``client`` is used as given (tests pass FastAPI's ``TestClient``); otherwise a client for
    ``base_url`` is made and closed by :meth:`close`. Proxy environment variables are ignored:
    Trustbook runs on this computer or the local network.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self._owned = client is None
        self.http = client or httpx.Client(
            base_url=self.base_url, timeout=DEFAULT_TIMEOUT, trust_env=False
        )

    def close(self) -> None:
        if self._owned:
            self.http.close()

    def request(self, method: str, path: str, what: str, **kwargs: Any) -> Any:
        return request_json(self.http, method, path, what, **kwargs)

    # --- spaces --------------------------------------------------------------------------------

    def spaces(self) -> list[dict[str, Any]]:
        data = self.request("GET", "/api/spaces", "list spaces")
        if not isinstance(data, list):
            raise ApiError("list spaces: expected a list")
        return [s for s in data if isinstance(s, dict)]

    def space(self, key: str) -> dict[str, Any]:
        """The space with this id, slug or name; :class:`TrustbookError` if there is none."""
        found = find_space(self.spaces(), key)
        if found is None:
            raise TrustbookError(f"No space named {key!r}. See `trustbook spaces list`.")
        return found

    def create_space(self, name: str, description: str = "") -> dict[str, Any]:
        body = {"name": name, "description": description}
        return self._object(
            self.request("POST", "/api/spaces", "create space", json=body), "create space"
        )

    def delete_space(self, space_id: str) -> None:
        self.request("DELETE", f"/api/spaces/{space_id}", "delete space", empty_ok=True)

    # --- sources -------------------------------------------------------------------------------

    def sources(self, space_id: str) -> list[dict[str, Any]]:
        data = self.request("GET", f"/api/spaces/{space_id}/sources", "list sources")
        if not isinstance(data, list):
            raise ApiError("list sources: expected a list")
        return [s for s in data if isinstance(s, dict)]

    def source(self, space_id: str, source_id: str) -> dict[str, Any]:
        url = f"/api/spaces/{space_id}/sources/{source_id}"
        return self._object(self.request("GET", url, "read source"), "read source")

    def add_folder(self, space_id: str, path: str) -> dict[str, Any]:
        url = f"/api/spaces/{space_id}/sources/folder"
        what = f"add folder {path}"
        return self._object(self.request("POST", url, what, json={"path": path}), what)

    def add_url(self, space_id: str, url: str) -> dict[str, Any]:
        what = f"add web page {url}"
        route = f"/api/spaces/{space_id}/sources/url"
        return self._object(self.request("POST", route, what, json={"url": url}), what)

    def add_paste(self, space_id: str, text: str, title: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text}
        if title:
            body["title"] = title
        url = f"/api/spaces/{space_id}/sources/paste"
        return self._object(self.request("POST", url, "add text", json=body), "add text")

    def upload(self, space_id: str, files: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
        parts = [("files", (name, content)) for name, content in files]
        url = f"/api/spaces/{space_id}/sources/upload"
        return self._list(self.request("POST", url, "upload files", files=parts), "upload files")

    def web_search(self, space_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        url = f"/api/spaces/{space_id}/web-search"
        data = self.request("POST", url, "web search", json={"query": query, "limit": limit})
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ApiError("web search: unexpected response")
        return [r for r in results if isinstance(r, dict)]

    def add_from_search(self, space_id: str, items: list[dict[str, str]]) -> list[dict[str, Any]]:
        url = f"/api/spaces/{space_id}/sources/from-search"
        what = "add search results"
        return self._list(self.request("POST", url, what, json={"items": items}), what)

    def refresh_source(self, space_id: str, source_id: str) -> dict[str, Any]:
        url = f"/api/spaces/{space_id}/sources/{source_id}/refresh"
        return self._object(self.request("POST", url, "refresh source"), "refresh source")

    def delete_source(self, space_id: str, source_id: str) -> None:
        url = f"/api/spaces/{space_id}/sources/{source_id}"
        self.request("DELETE", url, "remove source", empty_ok=True)

    def chunk_context(self, space_id: str, chunk_id: str) -> dict[str, Any]:
        url = f"/api/spaces/{space_id}/chunks/{chunk_id}/context"
        return self._object(self.request("GET", url, "read cited passage"), "read cited passage")

    # --- chat, jobs, gate log ------------------------------------------------------------------

    def create_conversation(self, space_id: str, title: str) -> str:
        url = f"/api/spaces/{space_id}/conversations"
        data = self._object(
            self.request("POST", url, "create conversation", json={"title": title}),
            "create conversation",
        )
        if "id" not in data:
            raise ApiError("create conversation: unexpected response")
        return str(data["id"])

    def jobs(self) -> dict[str, Any]:
        return self._object(self.request("GET", "/api/jobs", "list jobs"), "list jobs")

    def gate_log(self, space_id: str, limit: int) -> list[dict[str, Any]]:
        url = f"/api/spaces/{space_id}/gate-log"
        what = "read gate log"
        return self._list(self.request("GET", url, what, params={"limit": limit}), what)

    @staticmethod
    def _object(data: Any, what: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ApiError(f"{what}: unexpected response")
        return data

    @staticmethod
    def _list(data: Any, what: str) -> list[dict[str, Any]]:
        if not isinstance(data, list):
            raise ApiError(f"{what}: unexpected response")
        return [item for item in data if isinstance(item, dict)]
