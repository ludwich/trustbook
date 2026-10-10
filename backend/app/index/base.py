"""The index interface shared by ingestion (writes) and retrieval (reads)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source_id: str
    text: str


@dataclass(frozen=True)
class Hit:
    """One search result. ``score``: higher is better (cosine similarity or BM25)."""

    chunk_id: str
    source_id: str
    score: float


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
