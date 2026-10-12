"""Deterministic tie-breaking (D-059): equal scores are ordered by content, never by write order or
uuid, so a re-indexed space gives the same candidates, fused order and context."""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.base import Hit, IndexedChunk, SpaceIndex, stable_top, tie_key
from app.index.lance import LanceSpaceIndex
from app.index.memory import InMemorySpaceIndex
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.service import rerank, retrieve
from app.retrieval.store import ChunkRow
from tests.chat_helpers import KeywordEmbedder, OverlapReranker, add_chunk, add_source

TOP = 40
SOURCE = "s1"


def test_tie_key_depends_only_on_the_text() -> None:
    assert tie_key("abc") == tie_key("abc")
    assert tie_key("abc") != tie_key("abd")
    assert len(tie_key("")) == 64


def test_stable_top_orders_ties_by_key_whatever_the_engine_returns() -> None:
    hits = [Hit(f"id{i}", SOURCE, 1.0 if i < 30 else 0.5, tie_key(f"text {i}")) for i in range(40)]
    expected = sorted(hits[:30], key=lambda h: h.key)[:5]
    calls: list[int] = []

    def engine(seed: int) -> Callable[[int], list[Hit]]:
        order = list(hits)
        random.Random(seed).shuffle(order)
        order.sort(key=lambda h: -h.score)  # the engine sorts by score, ties in any order

        def fetch(n: int) -> list[Hit]:
            calls.append(n)
            return order[:n]

        return fetch

    for seed in range(5):
        assert stable_top(engine(seed), 5) == expected
    # 10 fetched hits all tie at the cut, so the fetch doubles until it reaches a lower score.
    assert calls[:3] == [10, 20, 40]
    assert stable_top(engine(0), 0) == []


def test_stable_top_single_fetch_when_the_cut_is_not_a_tie() -> None:
    hits = [Hit(f"id{i}", SOURCE, float(100 - i), tie_key(str(i))) for i in range(50)]
    calls: list[int] = []

    def fetch(n: int) -> list[Hit]:
        calls.append(n)
        return hits[:n]

    assert [h.chunk_id for h in stable_top(fetch, 3)] == ["id0", "id1", "id2"]
    assert calls == [6]


def test_rrf_ties_are_broken_by_the_content_key_not_the_chunk_id() -> None:
    # A vector-only rank r always ties with a full-text-only rank r.
    a = Hit("zzz", SOURCE, 0.9, tie_key("alpha"))
    b = Hit("aaa", SOURCE, 7.0, tie_key("beta"))
    first = min((a, b), key=lambda h: h.key).chunk_id
    assert reciprocal_rank_fusion([a], [b])[0].chunk_id == first
    # Same texts under swapped (fresh uuid) ids: the order follows the texts.
    a2 = Hit("aaa", SOURCE, 0.9, tie_key("alpha"))
    b2 = Hit("zzz", SOURCE, 7.0, tie_key("beta"))
    fused = reciprocal_rank_fusion([a2], [b2])
    assert [f.key for f in fused] == [min(a.key, b.key), max(a.key, b.key)]
    # Without keys the chunk id still makes the order deterministic.
    plain = reciprocal_rank_fusion([Hit("y", SOURCE, 1.0)], [Hit("x", SOURCE, 1.0)])
    assert [f.chunk_id for f in plain] == ["x", "y"]


def test_rerank_ties_keep_the_fused_order() -> None:
    rows = [ChunkRow(f"c{i}", SOURCE, "T", i, f"text {i}", {}, 10) for i in range(4)]

    class Flat:
        def score(self, query: str, docs: Sequence[str]) -> list[float]:
            return [0.5, 0.7, 0.5, 0.5]

    assert [c.chunk_id for c in rerank("q", rows, Flat())] == ["c1", "c0", "c2", "c3"]


# --- rebuilt indexes -------------------------------------------------------------------------


