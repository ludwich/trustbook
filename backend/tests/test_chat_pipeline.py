from __future__ import annotations

import json

import pytest

from app.capabilities.adapters.base import AdapterError
from app.chat.llm import generate_structured
from app.chat.output import InvalidOutputError, parse_answer
from app.chat.pipeline import (
    NO_CITED_ANSWER,
    NOT_IN_SOURCES,
    NOT_VERIFIED,
    UNSUPPORTED_SUGGESTION,
    Answered,
    Refused,
    answer,
    refusal,
)
from app.chat.prompt import ANSWER_SCHEMA, RETRY_INSTRUCTION, TEMPERATURE
from app.chat.schemas import Stage
from app.grounding.verify import Verifier
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.chat_helpers import ScriptedChat, answer_json

QUESTION = "How are proxies configured in requests?"


def chunk(cid: str, score: float, source: str = "s1") -> RetrievedChunk:
    return RetrievedChunk(cid, source, f"Title {source}", f"text of {cid}", {}, 100, score)


def retrieval(*chunks: RetrievedChunk) -> Retrieval:
    return Retrieval(chunks=list(chunks), ranked=list(chunks), source_ids=["s1"])


def pass_all() -> Verifier:
    """Layers 4-5 that pass every sentence (these tests are about layers 2-3)."""
    return Verifier(lambda pairs: [1.0] * len(pairs), threshold=0.5)


def run(
    found: Retrieval, chat: ScriptedChat, threshold: float = 0.15
) -> tuple[Answered | Refused, list[Stage], int]:
    stages: list[Stage] = []
    built = 0

    def factory() -> ScriptedChat:
        nonlocal built
        built += 1
        return chat

    outcome = answer(
        QUESTION,
        retrieve=lambda _q: found,
        chat_adapter=factory,
        refuse_threshold=threshold,
        verifier=pass_all(),
        on_stage=stages.append,
    )
    return outcome, stages, built


def test_no_usable_sources_refuses_without_calling_the_model() -> None:
    chat = ScriptedChat()
    outcome, stages, built = run(Retrieval(chunks=[], ranked=[], source_ids=[]), chat)
    assert isinstance(outcome, Refused)
    assert outcome.refusal.reason == "no_sources"
    assert outcome.refusal.message == NOT_IN_SOURCES
    assert outcome.refusal.suggestion == "Add or tick at least one ready source, then ask again."
    assert (stages, built, chat.calls) == (["retrieving"], 0, [])


@pytest.mark.parametrize("top", [0.0, 0.1, 0.1499])
def test_low_top_score_refuses_before_generating(top: float) -> None:
    chat = ScriptedChat()
    outcome, stages, built = run(retrieval(chunk("k1", top), chunk("k2", top / 2)), chat)
    assert isinstance(outcome, Refused)
    assert outcome.refusal.reason == "not_in_sources"
    assert outcome.refusal.message == "Not in your sources"
    assert (
        outcome.refusal.suggestion
        == "Add sources that cover “how are proxies configured in requests”, then ask again."
    )
    assert (stages, built, chat.calls) == (["retrieving"], 0, [])


def test_no_candidates_at_all_is_not_in_sources() -> None:
    outcome, _, built = run(Retrieval(chunks=[], ranked=[], source_ids=["s1"]), ScriptedChat())
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "not_in_sources"
    assert built == 0


def test_threshold_is_inclusive_and_configurable() -> None:
    chat = ScriptedChat(answer_json(("Proxies use a dict.", ["c1"])))
    outcome, _, _ = run(retrieval(chunk("k1", 0.15)), chat)
    assert isinstance(outcome, Answered)
    outcome, _, _ = run(retrieval(chunk("k1", 0.5)), ScriptedChat(), threshold=0.6)
    assert isinstance(outcome, Refused)


def test_answer_with_numbered_citations_and_prompt() -> None:
    chat = ScriptedChat(
        answer_json(("Use the proxies argument.", ["c2"]), ("It takes a dict.", ["c1", "c2"]))
    )
    outcome, stages, _ = run(retrieval(chunk("k1", 0.9), chunk("k2", 0.8, "s2")), chat)
    assert isinstance(outcome, Answered)
    assert stages == ["retrieving", "generating", "checking"]
    assert [s.text for s in outcome.sentences] == ["Use the proxies argument.", "It takes a dict."]
    assert [[(c.n, c.chunk_id) for c in s.citations] for s in outcome.sentences] == [
        [(1, "k2")],
        [(2, "k1"), (1, "k2")],
    ]
    call = chat.calls[0]
    assert call["temperature"] == TEMPERATURE == 0.2
    assert call["schema"] == ANSWER_SCHEMA
    assert "[c1] Source: Title s1\ntext of k1" in call["messages"][1]["content"]


