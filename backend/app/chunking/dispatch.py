"""Pick the chunker for a snapshot and derive each chunk's locator from the map."""

from __future__ import annotations

import bisect
from typing import Any

from app.chunking.document import ChunkDraft, Locate, chunk_document
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
        heading: str | None = None
        for segment in segments:
            if segment.start <= start < segment.end:
                heading = segment.heading
                break
        return {"heading": heading, "offset": start}

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


def _file_document_locator(text: str, entry: FileEntry, segments: list[Segment]) -> Locate:
    pages = [s for s in segments if s.file == entry.path and s.page is not None]
    lines = _LineIndex(text, entry.start, entry.end)

    def locate(start: int, end: int) -> dict[str, Any]:
        if pages:
            numbers = sorted({s.page for s in _overlapping(pages, start, end) if s.page})
            return {"file": entry.path, "pages": numbers}
        return {
            "file": entry.path,
            "start_line": lines.line(start),
            "end_line": lines.line(max(start, end - 1)),
            "symbol": None,
        }

    return locate


# Changes whenever chunking a file gives different chunks; a refresh re-chunks files indexed with
# another version instead of keeping their chunks (incremental refresh, SPEC §7.5).
CHUNKER_VERSION = "p3-treesitter-1"


def chunk_file(snapshot: Snapshot, entry: FileEntry, counter: TokenCounter) -> list[ChunkDraft]:
    """The chunks of one file of a ``files`` snapshot."""
    text = snapshot.markdown
    if entry.kind == "code":
        return chunk_source_code(text, entry.start, entry.end, entry.path, counter)
    locate = _file_document_locator(text, entry, snapshot.segments)
    return chunk_document(
        text, entry.start, entry.end, counter, locate, prefix=f"File: {entry.path}\n"
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
