"""LanceDB index per space at ``<space>/vectors/`` (table ``chunks``), embedded and offline.

Columns: chunk_id, source_id, text, vector (fixed size, set by the first rows added). Full-text
index on ``text`` and a BTree on ``source_id``. Vector search is exact (no ANN index) with cosine
distance; scores are ``1 - distance`` for vectors and the BM25 ``_score`` for full text.
Writes to one space are serialised; every search is filtered to the given source ids. Bulk
indexing appends in batches and folds them into the full-text index once at the end.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.index.base import Hit, IndexedChunk

logger = logging.getLogger("trustbook.index")

TABLE = "chunks"
DELETE_BATCH = 500  # chunk ids per delete filter
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class IndexDimensionError(ValueError):
    """Vectors do not match the space's index (the embedding model changed)."""


def _filter(source_ids: Sequence[str]) -> str | None:
    """``source_id IN (...)`` for valid ids only, or ``None`` when nothing may match."""
    safe = sorted({s for s in source_ids if _SAFE_ID.match(s)})
    if not safe:
        return None
    return "source_id IN (" + ", ".join(f"'{s}'" for s in safe) + ")"


class LanceSpaceIndex:
    """One space's chunks in LanceDB."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._lock = threading.RLock()
        self._db: Any = None
        self._table: Any = None

    def _connect(self) -> Any:
        if self._db is None:
            import lancedb

            self._folder.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(str(self._folder))
        return self._db

    def _open(self) -> Any:
        """The table, or ``None`` when nothing was indexed yet."""
        with self._lock:
            if self._table is None:
                db = self._connect()
                if TABLE in db.list_tables().tables:
                    self._table = db.open_table(TABLE)
            return self._table

    def _create(self, dim: int) -> Any:
        import pyarrow as pa
        from lancedb.index import FTS, BTree

        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("source_id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
            ]
        )
        table = self._connect().create_table(TABLE, schema=schema)
        table.create_index("text", config=FTS(), replace=True)
        table.create_index("source_id", config=BTree(), replace=True)
        return table

    @property
    def dim(self) -> int | None:
        table = self._open()
        if table is None:
            return None
        return int(table.schema.field("vector").type.list_size)

    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        with self._lock:
            self.append(chunks, vectors)
            self.refresh_text_index()

    def refresh_text_index(self) -> None:
        with self._lock:
            table = self._open()
            if table is not None:
                table.optimize()  # fold new rows into the full-text index

    def append(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors differ in length")
        if not chunks:
            return
        dim = len(vectors[0])
        if any(len(v) != dim for v in vectors):
            raise IndexDimensionError("Vectors have different sizes.")
        rows = [
            {
                "chunk_id": c.chunk_id,
                "source_id": c.source_id,
                "text": c.text,
                "vector": [float(x) for x in v],
            }
            for c, v in zip(chunks, vectors, strict=True)
        ]
        with self._lock:
            table = self._open()
            if table is None:
                table = self._table = self._create(dim)
            elif self.dim != dim:
                raise IndexDimensionError(
                    "This space was indexed with a different embedding model."
                )
            table.add(rows)

    def delete_source(self, source_id: str) -> None:
        if not _SAFE_ID.match(source_id):
            raise ValueError("Invalid source id")
        with self._lock:
            table = self._open()
            if table is not None:
                table.delete(f"source_id = '{source_id}'")

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        ids = sorted(set(chunk_ids))
        if any(not _SAFE_ID.match(i) for i in ids):
            raise ValueError("Invalid chunk id")
        with self._lock:
            table = self._open()
            if table is None:
                return
            for start in range(0, len(ids), DELETE_BATCH):
                batch = ids[start : start + DELETE_BATCH]
                table.delete("chunk_id IN (" + ", ".join(f"'{i}'" for i in batch) + ")")

    def vector_search(
        self, vector: Sequence[float], *, limit: int, source_ids: Sequence[str]
    ) -> list[Hit]:
        where = _filter(source_ids)
        table = self._open()
        if where is None or table is None or limit <= 0:
            return []
        if self.dim != len(vector):
            logger.warning("Query vector size %d does not match the index", len(vector))
            return []
        rows = (
            table.search([float(x) for x in vector], vector_column_name="vector")
            .metric("cosine")
            .where(where, prefilter=True)
            .limit(limit)
            .select(["chunk_id", "source_id", "_distance"])
            .to_list()
        )
        return [Hit(r["chunk_id"], r["source_id"], 1.0 - float(r["_distance"])) for r in rows]

    def text_search(self, query: str, *, limit: int, source_ids: Sequence[str]) -> list[Hit]:
        where = _filter(source_ids)
        table = self._open()
        if where is None or table is None or limit <= 0 or not query.strip():
            return []
        rows = (
            table.search(query, query_type="fts", fts_columns="text")
            .where(where, prefilter=True)
            .limit(limit)
            .select(["chunk_id", "source_id", "_score"])
            .to_list()
        )
        return [Hit(r["chunk_id"], r["source_id"], float(r["_score"])) for r in rows]

    def count(self) -> int:
        table = self._open()
        return 0 if table is None else int(table.count_rows())


class LanceIndexRegistry:
    """Opens and caches each space's index; ``folder_for`` maps a space id to its vectors dir."""

    def __init__(self, folder_for: Callable[[str], Path]) -> None:
        self._folder_for = folder_for
        self._indexes: dict[str, LanceSpaceIndex] = {}
        self._lock = threading.Lock()

    def for_space(self, space_id: str) -> LanceSpaceIndex:
        with self._lock:
            index = self._indexes.get(space_id)
            if index is None:
                index = self._indexes[space_id] = LanceSpaceIndex(self._folder_for(space_id))
            return index

    def drop(self, space_id: str) -> None:
        with self._lock:
            self._indexes.pop(space_id, None)
