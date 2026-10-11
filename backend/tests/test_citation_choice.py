"""Citation completion, the near-copy collapse and the guarded upgrade (D-052 extension, D-056)."""

from __future__ import annotations

from collections.abc import Sequence

from app.chat.citation_choice import (
    choose,
    complete_citations,
    containment,
    names_symbol,
    upgrade_candidates,
)
from app.chat.output import DraftSentence
from app.chat.pipeline import Answered, answer
from app.chat.prompt import labelled
from app.grounding.code_checks import LineRange, SnapshotFiles
from app.grounding.verify import Verifier
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.chat_helpers import ScriptedChat, answer_json

# src/requests/models.py of psf/requests (Apache-2.0), the smoke corpus: three methods share
# one docstring, so a sentence about it is supported by each of them alone.
DOC = '''        """Returns True if :attr:`status_code` is less than 400.

        This attribute checks if the status code of the response is between
        400 and 600 to see if there was a client error or a server error. If
        the status code, is between 200 and 400, this will return True. This
        is **not** a check to see if the response code is ``200 OK``.
        """'''
OK = f"""File: src/requests/models.py · Response.ok (lines 859-872)
    @property
    def ok(self) -> bool:
{DOC}
        try:
            self.raise_for_status()
        except HTTPError:
            return False
        return True"""
BOOL = f"""File: src/requests/models.py · Response.__bool__ (lines 835-843)
    def __bool__(self) -> bool:
{DOC}
        return self.ok"""
NONZERO = BOOL.replace("__bool__", "__nonzero__").replace("835-843", "845-853")
WEB = (
    "## Response Status Codes\nWe can check the response status code: r.status_code is 200. "
    "Requests also comes with a built-in status code lookup object."
)
SENTENCE = (
    "It returns True if the status code is less than 400, which covers codes between 200 and 400."
)
QUESTION = "Does Response.ok mean the status code was exactly 200? How does requests compute it?"


def code(cid: str, text: str, symbol: str, start: int, end: int, score: float) -> RetrievedChunk:
    locator = {"file": "src/requests/models.py", "start_line": start, "end_line": end}
    return RetrievedChunk(cid, "s1", "requests", text, {**locator, "symbol": symbol}, 150, score)


def context() -> dict[str, RetrievedChunk]:
    return labelled(
        [
            code("ok", OK, "Response.ok", 859, 872, 0.76),
            RetrievedChunk("web", "s2", "Quickstart", WEB, {"heading": "Status"}, 40, 0.62),
            code("bool", BOOL, "Response.__bool__", 835, 843, 0.45),
            code("nonzero", NONZERO, "Response.__nonzero__", 845, 853, 0.42),
        ]
    )


# --- the near-copy collapse ------------------------------------------------------------------


def test_near_copies_collapse_to_the_best_ranked_chunk() -> None:
    chunks = context()
    choice = choose(QUESTION, DraftSentence(SENTENCE, ("c1", "c3", "c4")), (0.97,) * 3, chunks, 0.5)
    assert choice.sentence.chunk_ids == ("c1",)
    assert choice.scores == (0.97,)
    assert choice.dropped == ("c3", "c4")


def test_without_the_definition_cited_the_copies_still_collapse_to_one() -> None:
    choice = choose(QUESTION, DraftSentence(SENTENCE, ("c3", "c4")), (0.97, 0.97), context(), 0.5)
    assert choice.sentence.chunk_ids == ("c3",)


def test_the_chunk_defining_the_named_symbol_wins_over_rerank_order() -> None:
    text = "The `__bool__` method returns True if status_code is less than 400."
    choice = choose(QUESTION, DraftSentence(text, ("c1", "c3")), (0.91, 0.94), context(), 0.5)
    # c1 ranks higher for the question, but the sentence names __bool__: its copy of the
    # docstring is the one kept.
    assert choice.sentence.chunk_ids == ("c3",)
    assert choice.dropped == ("c1",)


def test_different_supporting_text_is_kept() -> None:
    text = "Response.ok does not check if the status code is exactly 200 OK."
    choice = choose(QUESTION, DraftSentence(text, ("c1", "c2")), (0.94, 0.57), context(), 0.5)
    assert choice.sentence.chunk_ids == ("c1", "c2")
    assert choice.dropped == ()


