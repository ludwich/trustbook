"""In-memory index for tests: cosine similarity and a simple term-overlap text score.

Ties are ordered like the LanceDB index: by a key derived from the text (D-059).
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Sequence

from app.index.base import Hit, IndexedChunk, hit_order, tie_key

_WORD = re.compile(r"\w+")


def _terms(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


class InMemorySpaceIndex:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[IndexedChunk, list[float]]] = {}
        self._lock = threading.Lock()

    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors differ in length")
        with self._lock:
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._rows[chunk.chunk_id] = (chunk, [float(v) for v in vector])

    def append(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        self.add(chunks, vectors)

    def refresh_text_index(self) -> None:
        return None

    def delete_source(self, source_id: str) -> None:
        with self._lock:
            for chunk_id in [k for k, (c, _) in self._rows.items() if c.source_id == source_id]:
                del self._rows[chunk_id]

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        with self._lock:
            for chunk_id in chunk_ids:
                self._rows.pop(chunk_id, None)

    def vector_search(
        self, vector: Sequence[float], *, limit: int, source_ids: Sequence[str]
    ) -> list[Hit]:
        allowed = set(source_ids)
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        hits: list[Hit] = []
        with self._lock:
            for chunk, row in self._rows.values():
                if chunk.source_id not in allowed:
                    continue
                row_norm = math.sqrt(sum(v * v for v in row)) or 1.0
                score = sum(a * b for a, b in zip(vector, row, strict=True)) / (norm * row_norm)
                hits.append(Hit(chunk.chunk_id, chunk.source_id, score, tie_key(chunk.text)))
        return sorted(hits, key=hit_order)[:limit]

    def text_search(self, query: str, *, limit: int, source_ids: Sequence[str]) -> list[Hit]:
        allowed = set(source_ids)
        wanted = set(_terms(query))
        hits: list[Hit] = []
        with self._lock:
            for chunk, _ in self._rows.values():
                if chunk.source_id not in allowed:
                    continue
                terms = _terms(chunk.text)
                score = float(sum(1 for t in terms if t in wanted))
                if score > 0:
                    hits.append(Hit(chunk.chunk_id, chunk.source_id, score, tie_key(chunk.text)))
        return sorted(hits, key=hit_order)[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._rows)


class InMemoryIndexRegistry:
    def __init__(self) -> None:
        self._indexes: dict[str, InMemorySpaceIndex] = {}
        self._lock = threading.Lock()

    def for_space(self, space_id: str) -> InMemorySpaceIndex:
        with self._lock:
            return self._indexes.setdefault(space_id, InMemorySpaceIndex())

    def drop(self, space_id: str) -> None:
        with self._lock:
            self._indexes.pop(space_id, None)
