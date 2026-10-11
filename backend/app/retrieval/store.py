"""Read-only queries on ``space.db`` used by retrieval, chat and the overview."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine, Row, bindparam, func, or_, text
from sqlmodel import Session, col, select

from app.chunking.structure import Structure, key_file, module_key, parse_structure
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
    # The chunk's parent (D-077): its parent key and structure record, ``None`` for rows without
    # one (old rows, transcripts, fallback-chunked code, heading-less text). Never citable text.
    parent_key: str | None = None
    structure: Structure | None = None


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


def chunks_of_symbol(engine: Engine, source_id: str, file: str, symbol: str) -> list[ChunkRow]:
    """Code chunks of one source whose locator names ``file`` and ``symbol``, by ordinal.

    Used to gather the parts of a split data literal (D-056, D-058); ``json_extract`` scans only
    the source's chunks.
    """
    locator = col(Chunk.locator)
    with Session(engine) as session:
        rows = session.exec(
            select(Chunk, Source.title)
            .join(Source, col(Source.id) == col(Chunk.source_id))
            .where(
                col(Chunk.source_id) == source_id,
                func.json_extract(locator, "$.file") == file,
                func.json_extract(locator, "$.symbol") == symbol,
            )
            .order_by(col(Chunk.ordinal))
        ).all()
    return [_row(chunk, title) for chunk, title in rows]


class FileRows:
    """The code rows of one file, by ordinal: a per-retrieval cache shared by the lookups (D-077).

    A file's rows are those whose parent key belongs to the file (``{file}::`` and
    ``{file}::{Class}``), i.e. the module and class families of a structure-chunked code file. One
    query per ``(source, file)``, a prefix range on ``parent_key``, run on the first call and then
    cached. Sources outside ``allowed_source_ids`` give no rows (the filter is applied again here).
    A file without structure (old rows, fallback-chunked code) gives no rows; callers then take
    their path without parent keys.
    """

    def __init__(self, engine: Engine, allowed_source_ids: Iterable[str]) -> None:
        self._engine = engine
        self._allowed = frozenset(allowed_source_ids)
        self._cache: dict[tuple[str, str], tuple[ChunkRow, ...]] = {}
        self._structured: bool | None = None
        self.queries = 0  # database queries run for rows (for tests and diagnostics)

    def __call__(self, source_id: str, file: str) -> Sequence[ChunkRow]:
        cached = self._cache.get((source_id, file))
        if cached is not None:
            return cached
        rows: tuple[ChunkRow, ...] = ()
        if file and source_id in self._allowed:
            with self._engine.connect() as conn:
                if self._structured is None:
                    self._structured = _has_structure_columns(conn)
                if self._structured:
                    prefix = module_key(file)
                    # Keys starting with "{file}::" sort in ["{file}::", "{file}:;") byte-wise.
                    result = conn.execute(
                        text(
                            f"{_ROW_SELECT.format(structure=_STRUCTURE_COLUMNS)} "
                            "WHERE c.source_id = :source_id "
                            "AND c.parent_key >= :low AND c.parent_key < :high "
                            "ORDER BY c.ordinal"
                        ),
                        {"source_id": source_id, "low": prefix, "high": prefix[:-1] + ";"},
                    )
                    self.queries += 1
                    rows = tuple(
                        row
                        for row in (_text_row(r) for r in result)
                        # A file named "{file}::x" would share the prefix: keep this file only.
                        if row.parent_key is not None and key_file(row.parent_key) == file
                    )
        self._cache[(source_id, file)] = rows
        return rows


def chunks_at_ordinals(
    engine: Engine, source_id: str, ordinals: Sequence[int], allowed_source_ids: Iterable[str]
) -> dict[int, ChunkRow]:
    """The chunks of one source at the given ordinals, by ordinal (missing ordinals are left out).

    Restricted to ``allowed_source_ids`` (the filter is applied again here).
    """
    if not ordinals or source_id not in set(allowed_source_ids):
        return {}
    with engine.connect() as conn:
        structure = _STRUCTURE_COLUMNS if _has_structure_columns(conn) else _NO_STRUCTURE
        result = conn.execute(
            text(
                f"{_ROW_SELECT.format(structure=structure)} "
                "WHERE c.source_id = :source_id AND c.ordinal IN :ordinals"
            ).bindparams(bindparam("ordinals", expanding=True)),
            {"source_id": source_id, "ordinals": sorted(set(ordinals))},
        )
        rows = [_text_row(r) for r in result]
    return {row.ordinal: row for row in rows}


# Until every space.db has the parent columns (migration 005), reads check for them.
_ROW_SELECT = (
    "SELECT c.id, c.source_id, s.title, c.ordinal, c.text, c.locator, c.token_count, {structure} "
    "FROM chunks AS c JOIN sources AS s ON s.id = c.source_id"
)
_STRUCTURE_COLUMNS = "c.parent_key, c.structure"
_NO_STRUCTURE = "NULL, NULL"


def _has_structure_columns(conn: Connection) -> bool:
    names = {str(r[1]) for r in conn.execute(text("PRAGMA table_info(chunks)"))}
    return {"parent_key", "structure"} <= names


def _text_row(r: Row[Any]) -> ChunkRow:
    chunk_id, source_id, title, ordinal, body, locator, tokens, parent_key, structure = tuple(r)
    return ChunkRow(
        chunk_id=str(chunk_id),
        source_id=str(source_id),
        source_title=str(title),
        ordinal=int(ordinal),
        text=str(body),
        locator=parse_locator(locator),
        token_count=int(tokens),
        parent_key=str(parent_key) if parent_key is not None else None,
        structure=parse_structure(structure if isinstance(structure, str) else None),
    )


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
