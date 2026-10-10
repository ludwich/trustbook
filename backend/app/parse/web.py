"""Fetch a public web page and turn it into a snapshot.

This is not an endpoint call (the user's own URL), so it uses a plain httpx client rather than
the logged outbound client: browser-like user agent, 20 s timeout, 15 MB cap, redirects followed
by hand so every hop passes the address check. Trafilatura extracts the text; when it finds fewer
than 200 characters the page is rendered in headless Chromium and extracted again (D-025).
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from app.ingest import errors
from app.ingest.errors import IngestError, fail, http_status_error
from app.parse.documents import DocumentParser
from app.parse.snapshot import Snapshot
from app.parse.text import decode_text, html_to_markdown, page_title, text_snapshot
from app.security.addresses import BlockedAddressError, Resolver, check_url, system_resolver
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.web")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
TIMEOUT_S = 20.0
MAX_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 10
MIN_EXTRACTED_CHARS = 200
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = ("text/plain", "text/markdown", "text/x-markdown")


class Renderer(Protocol):
    """Renders a page in a headless browser and returns its HTML."""

    def render(self, url: str) -> str: ...


@dataclass(frozen=True)
class FetchedPage:
    url: str
    content_type: str
    body: bytes
    fetched_at: str


def blocked(exc: BlockedAddressError) -> IngestError:
    return IngestError(str(exc), "Save the page as PDF and upload it instead.")


class WebFetcher:
    """HTTP fetches with address checks; ``transport``/``resolver``/``renderer`` are seams."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = system_resolver,
        renderer: Renderer | None = None,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self.transport = transport
        self.resolver = resolver
        self.renderer = renderer
        self.max_bytes = max_bytes

    def check(self, url: str) -> None:
        try:
            check_url(url, self.resolver)
        except BlockedAddressError as exc:
            raise blocked(exc) from exc
        except OSError as exc:
            raise fail(errors.UNREACHABLE) from exc

    def fetch(self, url: str) -> FetchedPage:
        """GET ``url`` following redirects (each hop checked); raises :class:`IngestError`."""
        current = url
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        with httpx.Client(
            transport=self.transport,
            timeout=httpx.Timeout(TIMEOUT_S),
            follow_redirects=False,
            headers=headers,
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                self.check(current)
                try:
                    with client.stream("GET", current) as response:
                        if response.is_redirect and "location" in response.headers:
                            current = urljoin(str(response.url), response.headers["location"])
                            continue
                        if response.status_code >= 400:
                            raise http_status_error(response.status_code)
                        body = self._read(response)
                        content_type = response.headers.get("content-type", "")
                        return FetchedPage(
                            url=str(response.url),
                            content_type=content_type.split(";")[0].strip().lower(),
                            body=body,
                            fetched_at=utc_now_iso(),
                        )
                except httpx.TimeoutException as exc:
                    raise fail(errors.TIMEOUT) from exc
                except httpx.TransportError as exc:
                    raise fail(errors.UNREACHABLE) from exc
        raise fail(errors.TOO_MANY_REDIRECTS)

    def _read(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            raise fail(errors.PAGE_TOO_LARGE)
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > self.max_bytes:
                raise fail(errors.PAGE_TOO_LARGE)
        return bytes(body)

    def _rendered_markdown(self, url: str) -> tuple[str, str | None]:
        if self.renderer is None:
            return "", None
        try:
            html = self.renderer.render(url)
        except Exception:
            logger.info("Headless browser could not render the page", exc_info=True)
            return "", None
        return html_to_markdown(html, url), page_title(html, url)

    def page_snapshot(
        self,
        url: str,
        documents: DocumentParser,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> tuple[Snapshot, str]:
        """Fetch ``url`` and return (snapshot, fetch time)."""
        page = self.fetch(url)
        if check_cancelled is not None:
            check_cancelled()
        name = Path(urlsplit(page.url).path).name or urlsplit(page.url).hostname or page.url
        is_pdf = page.content_type == "application/pdf" or page.body.startswith(b"%PDF-")
        if is_pdf:
            with tempfile.TemporaryDirectory(prefix="trustbook-page-") as tmp:
                path = Path(tmp) / "page.pdf"
                path.write_bytes(page.body)
                return documents.parse(path, "pdf", title=name), page.fetched_at
        sniff = page.body[:512].lstrip().lower()
        if page.content_type in _HTML_TYPES or (
            not page.content_type and sniff.startswith((b"<!doctype", b"<html"))
        ):
            html = decode_text(page.body)
            markdown = html_to_markdown(html, page.url)
            title = page_title(html, page.url)
            if len(markdown) < MIN_EXTRACTED_CHARS:
                rendered, rendered_title = self._rendered_markdown(page.url)
                if len(rendered) > len(markdown):
                    markdown, title = rendered, title or rendered_title
            if not markdown.strip():
                raise fail(errors.PAGE_NO_TEXT)
            return text_snapshot(markdown, title or name), page.fetched_at
        if page.content_type in _TEXT_TYPES:
            text = decode_text(page.body)
            if not text.strip():
                raise fail(errors.PAGE_NO_TEXT)
            return text_snapshot(text, name), page.fetched_at
        raise fail(errors.UNSUPPORTED_PAGE)
