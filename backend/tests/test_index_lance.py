from __future__ import annotations

from pathlib import Path

import pytest

from app.index.base import IndexedChunk
from app.index.lance import IndexDimensionError, LanceIndexRegistry, LanceSpaceIndex


def _chunks() -> tuple[list[IndexedChunk], list[list[float]]]:
    chunks = [
        IndexedChunk("c1", "s1", "Tighten the valve to 40 Nm before the pressure test."),
        IndexedChunk("c2", "s1", "Inspect the seals every six months."),
        IndexedChunk("c3", "s2", "Invoices are approved by the finance team."),
    ]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.8, 0.6, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    return chunks, vectors


def test_empty_index_searches_return_nothing(tmp_path: Path) -> None:
    index = LanceSpaceIndex(tmp_path / "vectors")
    assert index.count() == 0
    assert index.vector_search([1, 0, 0, 0], limit=5, source_ids=["s1"]) == []
    assert index.text_search("valve", limit=5, source_ids=["s1"]) == []
    index.delete_source("s1")  # no table yet: no error


def test_vector_and_text_search_filtered_by_source(tmp_path: Path) -> None:
    index = LanceSpaceIndex(tmp_path / "vectors")
    chunks, vectors = _chunks()
    index.add(chunks, vectors)
    assert index.count() == 3

    hits = index.vector_search([1.0, 0.0, 0.0, 0.0], limit=5, source_ids=["s1", "s2"])
    assert [h.chunk_id for h in hits] == ["c1", "c2", "c3"]
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    assert hits[1].score == pytest.approx(0.8, abs=1e-5)

    only_s2 = index.vector_search([1.0, 0.0, 0.0, 0.0], limit=5, source_ids=["s2"])
    assert [h.chunk_id for h in only_s2] == ["c3"]
    assert index.vector_search([1.0, 0, 0, 0], limit=5, source_ids=[]) == []
    assert index.vector_search([1.0, 0, 0, 0], limit=5, source_ids=["x' OR '1'='1"]) == []

    text = index.text_search("valve pressure", limit=5, source_ids=["s1", "s2"])
    assert [h.chunk_id for h in text] == ["c1"]
    assert text[0].score > 0
    assert index.text_search("valve", limit=5, source_ids=["s2"]) == []
    assert index.text_search("what's `valve()`?", limit=5, source_ids=["s1"])[0].chunk_id == "c1"


def test_delete_source_and_reopen(tmp_path: Path) -> None:
    folder = tmp_path / "vectors"
    index = LanceSpaceIndex(folder)
    chunks, vectors = _chunks()
    index.add(chunks, vectors)
    index.delete_source("s1")
    assert index.count() == 1
    assert index.text_search("valve", limit=5, source_ids=["s1"]) == []

    reopened = LanceSpaceIndex(folder)
    assert reopened.count() == 1
    assert reopened.dim == 4
    reopened.add([IndexedChunk("c4", "s3", "New valve notes")], [[1.0, 0.0, 0.0, 0.0]])
    assert [h.chunk_id for h in reopened.text_search("valve", limit=5, source_ids=["s3"])] == ["c4"]


def test_dimension_mismatch_is_refused(tmp_path: Path) -> None:
    index = LanceSpaceIndex(tmp_path / "vectors")
    chunks, vectors = _chunks()
    index.add(chunks, vectors)
    with pytest.raises(IndexDimensionError):
        index.add([IndexedChunk("c9", "s9", "x")], [[1.0, 0.0]])
    assert index.vector_search([1.0, 0.0], limit=5, source_ids=["s1"]) == []
    with pytest.raises(ValueError):
        index.add(chunks[:1], [])


def test_registry_caches_and_drops(tmp_path: Path) -> None:
    registry = LanceIndexRegistry(lambda space_id: tmp_path / space_id / "vectors")
    first = registry.for_space("a")
    assert registry.for_space("a") is first
    registry.drop("a")
    assert registry.for_space("a") is not first


def test_append_in_batches_then_refresh_text_index_once(tmp_path: Path) -> None:
    index = LanceSpaceIndex(tmp_path / "vectors")
    chunks, vectors = _chunks()
    index.append(chunks[:2], vectors[:2])
    index.append(chunks[2:], vectors[2:])
    index.refresh_text_index()
    assert index.count() == 3
    assert [h.chunk_id for h in index.text_search("valve", limit=5, source_ids=["s1"])] == ["c1"]
    assert [h.chunk_id for h in index.text_search("finance", limit=5, source_ids=["s2"])] == ["c3"]
    with pytest.raises(IndexDimensionError):
        index.append([IndexedChunk("c4", "s3", "x")], [[1.0, 0.0]])
    LanceSpaceIndex(tmp_path / "empty").refresh_text_index()  # nothing indexed yet: no error
