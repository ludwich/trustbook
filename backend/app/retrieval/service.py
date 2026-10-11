"""Hybrid retrieval (SPEC §8): enabled sources → vector + full text → RRF → rerank → budget.

Agreement guard (D-046): a chunk ranked in the top :data:`AGREEMENT_TOP` of **both** the vector
and the full-text lists stays in the context even if the reranker dropped it. The reranker was
trained on prose and sometimes ranks the one code chunk that answers a question below the
context cut, although both searches put it first. The kept chunk replaces the lowest-ranked kept
chunk and the token budget still holds. The refusal threshold still uses the rerank scores.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from sqlalchemy import Engine
from sqlmodel import Session

from app.capabilities.bundled import Embedder, Reranker
from app.index.base import Hit, SpaceIndex
from app.retrieval.callees import callee_extras
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

AGREEMENT_TOP = 3
FUSED_TOP = 3  # the top of the RRF list is guarded too


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
    # Set when the chunk was added because it defines this constant (D-032) or this function the
    # context's code calls (D-057), not because it was ranked into the context.
    definition_of: str | None = None
    # The id of the context chunk such an added chunk was found for (it refers to or calls it).
    referrer: str | None = None

    @classmethod
    def from_row(
        cls,
        row: ChunkRow,
        score: float,
        *,
        definition_of: str | None = None,
        referrer: str | None = None,
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
            referrer=referrer,
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
    """Score ``rows`` against ``query``; best first, ties keep the input order.

    The input is the fused list, whose order is itself fixed by scores and content keys (D-056),
    so equal rerank scores (common with a BF16 reranker) are ordered the same on every run,
    re-index and machine. The fused rank is a better tie-breaker than a hash: it is evidence.
    """
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
            RetrievedChunk.from_row(
                e.row, scores.get(e.row.chunk_id, 0.0), definition_of=e.name, referrer=e.referrer
            ),
            e.referrer,
        )
        for e in extras
    ]
    return fit_extras(selected, chunks, max_tokens=max_tokens)


def with_callees(
    selected: Sequence[RetrievedChunk],
    ranked: Sequence[RetrievedChunk],
    *,
    max_tokens: int = CONTEXT_TOKENS,
) -> list[RetrievedChunk]:
    """``selected`` plus the reranked chunks defining functions its code calls (D-057).

    See :mod:`app.retrieval.callees`. At most two, within ``max_tokens``; the top chunk, the
    calling chunks, the constants added before and the chunks those constants were added for are
    never displaced. Each keeps its rerank score.
    """
    try:
        extras = callee_extras(selected, ranked)
    except Exception:
        logger.warning("Looking up called functions failed; context unchanged", exc_info=True)
        return list(selected)
    if not extras:
        return list(selected)
    for chunk, name, callers in extras:
        logger.info(
            "Callee: added %s (chunk %s) for %d context chunk(s)",
            name,
            chunk.chunk_id,
            len(callers),
        )
    chunks = [
        (replace(chunk, definition_of=name, referrer=callers[0]), callers[0])
        for chunk, name, callers in extras
    ]
    # Constants added before, the chunks they were added for (D-032: an extra is never left
    # without its referrer) and every calling chunk stay: a body is read through its call site.
    added = [c for c in selected if c.definition_of is not None]
    keep = [c.chunk_id for c in added] + [c.referrer for c in added if c.referrer is not None]
    keep += [cid for _, _, callers in extras for cid in callers]
    return fit_extras(selected, chunks, max_tokens=max_tokens, protect=keep)


def _top_ids(hits: Sequence[Hit], n: int) -> list[str]:
    found: list[str] = []
    for hit in hits:
        if hit.chunk_id not in found:
            found.append(hit.chunk_id)
        if len(found) == n:
            break
    return found


def agreed_ids(
    vector_hits: Sequence[Hit], text_hits: Sequence[Hit], *, top: int = AGREEMENT_TOP
) -> list[str]:
    """Chunk ids in the top ``top`` of both lists, in vector order (D-046)."""
    text_top = set(_top_ids(text_hits, top))
    return [cid for cid in _top_ids(vector_hits, top) if cid in text_top]


@dataclass(frozen=True)
class Candidates:
    """The fused candidates (best first, at most 40) and the ids both searches ranked top 3."""

    rows: list[ChunkRow]
    agreed: list[str] = field(default_factory=list)


def search(
    engine: Engine,
    index: SpaceIndex,
    embedder: Embedder,
    query: str,
    source_ids: Sequence[str],
) -> Candidates:
    """Vector top 40 + full-text top 40, fused with RRF (k = 60), cut to the top 40."""
    if not source_ids:
        return Candidates([])
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
    # Kept even if the reranker drops them: chunks both searches rank top 3, and the top of the
    # fused list (D-046; the prose reranker under-rates code chunks).
    guarded = agreed_ids(vector_hits, text_hits) + [f.chunk_id for f in fused[:FUSED_TOP]]
    agreed = [cid for cid in dict.fromkeys(guarded) if cid in rows]
    return Candidates(ordered[:RERANK_TOP], agreed)


def candidates(
    engine: Engine,
    index: SpaceIndex,
    embedder: Embedder,
    query: str,
    source_ids: Sequence[str],
) -> list[ChunkRow]:
    """The fused candidates of :func:`search` (best first, at most 40)."""
    return search(engine, index, embedder, query, source_ids).rows


def keep_agreed(
    selected: Sequence[RetrievedChunk],
    ranked: Sequence[RetrievedChunk],
    agreed: Sequence[str],
    *,
    max_chunks: int = CONTEXT_CHUNKS,
    max_tokens: int = CONTEXT_TOKENS,
) -> list[RetrievedChunk]:
    """``selected`` with every agreed chunk the reranker left out put back (D-046).

    Each one replaces the lowest-ranked kept chunk, more than one if that is what the token
    budget needs. The top chunk and other agreed chunks are never replaced; an agreed chunk that
    still does not fit is left out. The result keeps the rerank order.
    """
    position = {c.chunk_id: i for i, c in enumerate(ranked)}
    by_id = {c.chunk_id: c for c in ranked}
    result = list(selected)
    protected = set(agreed)
    for chunk_id in agreed:
        chunk = by_id.get(chunk_id)
        if chunk is None or any(c.chunk_id == chunk_id for c in result):
            continue
        trial = list(result)
        size = max(0, chunk.token_count)

        def over(chosen: list[RetrievedChunk], size: int = size) -> bool:
            used = sum(max(0, c.token_count) for c in chosen)
            return len(chosen) >= max_chunks or used + size > max_tokens

        while trial and over(trial):
            victims = [i for i, c in enumerate(trial) if i > 0 and c.chunk_id not in protected]
            if not victims:
                break
            trial.pop(max(victims, key=lambda i: position.get(trial[i].chunk_id, len(position))))
        if trial and over(trial):
            logger.info("Agreement guard: chunk %s does not fit the context budget", chunk_id)
            continue
        trial.append(chunk)
        trial.sort(key=lambda c: position.get(c.chunk_id, len(position)))
        logger.info(
            "Agreement guard: kept chunk %s (reranked #%d)", chunk_id, position[chunk_id] + 1
        )
        result = trial
    return result


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
    found = search(engine, index, embedder, query, allowed)
    ranked = rerank(query, found.rows, reranker)
    chosen = keep_agreed(select_context(ranked), ranked, found.agreed)
    chunks = with_callees(with_definitions(engine, chosen, ranked), ranked)
    return Retrieval(chunks=chunks, ranked=ranked, source_ids=allowed)
