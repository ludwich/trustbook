"""The index interface shared by ingestion (writes) and retrieval (reads)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

_LINES_IN_HEADER = re.compile(r"^(File: [^\n]*?)\s*\(lines \d+-\d+\)", re.MULTILINE)


def search_text(chunk_text: str) -> str:
    """The text indexed for full-text search: a code chunk's header keeps path and symbol but loses
    its line numbers, so a query for "429" matches content, not "(lines 425-450)" (D-051)."""
    return _LINES_IN_HEADER.sub(r"\1", chunk_text, count=1)


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source_id: str
    text: str


def tie_key(indexed_text: str) -> str:
    """A key that orders search hits with equal scores (D-056).

    It depends only on the indexed text, so it is the same on every machine and after every
    re-index; chunk ids are new uuids each time and an index's internal order depends on the order
    rows were written in.
    """
    return hashlib.sha256(indexed_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Hit:
    """One search result. ``score``: higher is better (cosine similarity or BM25).

    ``key``: :func:`tie_key` of the indexed text, which breaks score ties ("" when unknown; the
    chunk id is then used).
    """

    chunk_id: str
    source_id: str
    score: float
    key: str = ""


def hit_order(hit: Hit) -> tuple[float, str, str]:
    """Sort key: best score first, then the content key, then the chunk id."""
    return (-hit.score, hit.key or hit.chunk_id, hit.chunk_id)


def stable_top(fetch: Callable[[int], Sequence[Hit]], limit: int) -> list[Hit]:
    """The top ``limit`` hits of ``fetch(n)`` with ties ordered by :func:`hit_order`.

    An engine returns tied hits in an arbitrary order and may cut a tie group anywhere. So more
    hits are fetched than needed (twice the limit, doubling) until the tie group at the cut is
    complete, then the hits are sorted and cut. Most queries need one fetch.
    """
    if limit <= 0:
        return []
    size = limit * 2
    while True:
        hits = list(fetch(size))
        if len(hits) < size:
            break
        scores = sorted((h.score for h in hits), reverse=True)
        if scores[-1] < scores[limit - 1]:
            break  # the last fetched hit is below the cut: the tie group at the cut is closed
        size *= 2
    return sorted(hits, key=hit_order)[:limit]


class SpaceIndex(Protocol):
    """Vectors and full text for one space's chunks; every search is filtered to ``source_ids``."""

    def add(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        """Add rows and make them searchable by text right away."""
        ...

    def append(self, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]) -> None:
        """Add rows in bulk; call :meth:`refresh_text_index` once after the last batch."""
        ...

    def refresh_text_index(self) -> None:
        """Fold rows added with :meth:`append` into the full-text index."""
        ...

    def delete_source(self, source_id: str) -> None: ...

    def delete_chunks(self, chunk_ids: Sequence[str]) -> None:
        """Remove these chunks' rows (incremental refresh of a linked folder)."""
        ...

    def vector_search(
        self, vector: Sequence[float], *, limit: int, source_ids: Sequence[str]
    ) -> list[Hit]: ...

    def text_search(self, query: str, *, limit: int, source_ids: Sequence[str]) -> list[Hit]: ...

    def count(self) -> int: ...


class IndexRegistry(Protocol):
    """Opens (and caches) the index of each space."""

    def for_space(self, space_id: str) -> SpaceIndex: ...

    def drop(self, space_id: str) -> None:
        """Forget the cached index (the space folder is being deleted)."""
        ...