def corpus() -> list[tuple[str, list[float]]]:
    """120 chunks with heavy ties: equal-length texts sharing the query terms (equal BM25 scores)
    and groups of ten with the same vector (equal cosine scores)."""
    items = []
    for i in range(120):
        text = f"valve seal torque note w{i:03d}x"
        group = i % 12
        vector = [1.0, group / 12.0, 0.0, (group % 3) / 3.0]
        items.append((text, vector))
    return items


def build(index: SpaceIndex, order: Sequence[int], *, batch: int = 25) -> dict[str, str]:
    """Write the corpus in ``order`` under fresh uuid ids, in batches; returns id → text."""
    items = corpus()
    text_of: dict[str, str] = {}
    pending: list[tuple[IndexedChunk, list[float]]] = []
    for i in order:
        text, vector = items[i]
        chunk_id = uuid.uuid4().hex
        text_of[chunk_id] = text
        pending.append((IndexedChunk(chunk_id, SOURCE, text), vector))
        if len(pending) == batch:
            index.append([c for c, _ in pending], [v for _, v in pending])
            pending = []
    if pending:
        index.append([c for c, _ in pending], [v for _, v in pending])
    index.refresh_text_index()
    return text_of


def orders() -> list[list[int]]:
    natural = list(range(120))
    shuffled = list(natural)
    random.Random(7).shuffle(shuffled)
    return [natural, natural[::-1], shuffled]


def top_lists(index: SpaceIndex, text_of: dict[str, str]) -> tuple[list[str], ...]:
    query_vector = [1.0, 0.5, 0.0, 0.3]
    vector = index.vector_search(query_vector, limit=TOP, source_ids=[SOURCE])
    text = index.text_search("valve seal", limit=TOP, source_ids=[SOURCE])
    fused = reciprocal_rank_fusion(vector, text)[:TOP]
    return (
        [text_of[h.chunk_id] for h in vector],
        [text_of[h.chunk_id] for h in text],
        [text_of[f.chunk_id] for f in fused],
    )


@pytest.mark.parametrize("kind", ["lance", "memory"])
def test_rebuilt_indexes_in_other_write_orders_give_identical_top_40(
    tmp_path: Path, kind: str
) -> None:
    results = []
    for n, order in enumerate(orders()):
        index: SpaceIndex = (
            LanceSpaceIndex(tmp_path / f"vectors{n}") if kind == "lance" else InMemorySpaceIndex()
        )
        text_of = build(index, order)
        lists = top_lists(index, text_of)
        assert all(len(found) == TOP for found in lists)
        results.append(lists)
        results.append(top_lists(index, text_of))  # the same index searched again
    assert all(r == results[0] for r in results[1:])
    vector, text, _ = results[0]
    # The test means something only if ties cross the cut: 120 equal BM25 scores for 40 places.
    assert len(set(text)) == TOP and len(set(vector)) == TOP


@pytest.fixture
def engine_factory(tmp_path: Path) -> Callable[[], Engine]:
    count = 0

    def make() -> Engine:
        nonlocal count
        count += 1
        db = tmp_path / f"space{count}.db"
        migrate(db, "space")
        return get_engine(db)

    return make


def test_full_retrieval_is_identical_after_a_reindex_in_another_order(
    engine_factory: Callable[[], Engine],
) -> None:
    texts = [
        f"Pump {w} maintenance: check the seal and the valve, entry {i}."
        for i, w in enumerate(["alpha", "beta", "gamma", "delta", "omega", "kappa"] * 10)
    ]
    contexts = []
    for order in (list(range(len(texts))), list(reversed(range(len(texts))))):
        engine = engine_factory()
        index = InMemorySpaceIndex()
        source = add_source(engine, "Manual")
        for ordinal in order:
            add_chunk(engine, index, source, texts[ordinal], ordinal=ordinal)
        found = retrieve(
            engine,
            index,
            KeywordEmbedder(),
            OverlapReranker(),
            "How do I check the pump seal and valve?",
        )
        contexts.append(([c.text for c in found.ranked], [c.text for c in found.chunks]))
    assert contexts[0] == contexts[1]
    assert len(contexts[0][0]) == TOP