def test_dropped_sentences_are_removed_but_the_rest_is_kept() -> None:
    chat = ScriptedChat(answer_json(("Uncited.", []), ("Cited.", ["c1"]), ("Bad id.", ["c7"])))
    outcome, _, _ = run(retrieval(chunk("k1", 0.9)), chat)
    assert isinstance(outcome, Answered)
    assert [s.text for s in outcome.sentences] == ["Cited."]
    assert outcome.dropped == ["Uncited.", "Bad id."]


def test_model_returning_no_sentences_is_not_in_sources() -> None:
    outcome, stages, _ = run(retrieval(chunk("k1", 0.9)), ScriptedChat('{"sentences": []}'))
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "not_in_sources"
    assert stages[-1] == "checking"


def test_all_sentences_uncited_is_invalid_output() -> None:
    chat = ScriptedChat(answer_json(("Outside knowledge.", []), ("Made up.", ["c5"])))
    outcome, _, _ = run(retrieval(chunk("k1", 0.9)), chat)
    assert isinstance(outcome, Refused)
    assert outcome.refusal.reason == "invalid_output"
    assert outcome.refusal.message == NO_CITED_ANSWER


def test_invalid_json_is_retried_once() -> None:
    chat = ScriptedChat("Sure! Proxies are…", answer_json(("Use proxies.", ["c1"])))
    outcome, _, _ = run(retrieval(chunk("k1", 0.9)), chat)
    assert isinstance(outcome, Answered)
    assert len(chat.calls) == 2
    retry = chat.calls[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": "Sure! Proxies are…"}
    assert retry[-1] == {"role": "user", "content": RETRY_INSTRUCTION}


def test_invalid_json_twice_is_invalid_output() -> None:
    chat = ScriptedChat("nope", '{"answer": 1}', "never used")
    outcome, _, _ = run(retrieval(chunk("k1", 0.9)), chat)
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "invalid_output"
    assert len(chat.calls) == 2


def test_valid_json_is_not_retried_even_when_everything_is_dropped() -> None:
    chat = ScriptedChat(answer_json(("No ids.", [])), "never used")
    outcome, _, _ = run(retrieval(chunk("k1", 0.9)), chat)
    assert isinstance(outcome, Refused) and len(chat.calls) == 1


def test_adapter_errors_propagate() -> None:
    class Failing(ScriptedChat):
        def chat(self, *_a: object, **_k: object) -> object:  # type: ignore[override]
            raise AdapterError("timeout", "No reply within 240 s.")

    with pytest.raises(AdapterError):
        run(retrieval(chunk("k1", 0.9)), Failing())


def test_generate_structured_parses_with_given_parser() -> None:
    chat = ScriptedChat(json.dumps({"sentences": [{"text": "A.", "chunk_ids": ["c1"]}]}))
    parsed = generate_structured(
        chat, [{"role": "user", "content": "q"}], ANSWER_SCHEMA, lambda t: parse_answer(t, {"c1"})
    )
    assert parsed.sentences[0].text == "A."
    with pytest.raises(InvalidOutputError):
        generate_structured(
            ScriptedChat("", ""), [], ANSWER_SCHEMA, lambda t: parse_answer(t, {"c1"})
        )


def test_each_refusal_reason_has_its_own_card() -> None:
    """D-118 (G3-2): a verification refusal is not shown as "Not in your sources"."""
    missing = refusal("not_in_sources", QUESTION)
    assert (missing.message, missing.suggestion) == (
        NOT_IN_SOURCES,
        "Add sources that cover “how are proxies configured in requests”, then ask again.",
    )
    unverified = refusal("unsupported", QUESTION)
    assert unverified.reason == "unsupported"
    assert (unverified.message, unverified.suggestion) == (NOT_VERIFIED, UNSUPPORTED_SUGGESTION)
    assert unverified.message == "Couldn't verify an answer"
    assert "rephras" in UNSUPPORTED_SUGGESTION and "file or function" in UNSUPPORTED_SUGGESTION
    assert "Add sources" not in UNSUPPORTED_SUGGESTION
    assert refusal("no_sources", QUESTION).message == NOT_IN_SOURCES
    assert refusal("invalid_output", QUESTION).message == NO_CITED_ANSWER
    titles = {refusal(r, QUESTION).message for r in ("not_in_sources", "unsupported")}
    assert len(titles) == 2
