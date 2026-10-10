"""Retrieval agreement guard (D-046): chunks both searches rank top 3 survive the reranker."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.base import Hit
from app.index.memory import InMemorySpaceIndex
from app.retrieval.service import (
    RetrievedChunk,
    agreed_ids,
    keep_agreed,
    retrieve,
    select_context,
)
from tests.chat_helpers import KeywordEmbedder, add_chunk, add_source


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return get_engine(db)


def hits(*ids: str) -> list[Hit]:
    return [Hit(chunk_id, "s", 1.0) for chunk_id in ids]


def chunk(cid: str, score: float, tokens: int = 100) -> RetrievedChunk:
    return RetrievedChunk(cid, "s", "T", cid, {}, tokens, score)


class Pinned(InMemorySpaceIndex):
    """Fixed vector and full-text result lists (ids are looked up from the order given)."""

    def __init__(self) -> None:
        super().__init__()
        self.vector: list[str] = []
        self.text: list[str] = []

    def vector_search(
        self, vector: Sequence[float], *, limit: int, source_ids: Sequence[str]
    ) -> list[Hit]:
        return [Hit(cid, source_ids[0], 1.0 / (i + 1)) for i, cid in enumerate(self.vector)]

    def text_search(self, query: str, *, limit: int, source_ids: Sequence[str]) -> list[Hit]:
        return [Hit(cid, source_ids[0], 10.0 / (i + 1)) for i, cid in enumerate(self.text)]


class ByText:
    """A reranker scoring each chunk text from a table (the prose-trained reranker's view)."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        return [self.scores.get(d, 0.0) for d in docs]


def test_agreed_ids_are_the_top_three_of_both_lists_in_vector_order() -> None:
    assert agreed_ids(hits("a", "b", "c", "d"), hits("d", "c", "a")) == ["a", "c"]
    assert agreed_ids(hits("a", "a", "b", "c"), hits("c", "x", "y")) == ["c"]  # repeats count once
    assert agreed_ids(hits("a", "b", "c", "d"), hits("d", "e", "f")) == []  # 4th is not top 3
    assert agreed_ids(hits("a"), []) == []  # full-text search failed


def test_ref_near_08_shape_kept_although_the_reranker_drops_it(engine: Engine) -> None:
    """The Digest MD5 chunk is #1 in vector and full-text search but reranked below the top 8."""
    index = Pinned()
    source = add_source(engine, "requests", source_type="folder")
    digest = add_chunk(
        engine, index, source, "def build_digest_header ... if _algorithm is None: MD5", ordinal=0
    )
    prose = [
        add_chunk(engine, index, source, f"Digest authentication docs page {i}", ordinal=i + 1)
        for i in range(10)
    ]
    index.vector = [digest, *prose]
    index.text = [digest, *reversed(prose)]
    scores = {f"Digest authentication docs page {i}": 0.9 - i * 0.01 for i in range(10)}
    scores["def build_digest_header ... if _algorithm is None: MD5"] = 0.02
    result = retrieve(engine, index, KeywordEmbedder(), ByText(scores), "Digest default algorithm?")
    ids = [c.chunk_id for c in result.chunks]
    assert digest in ids and len(ids) == 8
    assert ids[0] == prose[0]  # the top chunk is never replaced
    assert ids.index(digest) == len(ids) - 1  # guarded chunks join last; rerank order kept
    assert result.ranked[-1].chunk_id == digest  # ranking and refusal scores are unchanged
    assert result.top_score == pytest.approx(0.9)


def test_ref_near_10_shape_vector_first_and_text_second(engine: Engine) -> None:
    index = Pinned()
    source = add_source(engine, "requests")
    codes = add_chunk(engine, index, source, "429: ('too_many_requests', 'too_many')")
    other = [add_chunk(engine, index, source, f"status text {i}", ordinal=i + 1) for i in range(9)]
    index.vector = [codes, *other]
    index.text = [other[0], codes, *other[1:]]
    scores = {f"status text {i}": 0.5 for i in range(9)}
    result = retrieve(engine, index, KeywordEmbedder(), ByText(scores), "HTTP 429 name")
    assert codes in [c.chunk_id for c in result.chunks]


def test_no_agreement_means_the_reranker_alone_decides(engine: Engine) -> None:
    index = Pinned()
    source = add_source(engine, "Doc")
    ids = [add_chunk(engine, index, source, f"text {i}", ordinal=i) for i in range(10)]
    index.vector = ids
    index.text = ids  # both searches agree with the reranker: nothing to put back
    scores = {f"text {i}": 1.0 - i * 0.05 for i in range(10)}
    result = retrieve(engine, index, KeywordEmbedder(), ByText(scores), "text")
    assert [c.chunk_id for c in result.chunks] == ids[:8]


def test_the_top_of_the_fused_list_survives_reranking(engine: Engine) -> None:
    """Full-text #1 and vector #3 put a chunk in the fused top 3; the reranker drops it anyway."""
    index = Pinned()
    source = add_source(engine, "requests", source_type="folder")
    table = add_chunk(engine, index, source, "429: (too_many_requests, too_many)", ordinal=0)
    other = [add_chunk(engine, index, source, f"status text {i}", ordinal=i + 1) for i in range(12)]
    index.vector = [*other[:2], table, *other[2:]]
    index.text = [table, *other]
    scores = {f"status text {i}": 0.9 - i * 0.01 for i in range(12)}
    scores["429: (too_many_requests, too_many)"] = 0.05
    result = retrieve(engine, index, KeywordEmbedder(), ByText(scores), "HTTP 429 name?")
    assert table in [c.chunk_id for c in result.chunks]
    assert result.top_score == pytest.approx(0.9)  # the refusal threshold still sees rerank scores


def test_keep_agreed_respects_the_token_budget_and_protects_the_top_chunk() -> None:
    ranked = [chunk("top", 0.9, 3000), chunk("b", 0.8, 1500), chunk("c", 0.7, 1000)]
    ranked.append(chunk("agreed", 0.1, 2500))
    selected = select_context(ranked)  # top + b + c = 5,500 tokens; agreed doesn't fit
    assert [c.chunk_id for c in selected] == ["top", "b", "c"]
    kept = keep_agreed(selected, ranked, ["agreed"])
    # Dropping only c (1,000) is not enough room; b goes too. The top chunk always stays.
    assert [c.chunk_id for c in kept] == ["top", "agreed"]
    assert sum(c.token_count for c in kept) <= 6000
    too_big = [chunk("top", 0.9, 4000), chunk("huge", 0.1, 2500)]
    assert [c.chunk_id for c in keep_agreed(too_big[:1], too_big, ["huge"])] == ["top"]


def test_keep_agreed_never_replaces_another_agreed_chunk_or_duplicates() -> None:
    ranked = [chunk(f"k{i}", 1.0 - i * 0.1) for i in range(10)]
    selected = select_context(ranked)  # k0..k7
    kept = keep_agreed(selected, ranked, ["k7", "k9", "k8", "k0"])
    ids = [c.chunk_id for c in kept]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert {"k0", "k7", "k8", "k9"} <= set(ids)
    assert ids == sorted(ids, key=lambda cid: int(cid[1:]))  # rerank order
    assert keep_agreed(selected, ranked, ["k1"]) == selected  # already kept
    assert keep_agreed(selected, ranked, ["missing"]) == selected
