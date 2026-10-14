from __future__ import annotations

import json
import re

import pytest

from app.chat.citations import number_citations, sources_used
from app.chat.output import (
    DraftSentence,
    InvalidOutputError,
    extract_json,
    is_syntax_fragment,
    parse_answer,
    parse_overview,
    split_inline_citations,
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


# --- inline citation markers (several sentences packed into one "text") ----------------------

SMOKE_TEXT = (
    'The example uses a total of 3 and a backoff_factor of 0.1 ["c1", "c2"]. When max_retries is '
    "left at its default, HTTPAdapter uses the DEFAULT_RETRIES constant, which is set to 0 "
    '["c9"]. As a result, failed connections are not retried.'
)


def test_packed_sentences_are_split_at_inline_markers() -> None:
    raw = json.dumps({"sentences": [{"text": SMOKE_TEXT, "chunk_ids": ["c3"]}]})
    parsed = parse_answer(raw, {"c1", "c2", "c3", "c9"})
    assert [(s.text, s.chunk_ids) for s in parsed.sentences] == [
        ("The example uses a total of 3 and a backoff_factor of 0.1.", ("c1", "c2")),
        (
            "When max_retries is left at its default, HTTPAdapter uses the DEFAULT_RETRIES "
            "constant, which is set to 0.",
            ("c9",),
        ),
        ("As a result, failed connections are not retried.", ("c3",)),
    ]
    assert parsed.dropped == [] and parsed.returned == 1


def test_a_segment_with_an_unknown_marker_id_is_dropped_alone() -> None:
    raw = json.dumps({"sentences": [{"text": SMOKE_TEXT, "chunk_ids": ["c3"]}]})
    parsed = parse_answer(raw, {"c1", "c2", "c3"})  # c9 was not in the prompt
    assert [s.chunk_ids for s in parsed.sentences] == [("c1", "c2"), ("c3",)]
    assert parsed.dropped == [
        "When max_retries is left at its default, HTTPAdapter uses the DEFAULT_RETRIES "
        "constant, which is set to 0."
    ]


def test_trailing_text_without_declared_ids_is_dropped() -> None:
    raw = json.dumps({"sentences": [{"text": "One [c1]. Two without ids.", "chunk_ids": []}]})
    parsed = parse_answer(raw, VALID)
    assert [(s.text, s.chunk_ids) for s in parsed.sentences] == [("One.", ("c1",))]
    assert parsed.dropped == ["Two without ids."]


@pytest.mark.parametrize(
    ("text", "expected", "rest"),
    [
        ("Alpha [c1]", [("Alpha", ["c1"])], ""),
        ("Alpha [c1, c2].", [("Alpha.", ["c1", "c2"])], ""),
        ("Alpha (c2). Beta (C1, c2)!", [("Alpha.", ["c2"]), ("Beta!", ["c1", "c2"])], ""),
        ("Alpha ['c1'] beta [ \"c2\" ]", [("Alpha", ["c1"]), ("beta", ["c2"])], ""),
        (
            "Alpha [c1][c2]. Beta [c1], [c2].",
            [("Alpha.", ["c1", "c2"]), ("Beta.", ["c1", "c2"])],
            "",
        ),
        ("[c1] Leading marker only", [], "Leading marker only"),
        ("Uses arr[1] and f(x) [c2].", [("Uses arr[1] and f(x).", ["c2"])], ""),
        ("No markers  here.", [], "No markers here."),
    ],
)
def test_split_inline_citations(
    text: str, expected: list[tuple[str, list[str]]], rest: str
) -> None:
    assert split_inline_citations(text) == (expected, rest)


def test_no_prompt_label_ever_reaches_the_text() -> None:
    raw = json.dumps(
        {
            "sentences": [
                {"text": 'A ["c1"]. B [c2, c1]; C (c2). D', "chunk_ids": ["c1"]},
                {"text": "E [c7]. F", "chunk_ids": ["c2"]},
            ]
        }
    )
    parsed = parse_answer(raw, VALID)
    texts = [s.text for s in parsed.sentences] + parsed.dropped
    assert texts == ["A.", "B;", "C.", "D", "F", "E."]
    assert not any(re.search(r"\bc\d+\b", t, re.IGNORECASE) for t in texts)


# --- D-117: ref-near-05 (Qwen3.6, temperature 0). The stored answer was A, B, C, "}]}]}```json{",
# B, C: the fragment reached the checker as a sentence with a valid id, B and C were shown twice.
REF_A = "When verify is set to False, requests will ignore hostname mismatches."
REF_B = "It accepts any TLS certificate presented by the server."
REF_C = (
    "Consequently, requests does not reject a server certificate whose hostname does not match "
    "the URL."
)
FRAGMENT = "}]}]}```json{"


def test_ref_near_05_reply_drops_the_fragment_and_shows_each_sentence_once() -> None:
    reply = answer_json(
        (REF_A, ["c1"]),
        (REF_B, ["c2"]),
        (REF_C, ["c3"]),
        (FRAGMENT, ["c3"]),
        (REF_B, ["c2"]),
        (REF_C, ["c3"]),
    )
    parsed = parse_answer(reply, VALID)
    assert parsed.sentences == [
        DraftSentence(REF_A, ("c1",)),
        DraftSentence(REF_B, ("c2",)),
        DraftSentence(REF_C, ("c3",)),
    ]
    assert parsed.fragments == [FRAGMENT] and parsed.dropped == [FRAGMENT]
    assert parsed.duplicates == 2 and parsed.returned == 6


def test_ref_near_05_fragment_inside_one_text_with_inline_markers() -> None:
    """The same shape packed into one ``text`` with inline markers is cleaned the same way."""
    packed = f"{REF_C} [c3] {FRAGMENT} [c3] {REF_B} [c2] {REF_C} [c3]"
    parsed = parse_answer(answer_json((packed, [])), VALID)
    assert parsed.sentences == [DraftSentence(REF_C, ("c3",)), DraftSentence(REF_B, ("c2",))]
    assert parsed.fragments == [FRAGMENT] and parsed.duplicates == 1


def test_a_repeated_sentence_merges_its_new_citations_into_the_first() -> None:
    reply = answer_json(
        ("Proxies use the proxies argument.", ["c1"]),
        ("Sessions keep cookies.", ["c2"]),
        ("Proxies  use the\nproxies argument.", ["c3", "c1"]),  # whitespace differs only
    )
    parsed = parse_answer(reply, VALID)
    assert parsed.sentences == [
        DraftSentence("Proxies use the proxies argument.", ("c1", "c3")),
        DraftSentence("Sessions keep cookies.", ("c2",)),
    ]
    assert parsed.duplicates == 1 and parsed.dropped == []


def test_only_exact_repeats_are_merged() -> None:
    reply = answer_json(
        ("Proxies use the proxies argument.", ["c1"]),
        ("proxies use the proxies argument.", ["c2"]),  # case differs: another sentence
        ("Proxies use the proxies argument", ["c2"]),  # punctuation differs
    )
    parsed = parse_answer(reply, VALID)
    assert len(parsed.sentences) == 3 and parsed.duplicates == 0


def test_a_repeat_citing_an_unknown_id_is_dropped_not_merged() -> None:
    text = "Proxies use the proxies argument."
    parsed = parse_answer(answer_json((text, ["c1"]), (text, ["c9"])), VALID)
    assert parsed.sentences == [DraftSentence(text, ("c1",))]
    assert parsed.dropped == [text] and parsed.duplicates == 0


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("}]}]}```json{", True),
        ("```", True),
        ('"}, {"', True),
        ("]}", True),
        ("```json Proxies are set per session.", False),
        ("The default is `{}`.", False),
        ("Use `[]` for none, 0 otherwise.", False),
        ("Prüfung läuft.", False),
    ],
)
def test_is_syntax_fragment(text: str, fragment: bool) -> None:
    assert is_syntax_fragment(text) is fragment


