"""Re-indexing a space whose chunks an older chunker made (D-106).

A new chunker version changes the chunks of a kind (documents, code, transcripts). Sources keep
the chunks they were indexed with until they are indexed again, so a space tells the user when
one of its sources holds chunks of an older version, and re-indexes it on request:

- **what is old:** a source records the chunker version of each kind of chunk it holds
  (``stats["chunkers"]``, written when it becomes ready). A source indexed before that record
  existed is judged by its folder file records (``source_files.chunker``) or, for other sources,
  by its chunks: transcript chunks did not change, every other kind did.
- **how:** the stored snapshot (``snapshot.md`` and ``map.json``) is chunked again; nothing is
  read, fetched or parsed again, so a re-index never changes what a source says. Folder files
  whose chunks are still current (code files) keep them, and a chunk whose text and place are
  unchanged keeps its id and vector, so only changed chunks are embedded again.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.chunking.dispatch import CHUNKER_VERSIONS
from app.db.models import Source, SourceFile
from app.ingest.folder import TEXT_DOCUMENTS, FolderSnapshot
from app.ingest.incremental import file_chunker
from app.parse.snapshot import FileEntry, MapKind, Segment, Snapshot
from app.spaces.sources import load_stats

REINDEX_KEY = "reindex"  # ingest job payload flag: chunk the stored snapshot again
CHUNKERS_STAT = "chunkers"  # stats key: chunker version per kind of chunk (D-106)
_MAP_KINDS: tuple[MapKind, ...] = ("pages", "timestamps", "sections", "files")
# The chunker version of every kind when sources began to record theirs (D-106): a source without
# a record holds chunks of this version (or an older one).
BEFORE_RECORDS = "p3-treesitter-6"


class StoredSnapshotError(ValueError):
    """The stored snapshot can't be chunked again (missing or unreadable)."""


def chunk_kind(locator: Mapping[str, Any]) -> Literal["code", "document", "transcript"]:
    """The kind of chunk a locator belongs to: transcript (timestamps), code (a folder file
    without a heading key, D-070) or document (everything else)."""
    if "start_s" in locator:
        return "transcript"
    file = locator.get("file")
    if isinstance(file, str) and file and "heading" not in locator:
        return "code"
    return "document"


def parse_locator(raw: str | None) -> dict[str, Any]:
    """A stored chunk locator (``{}`` when unreadable)."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def outdated_kinds(
    source: Source, files: Sequence[SourceFile], first_locator: Mapping[str, Any] | None
) -> set[str]:
    """The kinds of chunk ``source`` holds that an older chunker made (empty: up to date).

    ``files``: a folder source's file records; ``first_locator``: one of its chunks' locator
    (``None`` without chunks), for sources indexed before ``stats["chunkers"]`` existed.
    """
    recorded = load_stats(source).get(CHUNKERS_STAT)
    if isinstance(recorded, dict):
        return {
            str(kind)
            for kind, version in recorded.items()
            if kind in CHUNKER_VERSIONS and version != CHUNKER_VERSIONS[kind]
        }
    if source.type == "folder":
        if not files:  # indexed before file records existed (Phase 2)
            return {"code", "document"} if first_locator is not None else set()
        return {
            "code" if f.kind == "code" else "document"
            for f in files
            if f.chunker != file_chunker(f.kind)
        }
    if first_locator is None:
        return set()
    kind = chunk_kind(first_locator)
    return set() if CHUNKER_VERSIONS[kind] == BEFORE_RECORDS else {kind}


@dataclass(frozen=True)
class ReindexWork:
    """How many of a source's chunks a re-index embeds again, at most (for its estimate)."""

    code_chunks: int
    text_chunks: int


def reindex_work(locators: Iterable[Mapping[str, Any]], kinds: set[str]) -> ReindexWork:
    """The chunks of the outdated ``kinds`` (an upper bound: unchanged chunks are kept)."""
    code = text = 0
    for locator in locators:
        kind = chunk_kind(locator)
        if kind not in kinds:
            continue
        if kind == "code":
            code += 1
        else:
            text += 1
    return ReindexWork(code, text)


def _segments(data: Mapping[str, Any]) -> list[Segment]:
    raw = data.get("segments")
    segments: list[Segment] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            try:
                segments.append(Segment.from_json(item))
            except TypeError:
                continue
    return segments


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _file_entries(segments: list[Segment], files: Sequence[SourceFile]) -> list[FileEntry]:
    """The files of a folder snapshot, from the map's segments (a file's segments are contiguous;
    a parsed document has one per page). Its kind comes from its file record, else from its
    extension as the folder reader decides it (a parsed document is a document)."""
    kinds = {f.path: f.kind for f in files}
    spans: dict[str, list[Segment]] = {}
    for segment in segments:
        if segment.file:
            spans.setdefault(segment.file, []).append(segment)
    entries: list[FileEntry] = []
    for path, own in spans.items():
        kind = kinds.get(path)
        if kind is None:
            paged = any(s.page is not None for s in own)
            kind = "document" if paged or _extension(path) in TEXT_DOCUMENTS else "code"
        start, end = min(s.start for s in own), max(s.end for s in own)
        entries.append(FileEntry(path, start, end, "code" if kind == "code" else "document"))
    return sorted(entries, key=lambda e: e.start)


def stored_snapshot(
    markdown: str,
    data: Mapping[str, Any],
    files: Sequence[SourceFile] = (),
    reusable: Mapping[str, int] | None = None,
) -> Snapshot:
    """The snapshot a source was last indexed from, as ingestion built it.

    A folder source's snapshot is a :class:`FolderSnapshot` whose files in ``reusable`` (path →
    start offset of its current file record) keep their chunks, and whose file hashes come from
    the file records.

    :raises StoredSnapshotError: the map names no kind this version knows.
    """
    kind = data.get("kind")
    if kind not in _MAP_KINDS:
        raise StoredSnapshotError(f"unknown snapshot kind {kind!r}")
    segments = _segments(data)
    if kind != "files":
        return Snapshot(markdown=markdown, kind=kind, segments=segments)
    entries = _file_entries(segments, files)
    if not entries:
        raise StoredSnapshotError("the folder snapshot names no files")
    keep = reusable or {}
    return FolderSnapshot(
        markdown=markdown,
        kind="files",
        segments=segments,
        files=entries,
        hashes={f.path: f.sha256 for f in files},
        reused={e.path: keep[e.path] for e in entries if e.path in keep},
    )


__all__ = [
    "CHUNKERS_STAT",
    "REINDEX_KEY",
    "ReindexWork",
    "StoredSnapshotError",
    "chunk_kind",
    "outdated_kinds",
    "parse_locator",
    "reindex_work",
    "stored_snapshot",
]