def test_d052_pruning_still_applies_and_joined_support_keeps_everything() -> None:
    chunks = context()
    sentence = DraftSentence(SENTENCE, ("c1", "c2"))
    assert choose(QUESTION, sentence, (0.97, 0.1), chunks, 0.5).sentence.chunk_ids == ("c1",)
    together = choose(QUESTION, sentence, (0.3, 0.2), chunks, 0.5)
    assert together.sentence.chunk_ids == ("c1", "c2") and together.dropped == ()
    assert choose(QUESTION, sentence, (0.97, 0.1), chunks, None).sentence == sentence


def test_a_choice_never_drops_the_chunk_holding_a_backticked_name() -> None:
    first = RetrievedChunk(
        "a", "s", "Guide", "Requests reads ~/.netrc for credentials.", {}, 9, 0.9
    )
    second = RetrievedChunk(
        "b",
        "s",
        "Guide",
        "Requests reads ~/.netrc for credentials via `get_netrc_auth`.",
        {},
        9,
        0.5,
    )
    chunks = labelled([first, second])
    text = "Requests reads ~/.netrc for credentials using `get_netrc_auth`."
    choice = choose("netrc?", DraftSentence(text, ("c1", "c2")), (0.9, 0.9), chunks, 0.5)
    assert "c2" in choice.sentence.chunk_ids


def test_containment_and_symbol_names() -> None:
    assert containment("a b c d e", "x a b c d e y") == 1.0
    assert containment("a b c d e", "a b x d e") == 0.0
    assert containment("", "anything") == 0.0
    chunks = context()
    assert names_symbol("Response.ok is a property.", chunks["c1"])
    assert names_symbol("The `ok` property.", chunks["c1"])
    assert not names_symbol("It returns self.ok here.", chunks["c1"])  # 2 letters, unquoted
    assert names_symbol("Calling __bool__ on it.", chunks["c3"])
    assert not names_symbol("Anything.", chunks["c2"])  # no symbol


# --- the guarded upgrade ---------------------------------------------------------------------


def test_upgrade_candidate_is_the_named_better_ranked_near_copy() -> None:
    chunks = context()
    named = DraftSentence(
        "Response.ok returns True if status_code is less than 400, not only for 200 OK.", ("c3",)
    )
    assert upgrade_candidates(named, chunks) == [("c3", "c1")]
    pronoun = DraftSentence(SENTENCE, ("c3",))
    assert upgrade_candidates(pronoun, chunks) == []  # nothing named: keep the model's choice
    own = DraftSentence("The `__bool__` method returns True below 400.", ("c3",))
    assert upgrade_candidates(own, chunks) == []


