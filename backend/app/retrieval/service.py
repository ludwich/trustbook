"""Hybrid retrieval (SPEC §8): enabled sources → vector + full text → RRF → rerank → budget."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlmodel import Session

from app.capabilities.bundled import Embedder, Reranker
from app.index.base import SpaceIndex
from app.retrieval.definitions import definition_extras, fit_extras
from app.retrieval.fusion import (
    CONTEXT_CHUNKS,
    CONTEXT_TOKENS,
    RERANK_TOP,
    TEXT_TOP,
    VECTOR_TOP,
    reciprocal_rank_fusion,
    select_within_budget,
)
from app.retrieval.store import ChunkRow, load_chunks, usable_sources

logger = logging.getLogger("trustbook.retrieval")


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk chosen for the prompt, with its reranker score (0..1, higher is better)."""

    chunk_id: str
    source_id: str
    source_title: str
    text: str
    locator: dict[str, object]
    token_count: int
    score: float
    # Set when the chunk was added because it defines this constant (not ranked in).
    definition_of: str | None = None

    @classmethod
    def from_row(
        cls, row: ChunkRow, score: float, *, definition_of: str | None = None
    ) -> RetrievedChunk:
        return cls(
            chunk_id=row.chunk_id,
            source_id=row.source_id,
            source_title=row.source_title,
            text=row.text,
            locator=dict(row.locator),
            token_count=row.token_count,
            score=score,
            definition_of=definition_of,
        )


@dataclass(frozen=True)
class Retrieval:
    """Result of one retrieval.

    ``chunks``: the context for the prompt (top 8 within the token budget).
    ``ranked``: every reranked candidate, best first. ``source_ids``: the sources searched.
    """

    chunks: list[RetrievedChunk]
    ranked: list[RetrievedChunk] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)

    @property
    def top_score(self) -> float | None:
        return self.ranked[0].score if self.ranked else None


def usable_source_ids(engine: Engine) -> list[str]:
    """Ids of the space's enabled and ready sources."""
    with Session(engine) as session:
        return [source.id for source in usable_sources(session)]


def rerank(query: str, rows: Sequence[ChunkRow], reranker: Reranker) -> list[RetrievedChunk]:
    """Score ``rows`` against ``query``; best first, ties keep the input order."""
    if not rows:
        return []
    scores = reranker.score(query, [row.text for row in rows])
    if len(scores) != len(rows):
        raise RuntimeError("The reranker returned the wrong number of scores")
    order = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), i))
    return [RetrievedChunk.from_row(rows[i], float(scores[i])) for i in order]


def select_context(
    ranked: Sequence[RetrievedChunk],
    *,
    max_chunks: int = CONTEXT_CHUNKS,
    max_tokens: int = CONTEXT_TOKENS,
) -> list[RetrievedChunk]:
    """The best chunks that fit the prompt budget."""
    return select_within_budget(
        ranked, lambda c: c.token_count, max_items=max_chunks, max_tokens=max_tokens
    )


def with_definitions(
    engine: Engine,
    selected: Sequence[RetrievedChunk],
    ranked: Sequence[RetrievedChunk] = (),
    *,
    max_tokens: int = CONTEXT_TOKENS,
) -> list[RetrievedChunk]:
    """``selected`` plus the chunks defining the module-level constants its code refers to.

    See :mod:`app.retrieval.definitions`. A chunk that was also reranked keeps its score;
    otherwise its score is 0.0 (it was not ranked; the refusal check only uses ``ranked``).
    """
    try:
        extras = definition_extras(engine, selected)
    except Exception:
        logger.warning("Looking up constant definitions failed; context unchanged", exc_info=True)
        return list(selected)
    if not extras:
        return list(selected)
    scores = {c.chunk_id: c.score for c in ranked}
    chunks = [
        (
            RetrievedChunk.from_row(e.row, scores.get(e.row.chunk_id, 0.0), definition_of=e.name),
            e.referrer,
        )
        for e in extras
    ]
    return fit_extras(selected, chunks, max_tokens=max_tokens)


def candidates(
    engine: Engine,
    index: SpaceIndex,
    embedder: Embedder,
    query: str,
    source_ids: Sequence[str],
) -> list[ChunkRow]:
    """Vector top 40 + full-text top 40, fused with RRF (k = 60), cut to the top 40."""
    if not source_ids:
        return []
    vectors = embedder.embed_queries([query])
    if len(vectors) != 1:
        raise RuntimeError("The embedding model returned the wrong number of vectors")
    vector_hits = index.vector_search(vectors[0], limit=VECTOR_TOP, source_ids=source_ids)
    try:
        text_hits = index.text_search(query, limit=TEXT_TOP, source_ids=source_ids)
    except Exception:
        # A query the full-text engine cannot parse must not cost the user the answer;
        # vector hits still go through the same filter, reranker and refusal threshold.
        logger.warning("Full-text search failed; using vector results only", exc_info=True)
        text_hits = []
    fused = reciprocal_rank_fusion(vector_hits, text_hits)
    rows = load_chunks(engine, [f.chunk_id for f in fused], source_ids)
    ordered = [rows[f.chunk_id] for f in fused if f.chunk_id in rows]
    missing = len(fused) - len(ordered)
    if missing:
        logger.warning("%d indexed chunk(s) have no row in space.db; skipped", missing)
    return ordered[:RERANK_TOP]


def retrieve(
    engine: Engine,
    index: SpaceIndex,
    embedder: Embedder,
    reranker: Reranker,
    query: str,
    *,
    source_ids: Sequence[str] | None = None,
) -> Retrieval:
    """Run the full retrieval for ``query`` over the enabled, ready sources of a space."""
    allowed = list(source_ids) if source_ids is not None else usable_source_ids(engine)
    if not allowed:
        return Retrieval(chunks=[], ranked=[], source_ids=[])
    rows = candidates(engine, index, embedder, query, allowed)
    ranked = rerank(query, rows, reranker)
    chunks = with_definitions(engine, select_context(ranked), ranked)
    return Retrieval(chunks=chunks, ranked=ranked, source_ids=allowed)
