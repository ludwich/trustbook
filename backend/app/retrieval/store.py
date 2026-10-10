"""Read-only queries on ``space.db`` used by retrieval, chat and the overview."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, func, or_
from sqlmodel import Session, col, select

from app.db.models import Chunk, Source

READY = "ready"


@dataclass(frozen=True)
class SourceCounts:
    """Numbers behind "Using X of Y sources" (X = enabled and ready, Y = ready)."""

    total: int
    ready: int
    enabled_ready: int


@dataclass(frozen=True)
class ChunkRow:
    """A chunk with its source title, as retrieval hands it on."""

    chunk_id: str
    source_id: str
    source_title: str
    ordinal: int
    text: str
    locator: dict[str, Any]
    token_count: int


def parse_locator(raw: str | None) -> dict[str, Any]:
    """The JSON locator of a chunk as a dict ({} when missing or broken)."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def usable_sources(session: Session) -> list[Source]:
    """Sources chat may use: ticked (enabled) and ready, in creation order."""
    query = (
        select(Source)
        .where(col(Source.enabled).is_(True), Source.status == READY)
        .order_by(col(Source.created_at), col(Source.id))
    )
    return list(session.exec(query).all())


def source_counts(session: Session) -> SourceCounts:
    total = session.exec(select(func.count()).select_from(Source)).one()
    ready = session.exec(
        select(func.count()).select_from(Source).where(Source.status == READY)
    ).one()
    enabled_ready = session.exec(
        select(func.count())
        .select_from(Source)
        .where(Source.status == READY, col(Source.enabled).is_(True))
    ).one()
    return SourceCounts(int(total), int(ready), int(enabled_ready))


def load_chunks(
    engine: Engine, chunk_ids: Sequence[str], allowed_source_ids: Iterable[str]
) -> dict[str, ChunkRow]:
    """Chunks by id, restricted to ``allowed_source_ids`` (the filter is applied again here)."""
    allowed = set(allowed_source_ids)
    if not chunk_ids or not allowed:
        return {}
    with Session(engine) as session:
        rows = session.exec(
            select(Chunk, Source.title)
            .join(Source, col(Source.id) == col(Chunk.source_id))
            .where(col(Chunk.id).in_(list(chunk_ids)), col(Chunk.source_id).in_(allowed))
        ).all()
    return {chunk.id: _row(chunk, title) for chunk, title in rows}


def first_chunks(engine: Engine, source_ids: Sequence[str]) -> list[ChunkRow]:
    """The first chunk (lowest ordinal) of each source, in the order of ``source_ids``."""
    if not source_ids:
        return []
    with Session(engine) as session:
        firsts = (
            select(Chunk.source_id, func.min(Chunk.ordinal).label("ordinal"))
            .where(col(Chunk.source_id).in_(list(source_ids)))
            .group_by(col(Chunk.source_id))
            .subquery()
        )
        rows = session.exec(
            select(Chunk, Source.title)
            .join(Source, col(Source.id) == col(Chunk.source_id))
            .join(
                firsts,
                (col(Chunk.source_id) == firsts.c.source_id)
                & (col(Chunk.ordinal) == firsts.c.ordinal),
            )
        ).all()
    by_source = {chunk.source_id: _row(chunk, title) for chunk, title in rows}
    return [by_source[sid] for sid in source_ids if sid in by_source]


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def chunks_assigning(engine: Engine, source_id: str, names: Sequence[str]) -> list[ChunkRow]:
    """Chunks of one source with a line starting ``NAME =`` / ``NAME:`` for any name, by ordinal.

    A pre-filter with case-sensitive ``GLOB`` (about 1 ms per name on 3,600 chunks; ``instr``
    is ~5× slower because it counts characters); callers check the exact match themselves.
    Code chunk bodies always follow their "File: …" line, so "\\n" + name finds column 0.
    """
    names = [name for name in names if _IDENTIFIER.fullmatch(name)]  # no GLOB wildcards
    if not names:
        return []
    conditions = [col(Chunk.text).op("GLOB")(f"*\n{name}[ \t:=]*") for name in names]
    with Session(engine) as session:
        rows = session.exec(
            select(Chunk, Source.title)
            .join(Source, col(Source.id) == col(Chunk.source_id))
            .where(col(Chunk.source_id) == source_id, or_(*conditions))
            .order_by(col(Chunk.ordinal))
        ).all()
    return [_row(chunk, title) for chunk, title in rows]


def total_tokens(engine: Engine, source_ids: Sequence[str]) -> tuple[int, int]:
    """(chunk count, token count) of the given sources."""
    if not source_ids:
        return 0, 0
    with Session(engine) as session:
        count, tokens = session.exec(
            select(func.count(), func.coalesce(func.sum(Chunk.token_count), 0)).where(
                col(Chunk.source_id).in_(list(source_ids))
            )
        ).one()
    return int(count), int(tokens)


def _row(chunk: Chunk, title: str) -> ChunkRow:
    return ChunkRow(
        chunk_id=chunk.id,
        source_id=chunk.source_id,
        source_title=title,
        ordinal=chunk.ordinal,
        text=chunk.text,
        locator=parse_locator(chunk.locator),
        token_count=chunk.token_count,
    )
