"""Pick the chunker for a snapshot and derive each chunk's locator from the map."""

from __future__ import annotations

import bisect
from typing import Any

from app.chunking.document import ChunkDraft, Header, Locate, chunk_document
from app.chunking.header import document_header
from app.chunking.headings import HeadingPaths, find_headings, path_text
from app.chunking.syntax import chunk_source_code
from app.chunking.tokens import TokenCounter
from app.chunking.transcript import chunk_transcript
from app.parse.snapshot import FileEntry, Segment, Snapshot


def _overlapping(segments: list[Segment], start: int, end: int) -> list[Segment]:
    return [s for s in segments if s.start < end and s.end > start]


def page_locator(segments: list[Segment]) -> Locate:
    def locate(start: int, end: int) -> dict[str, Any]:
        pages = sorted({s.page for s in _overlapping(segments, start, end) if s.page is not None})
        return {"pages": pages}

    return locate


def section_locator(segments: list[Segment]) -> Locate:
    def locate(start: int, end: int) -> dict[str, Any]:
        # The section holding most of the chunk names it (a chunk that starts in the last lines
        # of one section is about the next one); every section it spans is listed too (D-053).
        spans = [
            (min(end, s.end) - max(start, s.start), s.heading)
            for s in _overlapping(segments, start, end)
        ]
        headings = list(dict.fromkeys(h for _, h in spans if h))
        heading = max(spans, key=lambda item: item[0])[1] if spans else None
        locator: dict[str, Any] = {"heading": heading, "offset": start}
        if len(headings) > 1:
            locator["headings"] = headings
        return locator

    return locate


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


# Text documents written in Markdown; other text files (.rst, .txt, …) get reStructuredText
# heading rules. Documents parsed from other formats (PDF, Word, …) are Markdown too.
MARKDOWN_EXTENSIONS = frozenset({"md", "markdown"})


def _markdown_file(path: str, parsed: bool) -> bool:
    name = path.rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return parsed or extension in MARKDOWN_EXTENSIONS


def _file_document_locator(
    text: str, entry: FileEntry, segments: list[Segment], sections: HeadingPaths
) -> Locate:
    pages = [s for s in segments if s.file == entry.path and s.page is not None]
    lines = _LineIndex(text, entry.start, entry.end)

    def locate(start: int, end: int) -> dict[str, Any]:
        # Named like a web chunk (D-053): the innermost heading of the section holding most of
        # the chunk, and every section it spans when there are several (D-068).
        spans = sections.sections(start, end)
        titles = list(dict.fromkeys(h.title for _, h in spans if h is not None))
        biggest = max(spans, key=lambda item: item[0])[1] if spans else None
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
        located["heading"] = biggest.title if biggest is not None else None
        if len(titles) > 1:
            located["headings"] = titles
        return located

    return locate


def _file_document_header(
    text: str, entry: FileEntry, sections: HeadingPaths, paged: bool
) -> Header:
    lines = _LineIndex(text, entry.start, entry.end)

    def header(start: int, end: int) -> str:
        where = path_text(sections.at(start))
        span = None if paged else (lines.line(start), lines.line(max(start, end - 1)))
        return document_header(entry.path, where, span)

    return header


# Changes whenever chunking a file gives different chunks; a refresh re-chunks files indexed with
# another version instead of keeping their chunks (incremental refresh, SPEC §7.5).
# 2: FTS without header line numbers; 3: section = majority; 4: data literals split by element,
# locator kind, document files chunked on whole lines (D-056); 5: document files carry their
# heading path in the header and locator, headings found in Markdown, setext and
# reStructuredText form (D-068)
CHUNKER_VERSION = "p3-treesitter-5"


def chunk_file(snapshot: Snapshot, entry: FileEntry, counter: TokenCounter) -> list[ChunkDraft]:
    """The chunks of one file of a ``files`` snapshot."""
    text = snapshot.markdown
    if entry.kind == "code":
        return chunk_source_code(text, entry.start, entry.end, entry.path, counter)
    # A file's chunks are located by line (unless it has pages): they end on whole lines, so the
    # cited lines hold nothing the chunk does not (ref-near-07).
    paged = any(s.file == entry.path and s.page is not None for s in snapshot.segments)
    parsed = any(s.file == entry.path and s.start_line is None for s in snapshot.segments)
    headings = find_headings(
        text, entry.start, entry.end, markdown=_markdown_file(entry.path, parsed)
    )
    sections = HeadingPaths(headings)
    return chunk_document(
        text,
        entry.start,
        entry.end,
        counter,
        _file_document_locator(text, entry, snapshot.segments, sections),
        header=_file_document_header(text, entry, sections, paged),
        headings=headings,
        align_lines=not paged,
    )


def chunk_snapshot(snapshot: Snapshot, counter: TokenCounter) -> list[ChunkDraft]:
    """All chunks of ``snapshot`` in reading order."""
    text = snapshot.markdown
    if snapshot.kind == "timestamps":
        return chunk_transcript(text, snapshot.segments, counter)
    if snapshot.kind == "files":
        return [d for entry in snapshot.files for d in chunk_file(snapshot, entry, counter)]
    locate = (
        page_locator(snapshot.segments)
        if snapshot.kind == "pages"
        else section_locator(snapshot.segments)
    )
    return chunk_document(text, 0, len(text), counter, locate)
