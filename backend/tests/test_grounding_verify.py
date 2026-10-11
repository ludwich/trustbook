"""Grounding layers 4 and 5: sentence verification, code checks and the refusal rule."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.grounding.code_checks import (
    SnapshotFiles,
    backticked,
    files_from_map,
    locator_problem,
    missing_identifiers,
)
from app.grounding.verify import (
    Checked,
    GroundingSettings,
    SentenceCheck,
    Verifier,
    build_verifier,
)
from app.retrieval.service import RetrievedChunk
from tests.fakes import FakeGrounding


def rc(
    cid: str, text: str, locator: dict[str, Any] | None = None, source: str = "s1"
) -> RetrievedChunk:
    return RetrievedChunk(cid, source, "Title", text, locator or {}, 10, 0.9)


class Pinned:
    """A checker that scores by (premise, hypothesis) from a table (default 0.0)."""

    def __init__(self, table: dict[tuple[str, str], float]) -> None:
        self.table = table
        self.calls: list[list[tuple[str, str]]] = []

    def __call__(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.calls.append(list(pairs))
        return [self.table.get(p, 0.0) for p in pairs]


def write_map(space: Path, source_id: str, segments: list[dict[str, Any]]) -> None:
    folder = space / "sources" / source_id
    folder.mkdir(parents=True)
    (folder / "map.json").write_text(
        json.dumps({"version": 1, "kind": "files", "segments": segments})
    )


# --- layer 4 ----------------------------------------------------------------------------------


def test_sentence_score_is_the_max_over_cited_chunks_and_passes_at_threshold() -> None:
    a, b = rc("a", "chunk A"), rc("b", "chunk B")
    checker = Pinned({("chunk A", "S1"): 0.2, ("chunk B", "S1"): 0.5, ("chunk A", "S2"): 0.49})
    checked = Verifier(checker, threshold=0.5).check([("S1", [a, b]), ("S2", [a])])
    first, second = checked.checks
    assert (first.score, first.chunk_scores, first.passed) == (0.5, (0.2, 0.5), True)
    assert (second.score, second.passed) == (0.49, False)
    # One checker call for the whole reply, premise = chunk text, hypothesis = sentence; a
    # sentence citing two chunks is also scored against both joined.
    assert checker.calls == [
        [("chunk A", "S1"), ("chunk B", "S1"), ("chunk A", "S2"), ("chunk A\n\nchunk B", "S1")]
    ]


def test_a_claim_needing_two_cited_chunks_passes_on_the_joined_premise() -> None:
    signature = rc("a", "def __init__(self, pool_connections=DEFAULT_POOLSIZE):")
    constant = rc("b", "DEFAULT_POOLSIZE = 10")
    claim = "The default pool_connections is 10."
    checker = Pinned({(f"{signature.text}\n\n{constant.text}", claim): 0.9})
    check = Verifier(checker, threshold=0.5).check([(claim, [signature, constant])]).checks[0]
    assert (check.chunk_scores, check.score, check.passed) == ((0.0, 0.0), 0.9, True)
    # A single citation is not scored twice.
    single = Verifier(checker, threshold=0.5).check([(claim, [constant])]).checks[0]
    assert (single.score, single.passed) == (0.0, False)


def test_code_chunk_premise_keeps_its_file_header() -> None:
    text = "File: src/requests/sessions.py (lines 1-3)\nclass Session:\n    pass"
    checker = Pinned({})
    Verifier(checker, threshold=0.5).check([("Session is a class.", [rc("a", text)])])
    assert checker.calls[0][0][0] == text


def test_scores_are_clamped_and_nan_is_zero() -> None:
    def weird(pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [float("nan"), 1.7, -0.3][: len(pairs)]

    checked = Verifier(weird, threshold=0.5).check(
        [("x", [rc("a", "1")]), ("y", [rc("b", "2")]), ("z", [rc("c", "3")])]
    )
    assert [c.score for c in checked.checks] == [0.0, 1.0, 0.0]


def test_wrong_number_of_scores_is_an_error() -> None:
    with pytest.raises(RuntimeError):
        Verifier(lambda _p: [1.0, 1.0], threshold=0.5).check([("x", [rc("a", "t")])])


def test_checker_off_skips_layer_4_but_not_layer_5() -> None:
    def never(_pairs: Sequence[tuple[str, str]]) -> list[float]:
        raise AssertionError("the checker must not run when it is off")

    verifier = build_verifier(GroundingSettings(enabled=False), never, None)
    assert not verifier.enabled
    checked = verifier.check([("Anything.", [rc("a", "t")]), ("Uses `missing`.", [rc("b", "t")])])
    assert [(c.score, c.passed) for c in checked.checks] == [(None, True), (None, False)]
    assert [c.diagnostic_score for c in checked.checks] == [None, 0.0]


def test_fake_grounding_quotes_and_word_share() -> None:
    fake = FakeGrounding({"pinned": 0.3})
    assert fake.score(
        [
            (
                "Proxies are set with the proxies argument.",
                "proxies are set with the PROXIES argument.",
            ),
            ("Requests is an HTTP library.", "Requests library for HTTP…"),
            ("Some text", "pinned"),
            ("Some text", "Cookies persist"),
        ]
    ) == [1.0, 1.0, 0.3, 0.0]


# --- layer 5 ----------------------------------------------------------------------------------


def test_backticked_identifiers() -> None:
    assert backticked("Call `get()` or `Session.get`, not `get`; `  ` is ignored.") == [
        "get",
        "Session.get",
    ]
    assert missing_identifiers("`DEFAULT_RETRIES` is `0`.", ["DEFAULT_RETRIES = 0"]) == []
    assert missing_identifiers("`max_redirects` is 30.", ["self.max_redirects = DEFAULT"]) == []
    assert missing_identifiers("`MaxRedirects` is 30.", ["max_redirects = 30"]) == ["MaxRedirects"]


def test_identifier_in_any_cited_chunk_passes() -> None:
    verifier = Verifier(lambda p: [1.0] * len(p), threshold=0.5)
    a, b = rc("a", "def send(self):"), rc("b", "DEFAULT_POOLSIZE = 10")
    checked = verifier.check(
        [
            ("`send` uses `DEFAULT_POOLSIZE`.", [a, b]),
            ("`send` uses `DEFAULT_POOLBLOCK`.", [a, b]),
        ]
    )
    assert [c.passed for c in checked.checks] == [True, False]
    assert checked.checks[1].problems == ("`DEFAULT_POOLBLOCK` is not in the cited chunks",)
    assert checked.checks[1].diagnostic_score == 0.0  # fails at every threshold


def test_files_from_map() -> None:
    files = files_from_map(
        {
            "segments": [
                {"start": 0, "end": 9, "file": "a.py", "start_line": 1, "end_line": 40},
                {"start": 9, "end": 20, "file": "doc.pdf", "page": 2},
                {"start": 20, "end": 30, "heading": "Intro"},
                "junk",
            ]
        }
    )
    assert files == {"a.py": [(1, 40)], "doc.pdf": [None]}


def test_cited_file_and_line_range_must_exist_in_the_snapshot(tmp_path: Path) -> None:
    write_map(
        tmp_path,
        "src1",
        [
            {"start": 0, "end": 100, "file": "src/api.py", "start_line": 1, "end_line": 50},
            {"start": 100, "end": 200, "file": "docs/guide.pdf", "page": 1},
        ],
    )
    files = SnapshotFiles(tmp_path / "sources")

    def problem(locator: dict[str, Any], source: str = "src1") -> str | None:
        return locator_problem(files, source, locator)

    assert problem({"file": "src/api.py", "start_line": 1, "end_line": 50}) is None
    assert problem({"file": "src/api.py", "start_line": 12, "end_line": 12}) is None
    assert problem({"file": "src/api.py"}) is None
    assert problem({"file": "docs/guide.pdf", "pages": [1]}) is None
    assert problem({"heading": "Intro", "offset": 3}) is None  # not code
    assert problem({"pages": [2]}) is None
    assert problem({"file": "src/api.py", "start_line": 45, "end_line": 51}) == (
        "lines 45-51 are not in src/api.py"
    )
    assert problem({"file": "src/api.py", "start_line": 0, "end_line": 3}) == (
        "src/api.py has an invalid line range"
    )
    assert problem({"file": "src/api.py", "start_line": 9, "end_line": 3}) is not None
    assert problem({"file": "src/api.py", "start_line": "1", "end_line": 3}) is not None
    assert problem({"file": "src/other.py", "start_line": 1, "end_line": 2}) == (
        "src/other.py is not in the indexed snapshot"
    )
    # A source without a readable map, or an id that is not a plain folder name, has no files.
    assert problem({"file": "src/api.py"}, source="missing") is not None
    assert problem({"file": "src/api.py"}, source="../src1") is not None
    assert locator_problem(SnapshotFiles(None), "src1", {"file": "src/api.py"}) is not None


def test_broken_map_fails_file_citations(tmp_path: Path) -> None:
    folder = tmp_path / "sources" / "s1"
    folder.mkdir(parents=True)
    (folder / "map.json").write_text("{not json")
    files = SnapshotFiles(tmp_path / "sources")
    assert locator_problem(files, "s1", {"file": "a.py"}) == "a.py is not in the indexed snapshot"


def test_a_bad_line_range_fails_the_sentence(tmp_path: Path) -> None:
    write_map(
        tmp_path, "s1", [{"start": 0, "end": 9, "file": "a.py", "start_line": 1, "end_line": 20}]
    )
    verifier = build_verifier(GroundingSettings(), lambda p: [1.0] * len(p), tmp_path)
    good = rc("a", "x = 1", {"file": "a.py", "start_line": 1, "end_line": 20})
    bad = rc("b", "x = 1", {"file": "a.py", "start_line": 19, "end_line": 21})
    checked = verifier.check([("x is 1.", [good]), ("x is 1.", [good, bad])])
    assert [c.passed for c in checked.checks] == [True, False]
    assert checked.checks[1].score == 1.0 and checked.checks[1].problems


# --- refusal rule -----------------------------------------------------------------------------


def checks(*passed: bool) -> Checked:
    return Checked(
        [SentenceCheck(score=1.0 if p else 0.0, chunk_scores=(), passed=p) for p in passed]
    )


@pytest.mark.parametrize(
    ("passed", "mostly_failed", "refuse"),
    [
        ((True,), False, False),
        ((False,), True, True),
        ((True, False), False, False),  # exactly 50% is not "more than half"
        ((False, True), False, True),  # the first sentence failed
        ((True, False, False), True, True),
        ((True, True, False), False, False),
        ((True, False, True, False), False, False),
        ((True, False, False, False), True, True),
        ((), False, True),
    ],
)
def test_refusal_rule(passed: tuple[bool, ...], mostly_failed: bool, refuse: bool) -> None:
    checked = checks(*passed)
    if passed:
        assert checked.mostly_failed is mostly_failed
    assert checked.refuse is refuse


def test_qualified_names_pass_when_every_part_occurs_as_a_whole_word() -> None:
    from app.grounding.code_checks import missing_identifiers

    code = (
        "class Response:\n    @property\n    def ok(self):\n        return self.status_code < 400"
    )
    assert missing_identifiers("`Response.ok` is True below `400`.", [code]) == []
    assert missing_identifiers("Use `Response.okay`.", [code]) == ["Response.okay"]
    assert missing_identifiers("`Resp.ok` works.", [code]) == ["Resp.ok"]  # no partial words
    assert missing_identifiers('Prefix `"Basic "`.', [code]) == ['"Basic "']  # literal: verbatim
    assert missing_identifiers('Prefix `"Basic "`.', ['b"Basic " + token']) == []


def test_focus_premise_keeps_only_cited_lines_that_share_terms() -> None:
    from app.grounding.code_checks import focus_premise

    code = (
        "File: src/requests/adapters.py (lines 1-40)\n"
        + "\n".join(f"x{i} = {i}" for i in range(30))
        + "\nDEFAULT_POOLBLOCK = False\nDEFAULT_POOLSIZE = 10\nDEFAULT_RETRIES = 0"
    )
    window = focus_premise("DEFAULT_POOLSIZE is set to 10.", [code])
    assert "DEFAULT_POOLSIZE = 10" in window
    assert "x3 = 3" not in window  # unrelated lines are left out
    from app.grounding.code_checks import code_facts

    facts = set(code_facts([code]))
    assert all(line in code or line in facts for line in window.splitlines())  # only cited text
    assert focus_premise("the a of", [code]) == ""  # nothing to match on


def test_literals_in_code_sentences_must_occur_in_the_cited_code() -> None:
    from app.grounding.code_checks import missing_literals

    code = "DEFAULT_POOLBLOCK = False\nDEFAULT_POOLSIZE = 10"
    assert missing_literals("pool_block defaults to False and the pool size is 10.", [code]) == []
    assert missing_literals("By default, pool_block is True.", [code]) == ["True"]
    assert missing_literals("The pool size is 20.", [code]) == ["20"]
    assert missing_literals("Version 2.32.0 added it.", ["in 2.32.0"]) == []
    # Behaviour, not a value: code rarely spells it as a literal.
    assert missing_literals("should_strip_auth returns True if the host changes.", [code]) == []
    assert missing_literals("pool_block defaults to None.", [code]) == ["None"]


def test_a_split_literals_part_number_is_not_a_number_the_code_states() -> None:
    """D-058: "(part 6)" in the header must not vouch for "6 times" (reviewer repro)."""
    from app.chunking.syntax import chunk_source_code
    from app.chunking.tokens import CharEstimate
    from app.grounding.code_checks import missing_literals

    rows = "".join(f"    {100 + i}: ('name_{i}', 'alias_{i}', 'other_{i}'),\n" for i in range(80))
    src = f'"""Status codes."""\n\n_codes = {{\n{rows}}}\n'
    drafts = chunk_source_code(src, 0, len(src), "status_codes.py", CharEstimate())
    part6 = next(d for d in drafts if " · _codes (part 6) (lines " in d.text.split("\n", 1)[0])
    sentence = "`_codes` maps 150 to name_50 and retries 6 times."
    assert "150" not in part6.text and "160" in part6.text
    assert missing_literals(sentence, [part6.text]) == ["150", "6"]
    assert missing_identifiers("`_codes[6]` is a row.", [part6.text]) == ["_codes[6]"]
    assert missing_literals("`_codes` maps 160 to name_60.", [part6.text]) == []


def test_code_sentence_with_a_literal_not_in_the_code_fails_layer_5() -> None:
    chunk = rc(
        "a",
        "File: src/a.py (lines 1-2)\nDEFAULT_POOLBLOCK = False",
        {"file": "src/a.py", "start_line": 1, "end_line": 2},
    )
    checked = Verifier(lambda pairs: [1.0] * len(pairs), threshold=0.5).check(
        [("By default, pool_block is True.", [chunk])]
    )
    assert not checked.checks[0].passed
    assert "True is not in the cited code" in checked.checks[0].problems


def test_code_facts_restate_assignments_and_defaults_from_cited_code_only() -> None:
    from app.grounding.code_checks import code_facts

    signature = (
        "File: src/requests/adapters.py (lines 201-205)\n"
        "    def __init__(\n        self,\n"
        "        pool_connections: int = DEFAULT_POOLSIZE,\n"
        "        pool_block: bool = DEFAULT_POOLBLOCK,\n    ) -> None:"
    )
    constants = (
        "File: src/requests/adapters.py (lines 36-38)\n"
        "DEFAULT_POOLBLOCK = False\nDEFAULT_POOLSIZE = 10\nlogger = logging.getLogger(__name__)"
    )
    assert code_facts([signature, constants]) == [
        "pool_connections defaults to DEFAULT_POOLSIZE, which is 10.",
        "pool_block defaults to DEFAULT_POOLBLOCK, which is False.",
        "DEFAULT_POOLBLOCK is False.",
        "DEFAULT_POOLSIZE is 10.",
    ]
    # Without the constants chunk nothing is resolved; prose chunks are never restated.
    assert code_facts([signature]) == [
        "pool_connections defaults to DEFAULT_POOLSIZE.",
        "pool_block defaults to DEFAULT_POOLBLOCK.",
    ]
    assert code_facts(["A = 1 is written in the docs."]) == []


def test_focus_window_leads_with_the_matching_fact() -> None:
    from app.grounding.code_checks import focus_premise

    signature = "File: a.py (lines 1-3)\n    def f(\n        pool_size: int = DEFAULT_SIZE,\n    ):"
    constants = "File: a.py (lines 9-9)\nDEFAULT_SIZE = 10"
    window = focus_premise("pool_size defaults to 10.", [signature, constants])
    assert window.splitlines()[0] == "pool_size defaults to DEFAULT_SIZE, which is 10."
