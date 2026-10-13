"""Pick the chunker for a snapshot and derive each chunk's locator from the map."""

from __future__ import annotations

import bisect
from collections.abc import Callable
from typing import Any, Literal

from app.chunking.document import ChunkDraft, Header, Locate, Parent, chunk_document
from app.chunking.header import document_header, section_header
from app.chunking.headings import (
    FENCES,
    Heading,
    HeadingPaths,
    Syntax,
    find_headings,
    heading_syntax,
    path_text,
)
from app.chunking.imports import SourceFiles
from app.chunking.structure import (
    Structure,
    line_section_key,
    offset_section_key,
)
from app.chunking.syntax import chunk_source_code
from app.chunking.tokens import TokenCounter
from app.chunking.transcript import chunk_transcript
from app.parse.snapshot import FileEntry, Segment, Snapshot


def _overlapping(segments: list[Segment], start: int, end: int) -> list[Segment]:
    return [s for s in segments if s.start < end and s.end > start]


def chunk_section(sections: HeadingPaths, start: int, end: int) -> tuple[Heading, ...]:
    """The heading path a chunk of ``text[start:end]`` is named by (D-104): the section of its last
    character. Chunks are split at headings, so that is the section of all of its text; a chunk
    that starts with headings without text of their own is named by the last of them."""
    return sections.at(max(start, end - 1))


def _title(path: tuple[Heading, ...]) -> str | None:
    """The innermost title of ``path`` (``None`` for no heading)."""
    return path[-1].title if path else None


def page_locator(segments: list[Segment], sections: HeadingPaths | None = None) -> Locate:
    """Pages of a parsed document; with ``sections`` also the chunk's ``heading`` when it is
    under one (D-105)."""

    def locate(start: int, end: int) -> dict[str, Any]:
        pages = sorted({s.page for s in _overlapping(segments, start, end) if s.page is not None})
        located: dict[str, Any] = {"pages": pages}
        heading = _title(chunk_section(sections, start, end)) if sections is not None else None
        if heading:
            located["heading"] = heading
        return located

    return locate


def section_locator(sections: HeadingPaths) -> Locate:
    """A web page's, pasted or uploaded text's chunk: the innermost heading of its section (D-053;
    chunks are split at headings since D-104, so it names all of the chunk) and its offset."""

    def locate(start: int, end: int) -> dict[str, Any]:
        return {"heading": _title(chunk_section(sections, start, end)), "offset": start}

    return locate


def _section_header(sections: HeadingPaths) -> Header:
    """``Section: Outer › Inner`` for a chunk under a heading, ``""`` before the first (D-105)."""

    def header(start: int, end: int) -> str:
        return section_header(path_text(chunk_section(sections, start, end)))

    return header


class _LineIndex:
    """Line numbers for offsets in a file that starts at ``base`` in the snapshot."""

    def __init__(self, text: str, base: int, end: int) -> None:
        self._newlines: list[int] = []
        cursor = text.find("\n", base, end)
        while cursor != -1:
            self._newlines.append(cursor)
            cursor = text.find("\n", cursor + 1, end)

    def line(self, offset: int) -> int:
        return bisect.bisect_left(self._newlines, offset) + 1


def _file_document_locator(
    text: str, entry: FileEntry, segments: list[Segment], sections: HeadingPaths
) -> Locate:
    pages = [s for s in segments if s.file == entry.path and s.page is not None]
    lines = _LineIndex(text, entry.start, entry.end)

    def locate(start: int, end: int) -> dict[str, Any]:
        # The innermost heading of the chunk's section, the one its header names (D-068, D-104).
        path = chunk_section(sections, start, end)
        located: dict[str, Any]
        if pages:
            numbers = sorted({s.page for s in _overlapping(pages, start, end) if s.page})
            located = {"file": entry.path, "pages": numbers}
        else:
            located = {
                "file": entry.path,
                "start_line": lines.line(start),
                "end_line": lines.line(max(start, end - 1)),
                "symbol": None,
                "kind": None,
            }
        located["heading"] = _title(path)
        return located

    return locate


def _file_document_header(
    text: str, entry: FileEntry, sections: HeadingPaths, paged: bool
) -> Header:
    lines = _LineIndex(text, entry.start, entry.end)

    def header(start: int, end: int) -> str:
        where = path_text(chunk_section(sections, start, end))
        span = None if paged else (lines.line(start), lines.line(max(start, end - 1)))
        return document_header(entry.path, where, span)

    return header


def _section_parent(sections: HeadingPaths, key: Callable[[Heading], str]) -> Parent:
    """A document chunk's parent (D-077): the section its header names (D-068; the section of
    all of its text since D-104), so the parent title and the header path are the same string.
    ``head``: the chunk starts at or inside the section's heading block. No heading: no parent.

    Web pages, pasted and uploaded text use the same rule with offset keys (``#o<heading
    start>``); their parent title is the heading path of their ``Section:`` line (D-105)."""

    def parent(start: int, end: int) -> tuple[str | None, Structure | None]:
        path = chunk_section(sections, start, end)
        if not path:
            return None, None
        heading = path[-1]
        title = " ".join(path_text(path).split())
        return key(heading), Structure("section", title, None, start <= heading.end)

    return parent


