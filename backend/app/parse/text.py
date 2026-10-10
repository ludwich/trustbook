"""Plain text, Markdown, CSV, JSON and HTML into snapshots."""

from __future__ import annotations

import re
from typing import Any

from app.parse.snapshot import Snapshot, normalise_text, sections_from_markdown

_PILCROW = re.compile(r"[ \t]*¶[ \t]*")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def decode_text(data: bytes) -> str:
    """Decode file bytes: BOMs first, then UTF-8, then Windows-1252 (never fails)."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def looks_binary(data: bytes) -> bool:
    """NUL bytes in the first 8 KB mean binary (UTF-16 text is handled by its BOM)."""
    head = data[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    return b"\x00" in head


def text_snapshot(text: str, title: str | None = None) -> Snapshot:
    """A snapshot of plain text or Markdown, mapped by heading sections."""
    markdown = normalise_text(text)
    return Snapshot(
        markdown=markdown,
        kind="sections",
        segments=sections_from_markdown(markdown),
        title=title,
    )


def html_title(html: str) -> str | None:
    match = _TITLE.search(html[:200_000])
    if not match:
        return None
    title = " ".join(_PILCROW.sub(" ", match.group(1)).split())
    return title[:200] or None


def html_to_markdown(html: str, url: str | None = None) -> str:
    """Main content of an HTML page as Markdown via Trafilatura ('' when nothing is found)."""
    import trafilatura

    result: Any = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
        favor_precision=True,
    )
    if not isinstance(result, str):
        return ""
    return normalise_text(_PILCROW.sub("", result))


def page_title(html: str, url: str | None = None) -> str | None:
    """The page title from Trafilatura's metadata, else ``<title>``."""
    import trafilatura

    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        metadata = None
    title = getattr(metadata, "title", None) if metadata is not None else None
    if isinstance(title, str) and title.strip():
        cleaned = " ".join(_PILCROW.sub(" ", title).split())[:200]  # Sphinx headings end in ¶
        if cleaned:
            return cleaned
    return html_title(html)
