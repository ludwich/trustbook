from __future__ import annotations

import json

import pytest

from app.chat.citations import number_citations, sources_used
from app.chat.output import (
    DraftSentence,
    InvalidOutputError,
    extract_json,
    parse_answer,
    parse_overview,
)
from app.retrieval.service import RetrievedChunk
from tests.chat_helpers import answer_json

VALID = {"c1", "c2", "c3"}


def test_extract_json_accepts_plain_fenced_and_wrapped_objects() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Here you go: {"a": 3} hope it helps') == {"a": 3}
    for bad in ("", "   ", "no json here", "{broken"):
        with pytest.raises(InvalidOutputError):
            extract_json(bad)


def test_parse_answer_keeps_cited_sentences() -> None:
    parsed = parse_answer(answer_json(("One.", ["c1"]), ("Two.", ["c2", "c1", "c2"])), VALID)
    assert parsed.sentences == [DraftSentence("One.", ("c1",)), DraftSentence("Two.", ("c2", "c1"))]
    assert parsed.dropped == [] and parsed.returned == 2


def test_parse_answer_drops_sentences_without_valid_ids() -> None:
    raw = json.dumps(
        {
            "sentences": [
                {"text": "No ids.", "chunk_ids": []},
                {"text": "Unknown id.", "chunk_ids": ["c9"]},
                {"text": "Mixed ids.", "chunk_ids": ["c1", "c99"]},
                {"text": "Missing key."},
                {"text": "Wrong type.", "chunk_ids": [1]},
                "not an object",
                {"text": "   ", "chunk_ids": ["c1"]},
                {"text": "Kept.", "chunk_ids": ["c3"]},
            ]
        }
    )
    parsed = parse_answer(raw, VALID)
    assert parsed.sentences == [DraftSentence("Kept.", ("c3",))]
    assert parsed.dropped == [
        "No ids.",
        "Unknown id.",
        "Mixed ids.",
        "Missing key.",
        "Wrong type.",
        "not an object",
    ]


def test_parse_answer_normalises_id_formatting_only() -> None:
    raw = answer_json(("A.", [" [C1] "]), ("B.", ["c2, c3"]), ("C.", "c1"))  # type: ignore[arg-type]
    parsed = parse_answer(raw, VALID)
    assert [s.chunk_ids for s in parsed.sentences] == [("c1",), ("c2", "c3"), ("c1",)]
    # A chunk *id* from the database is not a prompt label and is not accepted.
    assert parse_answer(answer_json(("D.", ["5f2a"])), VALID).sentences == []


def test_parse_answer_rejects_wrong_shapes() -> None:
    for raw in ('{"answer": "x"}', '{"sentences": "x"}', "[1, 2]"):
        with pytest.raises(InvalidOutputError):
            parse_answer(raw, VALID)
    empty = parse_answer('{"sentences": []}', VALID)
    assert (empty.sentences, empty.dropped, empty.returned) == ([], [], 0)


def test_parse_overview_requires_three_questions() -> None:
    good = json.dumps(
        {
            "sentences": [{"text": "About X.", "chunk_ids": ["c1"]}],
            "questions": ["Q1?", "Q2?", " Q2? ", "Q3?", "Q4?"],
        }
    )
    parsed = parse_overview(good, VALID)
    assert parsed.questions == ["Q1?", "Q2?", "Q3?"]
    assert parsed.answer.sentences == [DraftSentence("About X.", ("c1",))]
    for bad in (
        {"sentences": [], "questions": ["Q1?", "Q2?"]},
        {"sentences": [], "questions": ["Q1?", "Q1?", ""]},
        {"sentences": []},
    ):
        with pytest.raises(InvalidOutputError):
            parse_overview(json.dumps(bad), VALID)


def chunk(cid: str, source: str) -> RetrievedChunk:
    return RetrievedChunk(cid, source, f"Title {source}", "text", {"pages": [1]}, 10, 0.5)


def test_citations_are_numbered_by_first_use() -> None:
    chunks = {"c1": chunk("k1", "s1"), "c2": chunk("k2", "s2"), "c3": chunk("k3", "s1")}
    sentences = number_citations(
        [
            DraftSentence("First.", ("c3",)),
            DraftSentence("Second.", ("c1", "c3")),
            DraftSentence("Third.", ("c2", "c1")),
        ],
        chunks,
    )
    assert [[(c.n, c.chunk_id) for c in s.citations] for s in sentences] == [
        [(1, "k3")],
        [(2, "k1"), (1, "k3")],
        [(3, "k2"), (2, "k1")],
    ]
    first = sentences[0].citations[0]
    assert (first.source_id, first.source_title, first.locator) == (
        "s1",
        "Title s1",
        {"pages": [1]},
    )
    assert [(s.source_id, s.title) for s in sources_used(sentences)] == [
        ("s1", "Title s1"),
        ("s2", "Title s2"),
    ]
    with pytest.raises(KeyError):
        number_citations([DraftSentence("X.", ("c9",))], chunks)
