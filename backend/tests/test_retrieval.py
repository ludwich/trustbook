from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.base import Hit
from app.index.memory import InMemorySpaceIndex
from app.retrieval.fusion import (
    RRF_K,
    reciprocal_rank_fusion,
    select_within_budget,
)
from app.retrieval.service import RetrievedChunk, rerank, retrieve, select_context
from app.retrieval.store import ChunkRow, first_chunks, parse_locator, source_counts
from tests.chat_helpers import KeywordEmbedder, OverlapReranker, add_chunk, add_source


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return get_engine(db)


def hits(*ids: str) -> list[Hit]:
    return [Hit(chunk_id, "s", 1.0) for chunk_id in ids]


# --- RRF -----------------------------------------------------------------------------------


def test_rrf_scores_and_order() -> None:
    fused = reciprocal_rank_fusion(hits("a", "b", "c"), hits("c", "a", "d"))
    assert [f.chunk_id for f in fused] == ["a", "c", "b", "d"]
    by_id = {f.chunk_id: f for f in fused}
    assert by_id["a"].score == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert by_id["c"].score == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert by_id["d"].score == pytest.approx(1 / (RRF_K + 3))
    assert (by_id["a"].best_rank, by_id["d"].best_rank) == (1, 3)


def test_rrf_ties_break_on_best_rank_then_id() -> None:
    # x and y both get 1/(k+1) + 1/(k+2): same score, same best rank → id order.
    fused = reciprocal_rank_fusion(hits("y", "x"), hits("x", "y"))
    assert [f.chunk_id for f in fused] == ["x", "y"]
    assert fused[0].score == fused[1].score
    # Equal single-list scores: the one ranked first anywhere wins over alphabetical order.
    fused = reciprocal_rank_fusion(hits("z", "b"), hits("a"))
    assert [f.chunk_id for f in fused] == ["a", "z", "b"]


def test_rrf_counts_a_chunk_once_per_list_and_handles_empty() -> None:
    fused = reciprocal_rank_fusion(hits("a", "a", "b"))
    assert [f.chunk_id for f in fused] == ["a", "b"]
    assert fused[1].score == pytest.approx(1 / (RRF_K + 2))
    assert reciprocal_rank_fusion() == []
    assert reciprocal_rank_fusion([], []) == []
    with pytest.raises(ValueError, match="negative"):
        reciprocal_rank_fusion(hits("a"), k=-1)


# --- budget --------------------------------------------------------------------------------


def test_budget_keeps_eight_at_most() -> None:
    assert select_within_budget(list(range(20)), lambda _i: 10) == list(range(8))


def test_budget_skips_items_that_do_not_fit() -> None:
    sizes = {"a": 3000, "b": 2500, "c": 1000, "d": 400, "e": 100}
    chosen = select_within_budget(list(sizes), sizes.__getitem__, max_tokens=6000)
    assert chosen == ["a", "b", "d", "e"]  # c would exceed 6,000 tokens
    assert sum(sizes[c] for c in chosen) <= 6000


def test_budget_always_keeps_the_best_item() -> None:
    assert select_within_budget(["huge", "small"], {"huge": 9000, "small": 5}.__getitem__) == [
        "huge"
    ]
    assert select_within_budget([], lambda _i: 1) == []


# --- store and retrieval -------------------------------------------------------------------


def test_source_counts_and_enabled_ready_filter(engine: Engine) -> None:
    add_source(engine, "ready on")
    add_source(engine, "ready off", enabled=False)
    add_source(engine, "indexing", status="indexing")
    add_source(engine, "failed", status="failed")
    with Session(engine) as session:
        counts = source_counts(session)
    assert (counts.total, counts.ready, counts.enabled_ready) == (4, 2, 1)


