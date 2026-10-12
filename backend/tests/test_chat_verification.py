"""The answer pipeline with layers 4 and 5: badges, removal, regeneration, refusal rule."""

from __future__ import annotations

from collections.abc import Sequence

from app.chat.pipeline import NOT_IN_SOURCES, Answered, Refused, answer, retrieval_fingerprint
from app.chat.prompt import (
    ANSWER_SYSTEM,
    FEEDBACK_HEADING,
    FEEDBACK_TASK,
    REGENERATE_INSTRUCTION,
    REGENERATION_HEADING,
)
from app.chat.schemas import Stage
from app.grounding.verify import Verifier
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.chat_helpers import ScriptedChat, answer_json

QUESTION = "How are proxies configured in requests?"


def chunk(cid: str, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(cid, "s1", "Guide", f"text of {cid}", {}, 100, score)


FOUND = Retrieval(
    chunks=[chunk("k1"), chunk("k2", 0.8), chunk("k3", 0.7)],
    ranked=[chunk("k1"), chunk("k2", 0.8), chunk("k3", 0.7)],
    source_ids=["s1"],
)


class BySentence:
    """Checker scoring each sentence from a table (unknown sentences: 1.0)."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls = 0

    def __call__(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.calls += 1
        return [self.scores.get(h, 1.0) for _, h in pairs]


def run(
    chat: ScriptedChat,
    scores: dict[str, float] | None = None,
    *,
    enabled: bool = True,
    found: Retrieval = FOUND,
) -> tuple[Answered | Refused, list[Stage]]:
    stages: list[Stage] = []
    checker = BySentence(scores or {})
    outcome = answer(
        QUESTION,
        retrieve=lambda _q: found,
        chat_adapter=lambda: chat,
        refuse_threshold=0.15,
        verifier=Verifier(checker if enabled else None, threshold=0.5),
        on_stage=stages.append,
    )
    return outcome, stages


def regenerated_user(first_user: str, feedback: str) -> str:
    """The second attempt's user message: the first one with the regeneration section added
    before the question (D-079)."""
    chunks, question = first_user.rsplit("\n\nQuestion: ", 1)
    return (
        f"{chunks}\n\n{REGENERATION_HEADING}\n\n{REGENERATE_INSTRUCTION}\n\n{feedback}"
        f"\n\nQuestion: {question}"
    )


def latency(dump: dict[str, object], *, passes: int, calls: int, cited: bool) -> None:
    """Pops and checks the D-080 latency fields that hold measured times."""
    timings = dump.pop("timings")
    assert isinstance(timings, dict)
    assert timings["retrieval_s"] is not None and timings["gate_s"] is None
    assert len(timings["generation_s"]) == calls and len(timings["verification_s"]) == passes
    assert (timings["citation_s"] is not None) is cited
    assert timings["total_s"] >= 0
    prompt = dump.pop("prompt_tokens")
    assert isinstance(prompt, list) and len(prompt) == calls and all(n > 0 for n in prompt)


PARENTS_OFF: dict[str, object] = {
    "expansions": [],
    "promoted_tokens": 0,
    "outline_tokens": 0,
    "outline_fingerprint": None,
    "promoted_cited": [0, 0],
    "windows": [],
    "window_tokens": 0,
    "window_fingerprint": None,
}


def test_all_sentences_pass_is_verified_with_scores_and_diagnostics() -> None:
    chat = ScriptedChat(answer_json(("A.", ["c1"]), ("B.", ["c2", "c1"])))
    outcome, stages = run(chat, {"A.": 0.9, "B.": 0.6})
    assert isinstance(outcome, Answered)
    assert outcome.badge == "verified" and outcome.removed == []
    assert outcome.scores == [{"k1": 0.9}, {"k2": 0.6, "k1": 0.6}]
    dump = outcome.diagnostics.model_dump()
    latency(dump, passes=1, calls=1, cited=True)
    assert dump == {
        "top_rerank": 0.9,
        "sentence_scores": [0.9, 0.6],
        "regenerated": False,
        "sentence_methods": ["checker", "checker"],
        "judge_vetoed": [],
        "judge_seconds": None,
        "citation_changes": [],
        "retrieval_fingerprint": retrieval_fingerprint(FOUND.chunks),
        "generation": {"temperature": 0.2, "seed": None},
        "failed_sentences": [],
        "judge_calls": 0,
        "judge_calls_per_pass": [0],
        "answer_calls": 1,
        "completion_tokens": [None],
        "outline_only": [0],
        "window_only": [0],
        **PARENTS_OFF,
    }
    assert stages == ["retrieving", "generating", "checking"]
    assert len(chat.calls) == 1


def test_failing_sentence_is_removed_and_citations_are_renumbered() -> None:
    chat = ScriptedChat(
        answer_json(("Kept one.", ["c2"]), ("Unsupported.", ["c3"]), ("Kept two.", ["c1", "c3"]))
    )
    outcome, _ = run(chat, {"Unsupported.": 0.2})
    assert isinstance(outcome, Answered)
    assert outcome.badge == "partially_verified"
    assert outcome.removed == ["Unsupported."]
    assert [s.text for s in outcome.sentences] == ["Kept one.", "Kept two."]
    # k3 was first used by the removed sentence; numbers follow the kept sentences only.
    assert [[(c.n, c.chunk_id) for c in s.citations] for s in outcome.sentences] == [
        [(1, "k2")],
        [(2, "k1"), (3, "k3")],
    ]
    assert outcome.diagnostics.sentence_scores == [1.0, 0.2, 1.0]
    assert len(chat.calls) == 1  # one of three failing does not regenerate


def test_exactly_half_failing_is_not_refused_and_not_regenerated() -> None:
    chat = ScriptedChat(answer_json(("Good.", ["c1"]), ("Bad.", ["c2"])))
    outcome, _ = run(chat, {"Bad.": 0.49})
    assert isinstance(outcome, Answered)
    assert outcome.badge == "partially_verified" and outcome.removed == ["Bad."]
    assert len(chat.calls) == 1


def test_first_sentence_failing_regenerates_once_then_refuses() -> None:
    # D-063: the first sentence failing alone (not more than half) also gets the one
    # regeneration; the refusal rule applies to the second reply.
    chat = ScriptedChat(
        answer_json(("Bad first.", ["c1"]), ("Good.", ["c2"])),
        answer_json(("Bad again.", ["c1"]), ("Good.", ["c2"]), ("Fine.", ["c3"])),
        "never used",
    )
    outcome, stages = run(chat, {"Bad first.": 0.1, "Bad again.": 0.2})
    assert isinstance(outcome, Refused)
    assert outcome.refusal.reason == "unsupported"
    assert outcome.refusal.message == NOT_IN_SOURCES
    assert outcome.refusal.suggestion == (
        "Add sources that cover “how are proxies configured in requests”, then ask again."
    )
    assert outcome.diagnostics.sentence_scores == [0.2, 1.0, 1.0]
    assert outcome.diagnostics.regenerated is True
    # The refusal keeps the failed sentence of the reply it rests on and why (D-075).
    assert [f.model_dump() for f in outcome.diagnostics.failed_sentences] == [
        {"text": "Bad again.", "reason": "checker score 0.20 < 0.5"}
    ]
    assert len(chat.calls) == 2
    assert stages == ["retrieving", "generating", "checking", "generating", "checking"]


def test_first_sentence_failing_can_recover_on_the_regeneration() -> None:
    chat = ScriptedChat(
        answer_json(("Bad first.", ["c1"]), ("Good.", ["c2"])),
        answer_json(("Better.", ["c1"]), ("Good.", ["c2"])),
    )
    outcome, _ = run(chat, {"Bad first.": 0.1})
    assert isinstance(outcome, Answered) and outcome.badge == "verified"
    assert [s.text for s in outcome.sentences] == ["Better.", "Good."]
    assert outcome.diagnostics.regenerated is True
    second = chat.calls[1]["messages"]
    feedback = (
        f"{FEEDBACK_HEADING}\n"
        '1. "Bad first." (cited c1): not supported by the cited chunks.\n'
        f"{FEEDBACK_TASK}"
    )
    # The system message stays the same; the regeneration text goes before the question (D-079).
    assert second[0]["content"] == ANSWER_SYSTEM
    assert second[1]["content"] == regenerated_user(
        chat.calls[0]["messages"][1]["content"], feedback
    )


def test_more_than_half_failing_regenerates_once_and_can_recover() -> None:
    chat = ScriptedChat(
        answer_json(("Bad 1.", ["c1"]), ("Bad 2.", ["c2"]), ("Good.", ["c3"])),
        answer_json(("Better.", ["c1"])),
    )
    outcome, stages = run(chat, {"Bad 1.": 0.0, "Bad 2.": 0.3})
    assert isinstance(outcome, Answered)
    assert outcome.badge == "verified"
    assert [s.text for s in outcome.sentences] == ["Better."]
    assert outcome.removed == []  # the first reply is replaced, not partly kept
    assert outcome.diagnostics.regenerated is True
    assert outcome.diagnostics.sentence_scores == [1.0]
    assert stages == ["retrieving", "generating", "checking", "generating", "checking"]
    assert len(chat.calls) == 2
    first, second = (c["messages"] for c in chat.calls)
    assert first[0]["content"] == ANSWER_SYSTEM
    # Only the failed sentences are listed, each with its reason (D-074).
    feedback = (
        f"{FEEDBACK_HEADING}\n"
        '1. "Bad 1." (cited c1): not supported by the cited chunks.\n'
        '2. "Bad 2." (cited c2): not supported by the cited chunks.\n'
        f"{FEEDBACK_TASK}"
    )
    # The system message stays the same; the regeneration text goes before the question (D-079).
    assert second[0]["content"] == ANSWER_SYSTEM
    assert second[1]["content"] == regenerated_user(
        chat.calls[0]["messages"][1]["content"], feedback
    )
    assert REGENERATE_INSTRUCTION == (
        "Some sentences were not supported by the cited chunks. "
        "Only state what the chunks say, in the chunks' own words, and cite them."
    )


def test_regeneration_happens_only_once_then_refuses() -> None:
    chat = ScriptedChat(
        answer_json(("Bad 1.", ["c1"]), ("Bad 2.", ["c2"])),
        answer_json(("Bad 3.", ["c1"]), ("Good.", ["c2"]), ("Bad 4.", ["c3"])),
        "never used",
    )
    outcome, _ = run(chat, {"Bad 1.": 0.0, "Bad 2.": 0.0, "Bad 3.": 0.0, "Bad 4.": 0.0})
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "unsupported"
    assert outcome.diagnostics.regenerated is True
    assert outcome.diagnostics.sentence_scores == [0.0, 1.0, 0.0]
    assert len(chat.calls) == 2


def test_regenerated_reply_with_a_failing_first_sentence_is_refused() -> None:
    chat = ScriptedChat(
        answer_json(("Bad 1.", ["c1"])),
        answer_json(("Bad 2.", ["c1"]), ("Good.", ["c2"]), ("Fine.", ["c3"])),
    )
    outcome, _ = run(chat, {"Bad 1.": 0.0, "Bad 2.": 0.0})
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "unsupported"


def test_unusable_regeneration_is_refused_as_unsupported() -> None:
    chat = ScriptedChat(answer_json(("Bad.", ["c1"])), "not json", "still not json")
    outcome, _ = run(chat, {"Bad.": 0.0})
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "unsupported"
    assert outcome.diagnostics.sentence_scores == [0.0]
    assert outcome.diagnostics.regenerated is True
    assert len(chat.calls) == 3  # first reply, regeneration, its one JSON retry


def test_empty_regeneration_is_refused_as_unsupported() -> None:
    chat = ScriptedChat(answer_json(("Bad.", ["c1"])), '{"sentences": []}')
    outcome, _ = run(chat, {"Bad.": 0.0})
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "unsupported"


def test_checker_off_gives_unverified_and_still_runs_code_checks() -> None:
    chat = ScriptedChat(
        answer_json(("Plain.", ["c1"]), ("Uses `nowhere`.", ["c2"]), ("Also plain.", ["c3"]))
    )
    outcome, _ = run(chat, enabled=False)
    assert isinstance(outcome, Answered)
    assert outcome.badge == "unverified"
    assert outcome.removed == ["Uses `nowhere`."]
    assert outcome.scores == [{"k1": None}, {"k3": None}]
    assert outcome.diagnostics.sentence_scores == [None, 0.0, None]


def test_checker_off_and_all_pass_is_still_unverified() -> None:
    outcome, _ = run(ScriptedChat(answer_json(("Plain.", ["c1"]))), enabled=False)
    assert isinstance(outcome, Answered) and outcome.badge == "unverified"
    assert outcome.removed == []


def test_refusals_before_generation_carry_the_top_score() -> None:
    low = Retrieval(chunks=[chunk("k1", 0.05)], ranked=[chunk("k1", 0.05)], source_ids=["s1"])
    outcome, _ = run(ScriptedChat(), found=low)
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "not_in_sources"
    dump = outcome.diagnostics.model_dump()
    latency(dump, passes=0, calls=0, cited=False)
    assert dump == {
        "top_rerank": 0.05,
        "sentence_scores": [],
        "regenerated": False,
        "sentence_methods": [],
        "judge_vetoed": [],
        "judge_seconds": None,
        "citation_changes": [],
        "retrieval_fingerprint": retrieval_fingerprint(low.chunks),
        "generation": None,  # no answer call
        "failed_sentences": [],
        "judge_calls": 0,
        "judge_calls_per_pass": [],
        "answer_calls": 0,
        "completion_tokens": [],
        "outline_only": [],
        "window_only": [],
        **PARENTS_OFF,
    }
    none = Retrieval(chunks=[], ranked=[], source_ids=[])
    outcome, _ = run(ScriptedChat(), found=none)
    assert isinstance(outcome, Refused) and outcome.diagnostics.top_rerank is None


def test_citations_are_pruned_to_the_chunks_that_support_a_sentence_alone() -> None:
    from app.chat.output import DraftSentence
    from app.chat.pipeline import prune_citations

    sentence = DraftSentence("netrc is read from ~/.netrc.", ("c1", "c2", "c3"))
    pruned, scores = prune_citations(sentence, (0.9, 0.2, 0.6), 0.5)
    assert (pruned.chunk_ids, scores) == (("c1", "c3"), (0.9, 0.6))
    # Only the joined/focused premise passed: every cited chunk stays.
    assert prune_citations(sentence, (0.2, 0.3, 0.1), 0.5)[0].chunk_ids == ("c1", "c2", "c3")
    # Checker off (no threshold) or a single citation: untouched.
    assert prune_citations(sentence, (0.9, 0.2, 0.6), None)[0] == sentence
