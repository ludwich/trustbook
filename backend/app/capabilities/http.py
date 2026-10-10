"""Shared outbound HTTP client. Every request to a configured endpoint is written to request_log.

Logged per request: timestamp, capability, endpoint host, space (from a context variable),
bytes sent, tokens sent (when known) and an excerpt of the body. Headers are never logged.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx
from sqlalchemy import Engine, delete, func
from sqlmodel import Session, col, select

from app.db.models import RequestLog
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.http")

EXCERPT_CHARS = 500
MAX_LOG_ROWS = 10_000
EXT_EXCERPT = "trustbook.excerpt"
EXT_TOKENS = "trustbook.tokens"
EXT_LOG_ID = "trustbook.log_id"
_SECRET_PARAM_HINTS = ("key", "token", "secret", "sig", "auth", "password")


@dataclass(frozen=True)
class SpaceRef:
    id: str
    name: str | None = None


current_space: ContextVar[SpaceRef | None] = ContextVar("trustbook_current_space", default=None)


@contextlib.contextmanager
def space_context(space_id: str | None, space_name: str | None = None) -> Iterator[None]:
    """Attribute outbound requests made inside this block to a space."""
    token = current_space.set(SpaceRef(space_id, space_name) if space_id else None)
    try:
        yield
    finally:
        current_space.reset(token)


class RequestLogRepository:
    """Reads and writes ``request_log`` in global.db."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._inserts = 0

    def add(self, entry: RequestLog) -> int:
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            row_id = int(entry.id or 0)
        self._inserts += 1
        if self._inserts % 100 == 0:
            self.prune()
        return row_id

    def set_tokens(self, row_id: int, tokens: int) -> None:
        with Session(self._engine) as session:
            row = session.get(RequestLog, row_id)
            if row is not None:
                row.tokens_sent = tokens
                session.add(row)
                session.commit()

    def recent(self, *, limit: int = 100, before_id: int | None = None) -> list[RequestLog]:
        with Session(self._engine) as session:
            query = select(RequestLog)
            if before_id is not None:
                query = query.where(col(RequestLog.id) < before_id)
            query = query.order_by(col(RequestLog.id).desc()).limit(limit)
            return list(session.exec(query).all())

    def clear(self) -> None:
        with Session(self._engine) as session:
            session.exec(delete(RequestLog))
            session.commit()

    def prune(self, keep: int = MAX_LOG_ROWS) -> None:
        """Keep only the newest ``keep`` rows."""
        with Session(self._engine) as session:
            newest = session.exec(select(func.max(RequestLog.id))).one()
            if newest is not None and int(newest) > keep:
                session.exec(delete(RequestLog).where(col(RequestLog.id) <= int(newest) - keep))
                session.commit()


def _redacted_target(url: httpx.URL) -> str:
    """Path and query of ``url`` with any secret-looking query values removed."""
    target = url.path
    if url.query:
        params = parse_qsl(url.query.decode("ascii", "replace"), keep_blank_values=True)
        safe = [
            (k, "[redacted]" if any(h in k.lower() for h in _SECRET_PARAM_HINTS) else v)
            for k, v in params
        ]
        target += "?" + urlencode(safe)
    return target


def describe_size(size: int) -> str:
    """Human-readable size, e.g. ``"32 KB"``."""
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _body_info(request: httpx.Request) -> tuple[int, str]:
    """Return (bytes sent, excerpt) for a request without consuming its stream."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/"):
        size = int(request.headers.get("content-length") or 0)
        return size, f"file upload, {describe_size(size)}"
    try:
        body = request.content
    except httpx.RequestNotRead:
        size = int(request.headers.get("content-length") or 0)
        return size, f"streamed body, {describe_size(size)}"
    if not body:
        return 0, f"{request.method} {_redacted_target(request.url)}"
    return len(body), body[: EXCERPT_CHARS * 4].decode("utf-8", "replace")[:EXCERPT_CHARS]


class OutboundHttp:
    """Factory for logged httpx clients, with an injectable transport for tests."""

    def __init__(
        self,
        log: RequestLogRepository,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._log = log
        self._transport = transport

    def client(self, capability: str, *, timeout: float = 30.0) -> httpx.Client:
        """Return a client whose every request is logged under ``capability``."""
        return httpx.Client(
            transport=self._transport,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            event_hooks={"request": [self._hook(capability)]},
        )

    def _hook(self, capability: str) -> Callable[[httpx.Request], None]:
        def on_request(request: httpx.Request) -> None:
            size, excerpt = _body_info(request)
            override = request.extensions.get(EXT_EXCERPT)
            if isinstance(override, str):
                excerpt = override[:EXCERPT_CHARS]
            tokens = request.extensions.get(EXT_TOKENS)
            space = current_space.get()
            try:
                row_id = self._log.add(
                    RequestLog(
                        timestamp=utc_now_iso(),
                        capability=capability,
                        endpoint_host=request.url.host,
                        space_id=space.id if space else None,
                        space_name=space.name if space else None,
                        bytes_sent=size,
                        tokens_sent=tokens if isinstance(tokens, int) else None,
                        excerpt=excerpt,
                    )
                )
                request.extensions[EXT_LOG_ID] = row_id
            except Exception:
                # Never send data that could not be logged.
                logger.exception("Could not write the request log")
                raise

        return on_request

    def record_tokens(self, response: httpx.Response, tokens: int | None) -> None:
        """Attach the provider-reported prompt token count to the logged request."""
        if tokens is None:
            return
        row_id = response.request.extensions.get(EXT_LOG_ID)
        if isinstance(row_id, int):
            try:
                self._log.set_tokens(row_id, tokens)
            except Exception:
                logger.warning("Could not record token usage", exc_info=True)


def json_body(response: httpx.Response) -> Any:
    """Parse a JSON response body (raises ``ValueError`` when it is not JSON)."""
    return json.loads(response.content)