def test_retrieve_only_searches_enabled_ready_sources(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    on = add_source(engine, "Guide")
    off = add_source(engine, "Unticked", enabled=False)
    busy = add_source(engine, "Busy", status="indexing")
    wanted = add_chunk(engine, index, on, "Timeouts are set with the timeout parameter.")
    add_chunk(engine, index, off, "Timeouts timeout parameter everywhere.")
    add_chunk(engine, index, busy, "Timeouts timeout parameter again.")
    result = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), "How do timeouts work?")
    assert result.source_ids == [on]
    assert [c.chunk_id for c in result.chunks] == [wanted]
    assert {c.source_id for c in result.ranked} == {on}
    chunk = result.chunks[0]
    assert (chunk.source_title, chunk.text) == (
        "Guide",
        "Timeouts are set with the timeout parameter.",
    )
    assert result.top_score == pytest.approx(0.5)  # "timeouts" of {timeouts, work}


def test_retrieve_without_usable_sources_does_no_work(engine: Engine) -> None:
    add_source(engine, "Unticked", enabled=False)
    embedder, reranker = KeywordEmbedder(), OverlapReranker()
    result = retrieve(engine, InMemorySpaceIndex(), embedder, reranker, "anything")
    assert (result.chunks, result.source_ids, result.top_score) == ([], [], None)
    assert embedder.queries == [] and reranker.calls == []


def test_retrieve_reranks_and_applies_budget(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "Doc")
    best = add_chunk(engine, index, source, "retry backoff policy", ordinal=0, token_count=5000)
    add_chunk(engine, index, source, "retry policy", ordinal=1, token_count=2000)
    small = add_chunk(engine, index, source, "retry", ordinal=2, token_count=500)
    for i in range(3, 15):
        add_chunk(engine, index, source, f"unrelated text number {i}", ordinal=i, token_count=10)
    result = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), "retry backoff policy")
    assert result.ranked[0].chunk_id == best
    ids = [c.chunk_id for c in result.chunks]
    assert ids[0] == best and small in ids  # the 2,000-token chunk does not fit next to 5,000
    assert sum(c.token_count for c in result.chunks) <= 6000
    assert len(result.chunks) <= 8


def test_retrieve_skips_index_entries_without_a_row(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "Doc")
    add_chunk(engine, index, source, "ghost retry text", in_db=False)
    real = add_chunk(engine, index, source, "retry text", ordinal=1)
    result = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), "retry")
    assert [c.chunk_id for c in result.ranked] == [real]


def _chunk(cid: str, score: float = 0.0, tokens: int = 10) -> RetrievedChunk:
    return RetrievedChunk(cid, "s", "T", "text", {}, tokens, score)


def test_rerank_orders_by_score_and_keeps_ties_stable() -> None:
    rows = [
        ChunkRow("a", "s", "T", 0, "alpha", {}, 1),
        ChunkRow("b", "s", "T", 1, "beta gamma", {}, 1),
        ChunkRow("c", "s", "T", 2, "gamma", {}, 1),
    ]
    ranked = rerank("gamma", rows, OverlapReranker())
    assert [c.chunk_id for c in ranked] == ["b", "c", "a"]
    assert rerank("gamma", [], OverlapReranker()) == []
    assert [c.chunk_id for c in select_context([_chunk(str(i)) for i in range(10)])] == [
        str(i) for i in range(8)
    ]


def test_first_chunks_and_locator_parsing(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    a = add_source(engine, "A")
    b = add_source(engine, "B")
    add_chunk(engine, index, a, "a second", ordinal=1)
    first_a = add_chunk(engine, index, a, "a first", ordinal=0, locator={"pages": [1]})
    first_b = add_chunk(engine, index, b, "b only", ordinal=3)
    rows = first_chunks(engine, [b, a])
    assert [r.chunk_id for r in rows] == [first_b, first_a]
    assert rows[1].locator == {"pages": [1]}
    assert parse_locator("not json") == {} and parse_locator("[1]") == {}


def test_failing_text_search_falls_back_to_vector_hits(engine: Engine) -> None:
    class BrokenText(InMemorySpaceIndex):
        def text_search(self, query: str, *, limit: int, source_ids: Sequence[str]) -> list[Hit]:
            raise RuntimeError("syntax error in FTS query")

    index = BrokenText()
    source = add_source(engine, "Doc")
    wanted = add_chunk(engine, index, source, "retry policy text")
    result = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), 'retry "policy')
    assert [c.chunk_id for c in result.chunks] == [wanted]
