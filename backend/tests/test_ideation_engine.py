"""The grilling engine's steps: reply parsing, the prompt, grounding of recommendations (cited,
removed, below threshold, checker off), new branches and risks, and the mock adapter's hooks."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.capabilities.adapters.mock import (
    IDEA_SUGGESTION,
    UNSUPPORTED_SENTENCE,
    MockChatAdapter,
)
from app.chat.output import InvalidOutputError
from app.grounding.verify import Verifier
from app.ideation import engine
from app.ideation.map import MapNode
from app.ideation.output import parse_seed, parse_turn
from app.ideation.prompt import (
    NO_CHUNKS,
    SEED_SCHEMA,
    TURN_SCHEMA,
    TURN_SYSTEM,
    Exchange,
    parse_ideation_prompt,
    seed_messages,
    turn_messages,
)
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.chat_helpers import ScriptedChat
from tests.fakes import FakeGrounding

FERRY = "The ferry to Gull Island runs twice a day from May to September."
HOSTEL = "The island hostel has twelve beds and closes in October."
IDEA = "A guided weekend trip to Gull Island for birdwatchers"


def chunk(n: int, text: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{n}",
        source_id="src-1",
        source_title="Gull Island guide",
        text=text,
        locator={"pages": [n]},
        token_count=20,
        score=score,
    )


def retrieval(*chunks: RetrievedChunk) -> Retrieval:
    return Retrieval(chunks=list(chunks), ranked=list(chunks), source_ids=["src-1"])


def turn_json(
    question: str = "When should the trip run?",
    sentences: list[tuple[str, list[str]]] | None = None,
    branches: list[dict[str, str]] | None = None,
    risks: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "question": question,
            "recommendation": {
                "sentences": [{"text": t, "chunk_ids": ids} for t, ids in (sentences or [])]
            },
            "new_branches": branches or [],
            "risks": risks or [],
        }
    )


def checker() -> Verifier:
    return Verifier(FakeGrounding().score, threshold=0.5)


NODES = [
    MapNode("s0", None, 0, "Who it is for", "resolved", "2026-01-01T00:00:00Z"),
    MapNode("s1", None, 1, "When it runs", "tentative"),
    MapNode("s2", None, 2, "Costs", "tentative"),
]
LAST = Exchange("s0", "Who is it for?", "Birdwatchers, a risk is bad weather", "user")


def run_turn(
    chat: ScriptedChat,
    found: Retrieval,
    *,
    verifier: Verifier | None = None,
    target: MapNode | None = NODES[1],
    last: Exchange | None = LAST,
    threshold: float = 0.15,
) -> tuple[engine.TurnResult, list[str]]:
    queries: list[str] = []

    def retrieve(query: str) -> Retrieval:
        queries.append(query)
        return found

    result = engine.turn(
        engine.TurnContext(IDEA, NODES, target, history=[LAST], last=last),
        retrieve=retrieve,
        chat_adapter=lambda: chat,
        refuse_threshold=threshold,
        verifier=verifier or checker(),
    )
    return result, queries


# --- output ----------------------------------------------------------------------------------


def test_parse_seed_needs_three_distinct_branches_and_strips_markers() -> None:
    parsed = parse_seed('{"title": "Trip [c1]", "branches": ["A", "a", "B [c2]", "C", "D", "E"]}')
    assert parsed.title == "Trip" and parsed.branches == ["A", "B", "C", "D"]
    with pytest.raises(InvalidOutputError):
        parse_seed('{"title": "x", "branches": ["A", "A", "B"]}')
    with pytest.raises(InvalidOutputError):
        parse_seed('{"title": "x"}')


def test_parse_turn_keeps_suggestions_and_drops_unknown_ids() -> None:
    reply = turn_json(
        sentences=[
            ("Run it in summer.", ["c1"]),
            ("Offer a rain plan.", []),
            ("The hostel is free.", ["c9"]),  # unknown id: dropped, never a suggestion
            ('Book early ["c2"]. Then advertise.', []),
        ],
        branches=[{"title": "Boats [c1]", "parent": "[N1]"}, {"title": "", "parent": ""}],
        risks=["Weather", "Weather", "Cost", "Third"],
    )
    parsed = parse_turn(reply, ["c1", "c2"], expect_question=True)
    assert [(s.text, s.chunk_ids) for s in parsed.sentences] == [
        ("Run it in summer.", ("c1",)),
        ("Offer a rain plan.", ()),
        ("Book early.", ("c2",)),
        ("Then advertise.", ()),
    ]
    assert parsed.dropped == ["The hostel is free."]
    assert [s.text for s in parsed.cited] == ["Run it in summer.", "Book early."]
    assert parsed.branches == [("Boats", "n1")]
    assert parsed.risks == ["Weather", "Cost"]


def test_parse_turn_question_rules() -> None:
    with pytest.raises(InvalidOutputError):
        parse_turn(turn_json(question=" "), [], expect_question=True)
    with pytest.raises(InvalidOutputError):
        parse_turn("not json", [], expect_question=True)
    nothing_left = parse_turn(
        turn_json(question="Ignored?", sentences=[("x", [])]), [], expect_question=False
    )
    assert nothing_left.question == "" and nothing_left.sentences == []


# --- prompt ----------------------------------------------------------------------------------


def test_turn_prompt_layout_and_read_back() -> None:
    messages = turn_messages(
        IDEA, [chunk(1, FERRY)], NODES, target=NODES[1], history=[LAST], last=LAST
    )
    assert messages[0] == {"role": "system", "content": TURN_SYSTEM}
    user = messages[1]["content"]
    assert user.startswith("Numbered chunks from the user's sources:\n\n[c1] Source: Gull")
    assert "- [n1] Who it is for (answered)\n- [n2] When it runs (not asked yet)" in user
    assert user.endswith("Ask about: [n2] When it runs")
    parts = parse_ideation_prompt(user)
    assert [c.label for c in parts.chunks] == ["c1"] and parts.chunks[0].text == FERRY
    assert parts.idea == IDEA
    assert (parts.target_label, parts.target_title) == ("n2", "When it runs")
    assert (parts.last_label, parts.last_answer) == ("n1", LAST.answer)

    empty = turn_messages(IDEA, [], NODES, target=None, last=LAST)[1]["content"]
    assert empty.startswith(NO_CHUNKS) and "Ask about: nothing" in empty
    read = parse_ideation_prompt(empty)
    assert read.chunks == [] and read.target_title is None
    seed = parse_ideation_prompt(seed_messages(IDEA)[1]["content"])
    assert seed.idea == IDEA and seed.chunks == []


# --- engine ----------------------------------------------------------------------------------


def test_cited_recommendation_passes_and_is_numbered() -> None:
    chat = ScriptedChat(turn_json(sentences=[(FERRY, ["c2"]), ("Pack rain gear.", [])]))
    result, queries = run_turn(chat, retrieval(chunk(1, HOSTEL), chunk(2, FERRY)))
    assert queries == [f"{IDEA}\nWhen it runs"]
    assert chat.calls[0]["schema"] == TURN_SCHEMA
    assert chat.calls[0]["think"] is False  # D-103: no thinking before a turn's JSON
    asked = result.asked
    assert asked is not None and asked.question == "When should the trip run?"
    rec = asked.recommendation
    assert (rec.grounding, rec.badge, rec.removed) == ("partly_cited", "verified", [])
    assert [(s.text, s.kind) for s in rec.sentences] == [
        (FERRY, "cited"),
        ("Pack rain gear.", "suggestion"),
    ]
    citation = rec.sentences[0].citations[0]
    assert (citation.n, citation.chunk_id, citation.locator) == (1, "chunk-2", {"pages": [2]})
    assert rec.sentences[1].citations == []
    assert asked.citations == [{"chunk_id": "chunk-2", "verifier_score": 1.0}]
    assert asked.recommended_answer == f"{FERRY} Pack rain gear."
    assert asked.diagnostics["top_rerank"] == 0.9


def test_failed_cited_sentence_is_removed_not_relabelled() -> None:
    reply = turn_json(sentences=[(FERRY, ["c1"]), (UNSUPPORTED_SENTENCE, ["c1"])])
    result, _ = run_turn(ScriptedChat(reply), retrieval(chunk(1, FERRY)))
    assert result.asked is not None
    rec = result.asked.recommendation
    assert rec.removed == [UNSUPPORTED_SENTENCE]
    assert [s.text for s in rec.sentences] == [FERRY]
    assert all(s.kind == "cited" for s in rec.sentences)
    assert (rec.grounding, rec.badge) == ("cited", "partially_verified")


def test_refusal_rule_over_cited_sentences_removes_them_all() -> None:
    reply = turn_json(
        sentences=[(UNSUPPORTED_SENTENCE, ["c1"]), (FERRY, ["c1"]), ("Try a pilot.", [])]
    )
    result, _ = run_turn(ScriptedChat(reply), retrieval(chunk(1, FERRY)))
    assert result.asked is not None
    rec = result.asked.recommendation
    assert rec.removed == [UNSUPPORTED_SENTENCE, FERRY]
    assert [(s.text, s.kind) for s in rec.sentences] == [("Try a pilot.", "suggestion")]
    assert (rec.grounding, rec.badge) == ("suggestion", None)
    assert result.asked.citations == []
    assert result.asked.diagnostics["refusal_rule"] is True


def test_below_threshold_gives_no_chunks_so_only_suggestions() -> None:
    reply = turn_json(sentences=[(FERRY, ["c1"]), ("Ask local guides.", [])])
    chat = ScriptedChat(reply)
    result, _ = run_turn(chat, retrieval(chunk(1, FERRY, score=0.05)))
    user = chat.calls[0]["messages"][1]["content"]
    assert user.startswith(NO_CHUNKS) and "[c1]" not in user
    assert result.asked is not None
    rec = result.asked.recommendation
    assert [(s.text, s.kind) for s in rec.sentences] == [("Ask local guides.", "suggestion")]
    assert rec.grounding == "suggestion" and rec.removed == []
    assert result.asked.diagnostics["below_threshold"] is True


def test_checker_off_keeps_citations_badged_unverified() -> None:
    reply = turn_json(sentences=[("Anything at all.", ["c1"])])
    off = Verifier(None, threshold=0.5)
    result, _ = run_turn(ScriptedChat(reply), retrieval(chunk(1, FERRY)), verifier=off)
    assert result.asked is not None
    rec = result.asked.recommendation
    assert (rec.grounding, rec.badge) == ("cited", "unverified")
    assert rec.sentences[0].citations[0].chunk_id == "chunk-1"


def test_new_branches_and_risks_come_from_the_last_answer() -> None:
    reply = turn_json(
        branches=[{"title": "Rain plan", "parent": "n1"}, {"title": "Boats", "parent": "n99"}],
        risks=["Bad weather"],
    )
    result, _ = run_turn(ScriptedChat(reply), retrieval())
    assert result.branches == [("Rain plan", "s0"), ("Boats", "s0")]
    assert [(r.text, r.node_id) for r in result.risks] == [("Bad weather", "s0")]
    first, _ = run_turn(ScriptedChat(reply), retrieval(), last=None)
    assert first.branches == [] and first.risks == []  # no answer yet: nothing opened up


def test_nothing_left_to_ask_only_adds_branches() -> None:
    reply = turn_json(question="", branches=[{"title": "Rain plan", "parent": "n1"}])
    result, queries = run_turn(ScriptedChat(reply), retrieval(), target=None)
    assert queries == [] and result.asked is None
    assert result.branches == [("Rain plan", "s0")]


def test_seed_call() -> None:
    chat = ScriptedChat("oops", '{"title": "Bird trips", "branches": ["Who", "When", "Cost"]}')
    parsed = engine.seed(IDEA, chat_adapter=lambda: chat)
    assert parsed.title == "Bird trips" and parsed.branches == ["Who", "When", "Cost"]
    assert len(chat.calls) == 2 and chat.calls[0]["schema"] == SEED_SCHEMA
    assert [c["think"] for c in chat.calls] == [False, False]  # the retry too (D-103)


# --- mock hooks ------------------------------------------------------------------------------


def _mock(messages: list[Any], schema: dict[str, Any]) -> dict[str, Any]:
    result = MockChatAdapter().chat(messages, json_schema=schema)
    data: dict[str, Any] = json.loads(result.text)
    return data


def test_mock_seed_and_turn_hooks() -> None:
    seeded = _mock(seed_messages(IDEA), SEED_SCHEMA)
    assert seeded["branches"] == [
        "Who it is for",
        "What your sources say about guided",
        "Risks and open problems",
    ]
    target = MapNode("s1", None, 1, "Gull Island ferry", "tentative")
    nodes = [NODES[0], target]
    messages = turn_messages(
        f"{IDEA} TB-UNSUPPORTED",
        [chunk(1, HOSTEL), chunk(2, FERRY)],
        nodes,
        target=target,
        last=Exchange("s0", "Who?", "TB-BRANCH families; risk of storms", "user"),
    )
    data = _mock(messages, TURN_SCHEMA)
    assert data["question"] == "What should be decided about “Gull Island ferry”?"
    assert data["recommendation"]["sentences"] == [
        {"text": FERRY, "chunk_ids": ["c2"]},
        {"text": UNSUPPORTED_SENTENCE, "chunk_ids": ["c1"]},
    ]
    assert data["new_branches"] == [
        {"title": "Follow-up on families; risk of storms", "parent": "n1"}
    ]
    assert data["risks"] == ["Raised in an answer: TB-BRANCH families; risk of storms"]
    plain = turn_messages("Unrelated plan", [chunk(1, HOSTEL)], nodes, target=NODES[1])
    suggestion = _mock(plain, TURN_SCHEMA)["recommendation"]["sentences"]
    assert suggestion == [{"text": IDEA_SUGGESTION, "chunk_ids": []}]
