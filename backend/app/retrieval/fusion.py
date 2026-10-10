"""Pure ranking helpers: Reciprocal Rank Fusion and the context token budget."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.index.base import Hit

RRF_K = 60
VECTOR_TOP = 40
TEXT_TOP = 40
RERANK_TOP = 40
CONTEXT_CHUNKS = 8
CONTEXT_TOKENS = 6_000


@dataclass(frozen=True)
class Fused:
    """One chunk after fusion. ``best_rank`` is its best 1-based rank in any input list."""

    chunk_id: str
    source_id: str
    score: float
    best_rank: int


def reciprocal_rank_fusion(*rankings: Sequence[Hit], k: int = RRF_K) -> list[Fused]:
    """Fuse ranked lists: ``score = Σ 1 / (k + rank)`` over the lists a chunk appears in.

    Only a chunk's first appearance in each list counts. Ties are broken by the best single rank,
    then by chunk id, so the order is deterministic.
    """
    if k < 0:
        raise ValueError("k must not be negative")
    scores: dict[str, float] = {}
    best: dict[str, int] = {}
    sources: dict[str, str] = {}
    for ranking in rankings:
        seen: set[str] = set()
        rank = 0
        for hit in ranking:
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            rank += 1
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            best[hit.chunk_id] = min(best.get(hit.chunk_id, rank), rank)
            sources.setdefault(hit.chunk_id, hit.source_id)
    fused = [Fused(cid, sources[cid], scores[cid], best[cid]) for cid in scores]
    fused.sort(key=lambda f: (-f.score, f.best_rank, f.chunk_id))
    return fused


def select_within_budget[T](
    items: Sequence[T],
    tokens: Callable[[T], int],
    *,
    max_items: int = CONTEXT_CHUNKS,
    max_tokens: int = CONTEXT_TOKENS,
) -> list[T]:
    """Keep items in order while they fit: at most ``max_items`` and ``max_tokens`` in total.

    An item that would overflow the budget is skipped and smaller later items may still fit.
    The first item is always kept, so an answer never starts without its best chunk.
    """
    chosen: list[T] = []
    used = 0
    for item in items:
        if len(chosen) >= max_items:
            break
        size = max(0, tokens(item))
        if chosen and used + size > max_tokens:
            continue
        chosen.append(item)
        used += size
    return chosen
