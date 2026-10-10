"""Incremental refresh of linked folders (SPEC §7.5): re-chunk and re-embed changed files only.

After a successful run each file of a folder source is recorded in ``source_files``: its SHA-256,
where its text sits in ``snapshot.md`` and the ids of its chunks. On the next refresh:

- files with the same hash (and the same chunker version) keep their chunks: same ids, text,
  locator and vectors, only their offsets move to where the file now sits in the rebuilt
  ``snapshot.md`` (so old citations still resolve and the source viewer still highlights them);
- new and changed files are read, chunked and embedded;
- chunks of changed and removed files are deleted from ``space.db`` and the index.

The file records are dropped when a run starts and written again only when it succeeds, so an
interrupted or failed run is followed by a full re-index. A stored record is only trusted when
its chunks still exist and still match the old snapshot text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.chunking.dispatch import CHUNKER_VERSION, chunk_file, chunk_snapshot
from app.chunking.document import ChunkDraft
from app.chunking.tokens import TokenCounter
from app.db.models import Chunk, SourceFile
from app.ingest.folder import FolderSnapshot, PreviousFile, PreviousSnapshot
from app.parse.snapshot import Segment, Snapshot, read_snapshot_files
from app.spaces.sources import SourceRepository, new_id

logger = logging.getLogger("trustbook.ingest")


@dataclass
class Previous:
    """What the last successful run of a folder source left behind."""

    snapshot: PreviousSnapshot
    chunks: dict[str, list[Chunk]]  # path → its chunks (reading order)


@dataclass
class ChunkPlan:
    """The chunk rows of a run and what changes in the index."""

    rows: list[Chunk]  # every chunk of the source, ordinals set
    new_rows: list[Chunk]  # to embed
    stale_ids: list[str]  # old chunks to delete from the index
    files: list[SourceFile] = field(default_factory=list)  # to record after success
    incremental: bool = False


def _segments(data: dict[str, Any]) -> list[Segment]:
    raw = data.get("segments")
    if not isinstance(raw, list):
        return []
    segments: list[Segment] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                segments.append(Segment.from_json(item))
            except TypeError:
                continue
    return segments


def _chunk_ids(raw: str) -> list[str] | None:
    try:
        ids = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return None
    return ids


def load_previous(repo: SourceRepository, source_id: str, folder: Path) -> Previous | None:
    """The trustworthy part of the last run's file records (``None``: index everything)."""
    records = repo.files(source_id)
    if not records:
        return None
    stored = read_snapshot_files(folder)
    if stored is None:
        return None
    markdown, data = stored
    segments = _segments(data)
    by_id = {c.id: c for c in repo.chunks(source_id)}
    files: dict[str, PreviousFile] = {}
    chunks: dict[str, list[Chunk]] = {}
    for record in records:
        start, end = record.start_offset, record.end_offset
        kind = record.kind
        ids = _chunk_ids(record.chunk_ids)
        if (
            record.chunker != CHUNKER_VERSION
            or kind not in ("code", "document")
            or not 0 <= start <= end <= len(markdown)
            or ids is None
        ):
            continue
        rows = [by_id.get(i) for i in ids]
        kept = [c for c in rows if c is not None]
        if len(kept) != len(rows) or not all(_matches(c, markdown, start, end) for c in kept):
            continue
        file_kind: Literal["code", "document"] = "code" if kind == "code" else "document"
        own = tuple(
            s for s in segments if s.file == record.path and start <= s.start <= s.end <= end
        )
        files[record.path] = PreviousFile(record.sha256, file_kind, start, end, own)
        chunks[record.path] = kept
    logger.info("Refresh: %d of %d file records can be reused", len(files), len(records))
    return Previous(PreviousSnapshot(markdown, files), chunks)


def _matches(chunk: Chunk, markdown: str, start: int, end: int) -> bool:
    """The chunk lies inside its file and still is the old snapshot's text."""
    if not start <= chunk.start_offset < chunk.end_offset <= end:
        return False
    return chunk.text.endswith(markdown[chunk.start_offset : chunk.end_offset])


def _new_rows(source_id: str, drafts: Iterable[ChunkDraft]) -> list[Chunk]:
    return [
        Chunk(
            id=new_id(),
            source_id=source_id,
            ordinal=0,
            text=d.text,
            start_offset=d.start,
            end_offset=d.end,
            locator=json.dumps(d.locator),
            token_count=d.token_count,
        )
        for d in drafts
    ]


def _moved(chunk: Chunk, delta: int) -> Chunk:
    return Chunk(
        id=chunk.id,
        source_id=chunk.source_id,
        ordinal=0,
        text=chunk.text,
        start_offset=chunk.start_offset + delta,
        end_offset=chunk.end_offset + delta,
        locator=chunk.locator,
        token_count=chunk.token_count,
    )


def plan_chunks(
    snapshot: Snapshot,
    counter: TokenCounter,
    source_id: str,
    *,
    old_ids: Iterable[str],
    previous: Previous | None,
) -> ChunkPlan:
    """Chunk rows for ``snapshot``: kept chunks of unchanged files, new ones for the rest."""
    rows: list[Chunk] = []
    new_rows: list[Chunk] = []
    files: list[SourceFile] = []
    if isinstance(snapshot, FolderSnapshot):
        kept_by_path = previous.chunks if previous is not None else {}
        for entry in snapshot.files:
            old_start = snapshot.reused.get(entry.path)
            kept = kept_by_path.get(entry.path)
            if old_start is not None and kept is not None:
                file_rows = [_moved(c, entry.start - old_start) for c in kept]
            else:
                file_rows = _new_rows(source_id, chunk_file(snapshot, entry, counter))
                new_rows.extend(file_rows)
            rows.extend(file_rows)
            sha = snapshot.hashes.get(entry.path)
            if sha is not None:
                files.append(
                    SourceFile(
                        source_id=source_id,
                        path=entry.path,
                        sha256=sha,
                        kind=entry.kind,
                        chunker=CHUNKER_VERSION,
                        start_offset=entry.start,
                        end_offset=entry.end,
                        chunk_ids=json.dumps([r.id for r in file_rows]),
                    )
                )
    else:
        rows = new_rows = _new_rows(source_id, chunk_snapshot(snapshot, counter))
    for ordinal, row in enumerate(rows):
        row.ordinal = ordinal
    current = {r.id for r in rows}
    return ChunkPlan(
        rows=rows,
        new_rows=new_rows,
        stale_ids=sorted(set(old_ids) - current),
        files=files,
        incremental=previous is not None,
    )