def test_a_reply_of_fragments_only_has_no_sentences() -> None:
    parsed = parse_answer(answer_json((FRAGMENT, ["c1"]), ("]}", ["c2"])), VALID)
    assert parsed.sentences == [] and parsed.dropped == [FRAGMENT, "]}"]


def test_a_second_json_object_after_the_answer_is_an_unusable_reply() -> None:
    """Without strict JSON-schema output, trailing junk holding another object never becomes
    text: the reply is unusable (layer 3's one retry). Junk without braces is ignored."""
    first = answer_json((REF_A, ["c1"]))
    with pytest.raises(InvalidOutputError):
        parse_answer(first + "}]}```json" + first + "```", VALID)
    assert parse_answer(first + "\n```", VALID).sentences == [DraftSentence(REF_A, ("c1",))]


def test_overview_sentences_are_cleaned_the_same_way() -> None:
    reply = json.dumps(
        {
            "sentences": [
                {"text": REF_A, "chunk_ids": ["c1"]},
                {"text": FRAGMENT, "chunk_ids": ["c1"]},
                {"text": REF_A, "chunk_ids": ["c2"]},
            ],
            "questions": ["One?", "Two?", "Three?"],
        }
    )
    parsed = parse_overview(reply, VALID)
    assert parsed.answer.sentences == [DraftSentence(REF_A, ("c1", "c2"))]