class ByPremise:
    """Checker: a premise containing ``marker`` supports everything (0.95), others 0.1."""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def __call__(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [0.95 if self.marker in premise else 0.1 for premise, _ in pairs]


class Snapshot(SnapshotFiles):
    def __init__(self) -> None:
        super().__init__(None)

    def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
        return {"src/requests/models.py": [(1, 1000)]}


def run(reply: str, checker: ByPremise) -> Answered:
    found = list(context().values())
    retrieval = Retrieval(chunks=found, ranked=found, source_ids=["s1", "s2"])
    outcome = answer(
        QUESTION,
        retrieve=lambda _q: retrieval,
        chat_adapter=lambda: ScriptedChat(reply),
        refuse_threshold=0.15,
        verifier=Verifier(checker, threshold=0.5, files=Snapshot()),
    )
    assert isinstance(outcome, Answered)
    return outcome


def test_pipeline_collapses_copies_and_records_the_change() -> None:
    outcome = run(answer_json((SENTENCE, ["c1", "c3", "c4"])), ByPremise("is less than 400"))
    assert [c.chunk_id for c in outcome.sentences[0].citations] == ["ok"]
    change = outcome.diagnostics.citation_changes[0]
    assert (change.sentence, change.dropped) == (0, ["bool", "nonzero"])


def test_pipeline_upgrades_to_the_defining_chunk_only_when_it_verifies_alone() -> None:
    text = "Response.ok returns True if status_code is less than 400, not only for 200 OK."
    outcome = run(answer_json((text, ["c3"])), ByPremise("is less than 400"))
    assert [c.chunk_id for c in outcome.sentences[0].citations] == ["ok"]
    assert outcome.diagnostics.citation_changes[0].swapped == [["bool", "ok"]]
    # The definition does not verify alone: the model's citation stays.
    outcome = run(answer_json((text, ["c3"])), ByPremise("return self.ok"))
    assert [c.chunk_id for c in outcome.sentences[0].citations] == ["bool"]
    assert outcome.diagnostics.citation_changes == []


# --- completion ------------------------------------------------------------------------------

STATUS_DOC = """File: src/requests/status_codes.py (lines 1-21)
r\"\"\"
The ``codes`` object defines a mapping from common names for HTTP statuses
to their numerical codes, accessible either as attributes or as dictionary
items.

Example::

    >>> import requests
    >>> requests.codes['temporary_redirect']
    307
\"\"\""""
STATUS_TABLE = """File: src/requests/status_codes.py (lines 81-88)
    428: ("precondition_required", "precondition"),
    429: ("too_many_requests", "too_many"),
    431: ("header_fields_too_large", "fields_too_large"),"""
NAMED = "`requests.codes` maps 429 to `too_many_requests` and `too_many`."


def status_context() -> dict[str, RetrievedChunk]:
    def chunk(cid: str, text: str, start: int, end: int, score: float) -> RetrievedChunk:
        locator = {"file": "src/requests/status_codes.py", "start_line": start, "end_line": end}
        return RetrievedChunk(cid, "s1", "requests", text, locator, 100, score)

    web = RetrievedChunk("web", "s2", "Docs", "requests.codes lists names.", {}, 9, 0.9)
    return labelled(
        [web, chunk("doc", STATUS_DOC, 1, 21, 0.5), chunk("table", STATUS_TABLE, 81, 88, 0.1)]
    )


def test_completion_adds_the_same_file_chunk_holding_the_missing_name() -> None:
    chunks = status_context()
    done, added = complete_citations(DraftSentence(NAMED, ("c3",)), chunks)
    assert added == ("c2",)  # same file first, although c1 also holds requests.codes
    assert done.chunk_ids == ("c3", "c2")


def test_completion_adds_nothing_for_names_no_chunk_has() -> None:
    chunks = status_context()
    for text in (
        "`requests.codes.rate_limited` is 429.",
        "`codes.TOO_MANY_REQUESTS` is 429.",
        "`too_many_requests` and `rate_limited` mean 429.",
    ):
        sentence = DraftSentence(text, ("c3",))
        assert complete_citations(sentence, chunks) == (sentence, ())
    complete = DraftSentence("`too_many_requests` means 429.", ("c3",))
    assert complete_citations(complete, chunks) == (complete, ())  # nothing missing


def test_completion_adds_at_most_two_chunks() -> None:
    chunks = labelled(
        [RetrievedChunk(str(i), "s", "T", f"defines `name{i}` here", {}, 5, 0.5) for i in range(4)]
    )
    text = "Uses `name1`, `name2` and `name3`."
    sentence = DraftSentence(text, ("c1",))
    assert complete_citations(sentence, chunks) == (sentence, ())
    two = DraftSentence("Uses `name1` and `name2`.", ("c1",))
    assert complete_citations(two, chunks)[1] == ("c2", "c3")


def test_completed_sentence_is_verified_on_its_new_citations() -> None:
    found = list(status_context().values())
    retrieval = Retrieval(chunks=found, ranked=found, source_ids=["s1", "s2"])

    class Snap(SnapshotFiles):
        def __init__(self) -> None:
            super().__init__(None)

        def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
            return {"src/requests/status_codes.py": [(1, 200)]}

    def checker(pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [0.9 if "429" in premise else 0.1 for premise, _ in pairs]

    outcome = answer(
        "Which names on requests.codes mean 429?",
        retrieve=lambda _q: retrieval,
        chat_adapter=lambda: ScriptedChat(answer_json((NAMED, ["c3"]))),
        refuse_threshold=0.15,
        verifier=Verifier(checker, threshold=0.5, files=Snap()),
    )
    assert isinstance(outcome, Answered) and outcome.badge == "verified"
    assert [c.chunk_id for c in outcome.sentences[0].citations] == ["table", "doc"]
    assert outcome.diagnostics.citation_changes[0].added == ["doc"]