# Changes whenever chunking a file gives different chunks; a refresh re-chunks files indexed with
# another version instead of keeping their chunks (incremental refresh, SPEC §7.5).
# 2: FTS without header line numbers; 3: section = majority; 4: data literals split by element,
# locator kind, document files chunked on whole lines (D-056); 5: document files carry their
# heading path in the header and locator, headings found in Markdown, setext and
# reStructuredText form (D-068); 6: every chunk stores its parent key and structure (section,
# class or module; definitions, imports) for parent/child retrieval (D-077); 7: documents, web
# pages, pasted and uploaded text are split at every heading, and web, paste and upload chunks
# under a heading start with a ``Section:`` line (D-104, D-105). Code and transcript chunks did not
# change in 7, so their kinds keep version 6 in ``CHUNKER_VERSIONS`` and their chunks are kept.
CHUNKER_VERSION = "p5-headings-7"

ChunkKind = Literal["code", "document", "transcript"]
# The chunker version that makes each kind of chunk (D-106). A kind's entry changes only when its
# chunks change: a refresh keeps a file's chunks only when its kind's version made them, and a
# source needs re-indexing when it holds chunks made by an older version.
CHUNKER_VERSIONS: dict[str, str] = {
    "code": "p3-treesitter-6",
    "document": CHUNKER_VERSION,
    "transcript": "p3-treesitter-6",
}


def snapshot_chunkers(snapshot: Snapshot) -> dict[str, str]:
    """The chunker version per kind of chunk ``snapshot`` gives (D-106)."""
    if snapshot.kind == "timestamps":
        kinds: set[ChunkKind] = {"transcript"}
    elif snapshot.kind == "files":
        kinds = {"code" if e.kind == "code" else "document" for e in snapshot.files}
    else:
        kinds = {"document"}
    return {kind: CHUNKER_VERSIONS[kind] for kind in sorted(kinds)}


def snapshot_files(snapshot: Snapshot) -> SourceFiles:
    """The files of a ``files`` snapshot, indexed to resolve code imports (once per snapshot)."""
    return SourceFiles(entry.path for entry in snapshot.files)


def chunk_file(
    snapshot: Snapshot,
    entry: FileEntry,
    counter: TokenCounter,
    files: SourceFiles | None = None,
) -> list[ChunkDraft]:
    """The chunks of one file of a ``files`` snapshot.

    ``files``: :func:`snapshot_files` of the snapshot (built here when not given).
    """
    text = snapshot.markdown
    if entry.kind == "code":
        if files is None:
            files = snapshot_files(snapshot)
        return chunk_source_code(text, entry.start, entry.end, entry.path, counter, files)
    # A file's chunks are located by line (unless it has pages): they end on whole lines, so the
    # cited lines hold nothing the chunk does not (ref-near-07).
    paged = any(s.file == entry.path and s.page is not None for s in snapshot.segments)
    parsed = any(s.file == entry.path and s.start_line is None for s in snapshot.segments)
    syntax: Syntax = "markdown" if parsed else heading_syntax(entry.path)
    headings = find_headings(text, entry.start, entry.end, syntax=syntax)
    sections = HeadingPaths(headings)
    lines = _LineIndex(text, entry.start, entry.end)

    def section_key(heading: Heading) -> str:
        if paged:  # file-relative, so a file moved by a refresh keeps its keys
            return offset_section_key(entry.path, heading.start - entry.start)
        return line_section_key(entry.path, lines.line(heading.start))

    return chunk_document(
        text,
        entry.start,
        entry.end,
        counter,
        _file_document_locator(text, entry, snapshot.segments, sections),
        header=_file_document_header(text, entry, sections, paged),
        parent=_section_parent(sections, section_key),
        headings=headings,
        fence=FENCES[syntax],
        align_lines=not paged,
    )


def chunk_snapshot(snapshot: Snapshot, counter: TokenCounter) -> list[ChunkDraft]:
    """All chunks of ``snapshot`` in reading order."""
    text = snapshot.markdown
    if snapshot.kind == "timestamps":
        return chunk_transcript(text, snapshot.segments, counter)
    if snapshot.kind == "files":
        files = snapshot_files(snapshot)
        return [d for entry in snapshot.files for d in chunk_file(snapshot, entry, counter, files)]
    # Parsed documents ("pages": PDF, Word, … uploads), web pages, pasted and uploaded text
    # ("sections"): Markdown headings, each starting a chunk (D-104); a chunk under a heading starts
    # with its ``Section:`` line (D-105), and its parent is that section, keyed by offset (D-077).
    headings = find_headings(text, 0, len(text), syntax="markdown")
    sections = HeadingPaths(headings)
    locate = (
        page_locator(snapshot.segments, sections)
        if snapshot.kind == "pages"
        else section_locator(sections)
    )
    return chunk_document(
        text,
        0,
        len(text),
        counter,
        locate,
        header=_section_header(sections),
        parent=_section_parent(sections, lambda h: offset_section_key("", h.start)),
        headings=headings,
    )
