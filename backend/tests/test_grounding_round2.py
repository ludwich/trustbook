"""Gate 3 round 2: anchor matching (D-072), confirmation vetoes widened to docstring fields (D-073),
targeted regeneration feedback (D-074) and failed sentences in diagnostics (D-075)."""

from __future__ import annotations

import json

import pytest

from app.chat.pipeline import Answered, Refused, answer, failed_sentences
from app.chat.prompt import (
    FEEDBACK_HEADING,
    FEEDBACK_TASK,
    FailedSentence,
    answer_messages,
    failure_reason,
    labelled,
    regeneration_feedback,
)
from app.grounding.code_checks import (
    LineRange,
    SnapshotFiles,
    missing_identifiers,
    normalize_inline,
    quoted_in,
    verbatim,
)
from app.grounding.code_judge import (
    JUDGE_SYSTEM,
    Anchor,
    CodeJudge,
    Statement,
    absent_from_cited,
    assess,
    chunk_names,
    code_anchors,
    docstring_field,
    parse_verdicts,
    prepare,
)
from app.grounding.verify import Checked, SentenceCheck, Verifier
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.chat_helpers import ScriptedChat, answer_json


def verdicts(*items: tuple[int, bool, list[tuple[str, int, int]]]) -> str:
    return json.dumps(
        {
            "verdicts": [
                {
                    "index": index,
                    "supported": supported,
                    "evidence": [
                        {"chunk": c, "start_line": a, "end_line": b} for c, a, b in evidence
                    ],
                }
                for index, supported, evidence in items
            ]
        }
    )


class Snapshot(SnapshotFiles):
    """Every file these tests cite is in source s1's indexed snapshot (layer 5 passes)."""

    def __init__(self) -> None:
        super().__init__(None)

    def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
        names = ("adapters.py", "sessions.py", "status_codes.py", "models.py")
        return {f"src/requests/{name}": [(1, 2000)] for name in names} | {"HISTORY.md": [(1, 999)]}


SNAPSHOT = Snapshot()


def code(
    cid: str, text: str, start: int, end: int, file: str, symbol: str | None = None
) -> RetrievedChunk:
    locator = {"file": file, "start_line": start, "end_line": end, "symbol": symbol}
    return RetrievedChunk(cid, "s1", "requests", text, locator, 100, 0.9)


# --- D-072: inline markup, plural acronyms, dotted symbols ------------------------------------

# src/requests/models.py, Response.ok (psf/requests v2.34.2, Apache-2.0): an RST docstring.
OK = '''\
File: src/requests/models.py · Response.ok (lines 859-872)
    @property
    def ok(self) -> bool:
        """Returns True if :attr:`status_code` is less than 400, False if not.

        This attribute checks if the status code of the response is between
        400 and 600 to see if there was a client error or a server error. If
        the status code is between 200 and 400, this will return True. This
        is **not** a check to see if the response code is ``200 OK``.
        """
        try:
            self.raise_for_status()
        except HTTPError:
            return False
        return True
'''


def ok_chunk() -> RetrievedChunk:
    return code("ok", OK, 859, 872, "src/requests/models.py", "Response.ok")


def test_inline_markup_is_removed_and_whitespace_collapsed() -> None:
    assert (
        normalize_inline("is **not** a\n   check for ``200 OK``.") == "is not a check for 200 OK."
    )
    assert (
        normalize_inline("use `socks5h`, *really* or _maybe_") == "use socks5h, really or _maybe_"
    )
    # The sentence side keeps emphasis markers: `*test*` is a glob, not emphasis.
    assert normalize_inline("``*test*`` and `**x**`", emphasis=False) == "*test* and **x**"
    # Code is left alone: dunders, sunders, star arguments, products, private names.
    for text in (
        "def __init__(self, *args, **kwargs):",
        "y = 2*x*y",
        "_codes = {}",
        "a_b_c",
        "return self._value_",
        "def _missing_(cls, value):",
    ):
        assert normalize_inline(text) == text


def test_python_sunder_names_are_not_markdown_emphasis() -> None:
    # Review of D-072: `_value_` became `value` on both sides, and layer 5 passed names the
    # chunk lacks.
    assert missing_identifiers("It returns `self.value`.", ["return self._value_"]) == [
        "self.value"
    ]
    assert missing_identifiers("It sets `_value_`.", ["def f(value): return value"]) == ["_value_"]
    assert missing_identifiers("Override `_missing_`.", ["# handle a missing value"]) == [
        "_missing_"
    ]
    assert missing_identifiers("It returns `self._value_`.", ["return self._value_"]) == []


def test_quoted_phrases_match_through_markup_but_not_with_another_word() -> None:
    quote = "not a check to see if the response code is 200 OK."
    assert quote not in OK  # the raw text has **not** and ``200 OK``
    assert quoted_in(quote, OK)
    assert not quoted_in("not a check to see if the status code is 200 OK.", OK)
    assert not quoted_in("a check to see if the response code is 200 OK.", "is **not**")
    assert not quoted_in("not a check to see if the response code is 201 OK.", OK)


def test_a_quote_may_not_drop_a_negation_or_start_inside_a_word() -> None:
    # Review of D-072: "is **not** a check …" normalised contains "a check …".
    assert not quoted_in("a check to see if the response code is 200 OK.", OK)
    assert not quoted_in("a check to see if the response code is 200 OK", OK)
    assert not quoted_in("retry failed", "Requests doesn't retry failed connections.")
    assert not quoted_in("retry failed", "Requests will never retry failed connections.")
    assert quoted_in("retry failed", "Requests can retry failed connections.")
    assert quoted_in("does not retry", "Requests does not retry failed connections.")
    # Word boundaries: no match that starts or ends inside a word.
    assert not quoted_in("heck to see", OK)
    assert not quoted_in("the response co", OK)


def test_a_quote_that_drops_the_source_negation_is_vetoed() -> None:
    chunks, _ = prepare([("x", [ok_chunk()])])
    supported = parse_verdicts(verdicts((1, True, [("c1", 865, 866)])))[1]
    for text in (
        'Response.ok is "a check to see if the response code is 200 OK."',
        '`Response.ok` is "a check to see if the response code is 200 OK".',
    ):
        outcome = assess(Statement(text, ("c1",)), supported, chunks)
        assert not outcome.accepted and outcome.vetoed, text


def test_verbatim_spans_match_through_markup_and_keep_the_name_boundary() -> None:
    assert verbatim("200 OK", OK)
    assert verbatim("not a check", OK)  # "**not** a check"
    assert not verbatim("socks5", "use ``socks5h`` as the scheme")
    assert missing_identifiers("It is `not a check`.", [OK]) == []
    assert missing_identifiers("It is `not a test`.", [OK]) == ["not a test"]


def test_plural_acronyms_are_prose_words_but_names_stay_anchors() -> None:
    anchors = code_anchors("Requests sends URLs, APIs and IDs through an HTTPAdapter to MD5s.")
    texts = {a.text for a in anchors}
    assert not {"URLs", "APIs", "IDs"} & texts
    assert Anchor("identifier", "HTTPAdapter") in anchors


def test_a_dotted_symbol_names_each_of_its_parts() -> None:
    send = code(
        "s",
        "File: src/requests/adapters.py · HTTPAdapter.send (lines 434-436)\n    def send(self):\n",
        434,
        436,
        "src/requests/adapters.py",
        "HTTPAdapter.send",
    )
    names = chunk_names(send)
    assert {"HTTPAdapter.send", "HTTPAdapter", "send"} <= set(names)
    part = code(
        "p",
        "File: src/requests/status_codes.py · _codes (part 6) (lines 77-85)\n    429: (),\n",
        77,
        85,
        "src/requests/status_codes.py",
        "_codes",
    )
    assert "_codes" in chunk_names(part) and "6" not in chunk_names(part)
    chunks, statements = prepare([("The HTTPAdapter's send method sends it.", [send])])
    supported = parse_verdicts(verdicts((1, True, [("c1", 434, 434)])))[1]
    assert assess(statements[0], supported, chunks).accepted


def test_a_quoted_docstring_phrase_is_accepted_and_a_changed_word_is_vetoed() -> None:
    chunks, _ = prepare([("x", [ok_chunk()])])
    supported = parse_verdicts(verdicts((1, True, [("c1", 865, 866)])))[1]
    true = Statement('Response.ok is "not a check to see if the response code is 200 OK."', ("c1",))
    assert assess(true, supported, chunks).accepted
    false = Statement('Response.ok is "not a check to see if the status code is 200 OK."', ("c1",))
    outcome = assess(false, supported, chunks)
    assert not outcome.accepted and outcome.vetoed
    assert outcome.absent == ("not a check to see if the status code is 200 OK.",)


# --- D-073: a confirmation widens the evidence lines to their docstring field ------------------

# src/requests/adapters.py, HTTPAdapter's class lines (excerpt).
ADAPTER = """\
File: src/requests/adapters.py · HTTPAdapter (lines 158-167)
class HTTPAdapter(BaseAdapter):
    \"\"\"The built-in HTTP Adapter for urllib3.

    :param max_retries: The maximum number of retries each connection
        should attempt. Note, this applies only to failed DNS lookups, socket
        connections and connection timeouts, never to requests where data has
        made it to the server. By default, Requests does not retry failed
        connections.
    :param pool_block: Whether the connection pool should block for connections.
\"\"\"
"""
MAX_RETRIES = "When max_retries is left at its default, Requests does not retry failed connections."


def adapter_chunk() -> RetrievedChunk:
    return code("a", ADAPTER, 158, 167, "src/requests/adapters.py", "HTTPAdapter")


def confirm(
    sentence: str, chunks: list[RetrievedChunk], batch: str, alone: str | None = None
) -> tuple[SentenceCheck, int]:
    """Confirm a checker pass (score 0.9) of ``sentence``; ``alone`` is the repeat's reply."""
    chat = ScriptedChat(batch, *([alone] if alone is not None else []))
    verifier = Verifier(
        lambda pairs: [0.9 for _ in pairs],
        threshold=0.5,
        files=SNAPSHOT,
        code_judge=CodeJudge(lambda: chat),
    )
    return verifier.check([(sentence, chunks)]).checks[0], len(chat.calls)


def test_docstring_fields_span_their_indented_continuation_lines() -> None:
    lines = ADAPTER.split("\n")[1:]
    assert docstring_field(lines, 3) == (3, 7)  # the :param max_retries: line itself
    assert docstring_field(lines, 6) == (3, 7)  # a continuation line
    assert docstring_field(lines, 8) == (8, 8)  # :param pool_block:
    assert docstring_field(lines, 0) is None  # class line
    assert docstring_field(lines, 1) is None  # docstring summary
    google = [
        "    Args:",
        "        retries (int): How many times to retry. By default",
        "            nothing is retried.",
        "        block: Whether to block.",
        "    Returns:",
        "        The adapter.",
    ]
    assert docstring_field(google, 2) == (1, 2)
    assert docstring_field(google, 3) == (3, 3)
    # Code and changelog list items are no fields: an annotated parameter, a bullet.
    code_lines = [
        "    def __init__(",
        "        self,",
        "        pool_connections: int = DEFAULT_POOLSIZE,",
        "        pool_block: bool = DEFAULT_POOLBLOCK,",
        "    ):",
    ]
    assert all(docstring_field(code_lines, i) is None for i in range(len(code_lines)))
    bullets = ["- Requests now supports optional use of character detection", "  (`chardet`)."]
    assert docstring_field(bullets, 1) is None


def test_a_pass_whose_evidence_is_a_continuation_line_of_the_named_field_is_kept() -> None:
    # The judge names lines 164-165 ("By default, Requests does not retry failed connections"),
    # which lack max_retries; their field starts with ":param max_retries:" (measured, multi-01).
    narrow = verdicts((1, True, [("c1", 164, 165)]))
    check, calls = confirm(MAX_RETRIES, [adapter_chunk()], narrow)
    assert check.passed and not check.vetoed and check.method == "checker"
    assert calls == 1  # no veto, so nothing to ask again


def test_a_name_elsewhere_in_the_cited_chunk_does_not_save_a_confirmation() -> None:
    # pool_block is in the chunk, but not in the field of the lines the judge named.
    sentence = "When pool_block is left at its default, Requests does not retry failed connections."
    narrow = verdicts((1, True, [("c1", 164, 165)]))
    check, calls = confirm(sentence, [adapter_chunk()], narrow, alone=narrow)
    assert not check.passed and check.vetoed and calls == 2
    assert check.judge_reason == "not in the evidence lines: pool_block"
    assert check.missing == ()  # pool_block IS in the cited chunk: no feedback names it


def test_a_pass_naming_something_absent_from_every_cited_chunk_is_vetoed() -> None:
    sentence = "When max_retries is left at its default, Requests uses DEFAULT_RETRIES."
    narrow = verdicts((1, True, [("c1", 164, 165)]))
    check, calls = confirm(sentence, [adapter_chunk()], narrow, alone=narrow)
    assert not check.passed and check.vetoed and calls == 2
    assert check.missing == ("DEFAULT_RETRIES",)
    assert check.judge_reason == "not in the cited chunks: DEFAULT_RETRIES"


def test_a_failed_rescue_names_what_no_cited_chunk_holds_for_the_feedback() -> None:
    # D-082 (gate run ref-near-10): a sentence that failed only the checker and that the judge
    # does not rescue gets the names no cited chunk holds, so the regeneration feedback can say
    # where they are. The sentence fails either way, and nothing is asked again.
    sentence = "When max_retries is left at its default, Requests uses DEFAULT_RETRIES."
    chat = ScriptedChat(verdicts((1, True, [("c1", 164, 165)])))
    verifier = Verifier(
        lambda pairs: [0.1 for _ in pairs],
        threshold=0.5,
        files=SNAPSHOT,
        code_judge=CodeJudge(lambda: chat),
    )
    check = verifier.check([(sentence, [adapter_chunk()])]).checks[0]
    assert not check.passed and not check.vetoed and len(chat.calls) == 1
    assert check.missing == ("DEFAULT_RETRIES",)
    assert check.judge_reason == "not in the cited chunks: DEFAULT_RETRIES"


def test_the_judge_is_asked_for_a_line_for_every_name_from_each_excerpt() -> None:
    # D-082: "the fewest lines" alone made the judge name only the value's line when a sentence
    # combined two cited chunks; the anchor check on the named lines is unchanged.
    flat = " ".join(JUDGE_SYSTEM.split())
    assert "give a line for every name and value the statement uses" in flat
    assert "give lines from each" in flat


# HISTORY.md lines 186-206 (psf/requests v2.34.2, Apache-2.0): the 2.32.0 heading is in the
# previous chunk, the next release's heading below the entry (gate run ref-near-03).
HISTORY = """\
File: HISTORY.md (lines 186-206)
**Deprecations**

- Requests has officially added support for CPython 3.12 (#6503)
- Requests has officially added support for PyPy 3.9 and 3.10 (#6641)

**Packaging**
- Requests has started adopting some modern packaging practices.
  The source files for the projects (formerly `requests`) is now located
  in `src/requests` in the Requests sdist. (#6506)
- Starting in Requests 2.33.0, Requests will migrate to a PEP 517 build system
  using `hatchling`. This should not impact the average user, but extremely old
  versions of packaging utilities may have issues with the new packaging format.
- Placeholder line one.
- Placeholder line two.
- Placeholder line three.
- Placeholder line four.
- Placeholder line five.


2.31.0 (2023-05-22)
-------------------
"""


def test_a_value_from_another_line_of_the_chunk_does_not_save_a_confirmation() -> None:
    # Review of D-073: the measured CORRECT veto of ref-near-03 ("not in the evidence lines: 2.31").
    history = code("h", HISTORY, 186, 206, "HISTORY.md")
    one_line = verdicts((1, True, [("c1", 188, 188)]))
    for sentence in (
        "Requests officially added support for CPython 3.12 in version 2.31.",
        "Requests officially added support for CPython 3.12 in the release preceding 2.31.0.",
    ):
        check, calls = confirm(sentence, [history], one_line, alone=one_line)
        assert not check.passed and check.vetoed and calls == 2, sentence
        assert check.judge_reason.startswith("not in the evidence lines: 2.31")


CONSTANTS = """\
File: src/requests/adapters.py · module (lines 60-64)
DEFAULT_POOLBLOCK = False
DEFAULT_POOLSIZE = 10
DEFAULT_RETRIES = 0
DEFAULT_POOL_TIMEOUT = None
"""


@pytest.mark.parametrize(
    ("sentence", "line"),
    [
        ("`DEFAULT_RETRIES` is 10 by default.", 61),  # the value of a sibling constant
        ("`DEFAULT_POOLSIZE` is 64.", 61),  # 64 is only in the header's line range
        ("`DEFAULT_POOLSIZE` is 0.", 61),
    ],
)
def test_a_swapped_value_in_the_same_chunk_is_vetoed(sentence: str, line: int) -> None:
    constants = code("k", CONSTANTS, 60, 63, "src/requests/adapters.py")
    reply = verdicts((1, True, [("c1", line, line)]))
    check, _ = confirm(sentence, [constants], reply, alone=reply)
    # Vetoed by the judge's evidence lines, or already failed by layer 5: header line numbers are
    # not stated values (D-071), so 64 never reaches the judge.
    assert not check.passed and (check.vetoed or check.problems), sentence


def test_a_swapped_name_or_number_on_a_prose_page_is_vetoed() -> None:
    page = RetrievedChunk(
        "w",
        "w",
        "Warranty",
        "The warranty lasts 12 months from delivery.\nReturns are accepted within 30 days.",
        {"heading": "Terms"},
        30,
        0.9,
    )
    reply = verdicts((1, True, [("c1", 1, 1)]))
    check, _ = confirm("The warranty lasts 30 days from delivery.", [page], reply, reply)
    assert not check.passed and check.vetoed
    init = code(
        "i",
        "File: src/requests/adapters.py · HTTPAdapter.__init__ (lines 200-204)\n"
        "    def __init__(\n"
        "        self,\n"
        "        pool_connections: int = DEFAULT_POOLSIZE,\n"
        "        pool_block: bool = DEFAULT_POOLBLOCK,\n"
        "    ):\n",
        200,
        204,
        "src/requests/adapters.py",
        "HTTPAdapter.__init__",
    )
    reply = verdicts((1, True, [("c1", 203, 203)]))
    check, _ = confirm("By default `pool_block` is set to DEFAULT_POOLSIZE.", [init], reply, reply)
    assert not check.passed and check.vetoed


def test_the_class_named_only_by_the_chunk_symbol_counts_for_a_confirmation() -> None:
    session = code(
        "m",
        "File: src/requests/sessions.py · Session.__init__ (lines 500-502)\n"
        "        self.adapters = OrderedDict()\n"
        '        self.mount("https://", HTTPAdapter())\n'
        '        self.mount("http://", HTTPAdapter())\n',
        500,
        502,
        "src/requests/sessions.py",
        "Session.__init__",
    )
    sentence = "A new Session mounts one HTTPAdapter for HTTP and one for HTTPS."
    right = verdicts((1, True, [("c1", 501, 502)]))
    check, _ = confirm(sentence, [session], right)
    assert check.passed and not check.vetoed
    wrong = verdicts((1, True, [("c1", 500, 500)]))  # the judge names a line without it
    check, _ = confirm(sentence, [session], wrong, wrong)
    assert not check.passed and check.vetoed


def test_a_judge_that_says_unsupported_still_vetoes() -> None:
    no = verdicts((1, False, []))
    check, calls = confirm(MAX_RETRIES, [adapter_chunk()], no, alone=no)
    assert check.vetoed and not check.passed and calls == 2
    assert check.judge_reason == "the judge found no support"


def test_evidence_lines_holding_a_longer_name_still_veto() -> None:
    # `socks5` is in the cited page, but the judge's line names socks5h: it conflated the two.
    page = RetrievedChunk(
        "w",
        "w",
        "Advanced Usage",
        "Using the scheme socks5 resolves DNS on the client.\n"
        "To resolve the domains on the proxy server, use socks5h as the scheme.",
        {"heading": "SOCKS"},
        30,
        0.9,
    )
    reply = verdicts((1, True, [("c1", 2, 2)]))
    check, _ = confirm("Use `socks5` to resolve domains on the proxy.", [page], reply, reply)
    assert check.vetoed and "longer name" in check.judge_reason


def test_a_rescue_still_needs_every_anchor_in_the_evidence_lines() -> None:
    # Checker FAILED: the strict D-045 anchoring applies, the docstring field does not count.
    narrow = verdicts((1, True, [("c1", 164, 165)]))
    chat = ScriptedChat(narrow, narrow)
    verifier = Verifier(
        lambda pairs: [0.1 for _ in pairs],
        threshold=0.5,
        files=SNAPSHOT,
        code_judge=CodeJudge(lambda: chat),
    )
    check = verifier.check([(MAX_RETRIES, [adapter_chunk()])]).checks[0]
    assert not check.passed and check.method is None and not check.vetoed
    assert check.judge_reason == "not in the evidence lines: max_retries (in their docstring field)"
    assert len(chat.calls) == 1  # not accepted in the batch: no second call


def test_a_split_literal_part_number_is_not_found_in_the_cited_chunks() -> None:
    part = code(
        "p",
        "File: src/requests/status_codes.py · _codes (part 6) (lines 77-85)\n"
        '    429: ("too_many_requests", "too_many"),\n',
        77,
        85,
        "src/requests/status_codes.py",
        "_codes",
    )
    chunks, statements = prepare([("Requests retries 6 times.", [part])])
    assert absent_from_cited([Anchor("number", "6")], statements[0], chunks) == ["6"]


# --- D-074: the regeneration is told what failed and where the names are ----------------------

CODES_DOC = "File: src/requests/status_codes.py (lines 1-3)\nUse ``requests.codes.teapot``.\n"
TABLE = 'File: src/requests/status_codes.py · _codes (lines 84-85)\n    429: ("too_many",),\n'


def test_the_feedback_names_each_missing_name_and_the_chunks_that_have_it() -> None:
    failed = [
        FailedSentence("Use `requests.codes.too_many`.", ("c2",), (("requests.codes", ("c1",)),)),
        FailedSentence("It is `rate_limited`.", ("c2",), (("rate_limited", ()),)),
        FailedSentence('He said "hi" here.', ("c1", "c2")),
    ]
    assert failure_reason(failed[0]) == "`requests.codes` is not in c2; it is in c1"
    assert failure_reason(failed[1]) == "`rate_limited` is in none of the chunks"
    assert regeneration_feedback(failed) == "\n".join(
        [
            FEEDBACK_HEADING,
            '1. "Use `requests.codes.too_many`." (cited c2): `requests.codes` is not in c2; it is '
            "in c1.",
            '2. "It is `rate_limited`." (cited c2): `rate_limited` is in none of the chunks.',
            "3. \"He said 'hi' here.\" (cited c1, c2): not supported by the cited chunks.",
            FEEDBACK_TASK,
        ]
    )
    assert regeneration_feedback([]) == ""
    long = FailedSentence("word " * 200, ("c1",))
    assert len(regeneration_feedback([long]).splitlines()[1]) < 400


def test_failed_sentences_find_the_context_chunks_holding_the_missing_names() -> None:
    chunks = labelled(
        [
            code("d", CODES_DOC, 1, 3, "src/requests/status_codes.py"),
            code("t", TABLE, 84, 85, "src/requests/status_codes.py", "_codes"),
        ]
    )
    from app.chat.output import DraftSentence

    sentences = [
        DraftSentence("Use `requests.codes.too_many` for 429.", ("c2",)),
        DraftSentence("Fine.", ("c1",)),
    ]
    checked = Checked(
        [
            SentenceCheck(0.1, (0.1,), passed=False, missing=("requests.codes.too_many",)),
            SentenceCheck(0.9, (0.9,)),
        ]
    )
    # Layer 5's test: every part of requests.codes.too_many is in neither chunk alone.
    assert failed_sentences(sentences, checked, chunks) == [
        FailedSentence(sentences[0].text, ("c2",), (("requests.codes.too_many", ()),))
    ]
    checked = Checked(
        [
            SentenceCheck(0.1, (0.1,), passed=False, missing=("requests.codes",)),
            SentenceCheck(0.9, (0.9,)),
        ]
    )
    assert failed_sentences(sentences, checked, chunks)[0].missing == (("requests.codes", ("c1",)),)


QUESTION = "Which names compare equal to HTTP 429?"


def run(chat: ScriptedChat, scores: dict[str, float]) -> Answered | Refused:
    found = [
        code("d", CODES_DOC, 1, 3, "src/requests/status_codes.py"),
        code("t", TABLE, 84, 85, "src/requests/status_codes.py", "_codes"),
    ]
    return answer(
        QUESTION,
        retrieve=lambda _q: Retrieval(chunks=found, ranked=found, source_ids=["s1"]),
        chat_adapter=lambda: chat,
        refuse_threshold=0.15,
        verifier=Verifier(
            lambda pairs: [scores.get(h, 0.9) for _, h in pairs], threshold=0.5, files=SNAPSHOT
        ),
    )


def test_the_regeneration_gets_the_feedback_and_its_citations_are_checked_as_given() -> None:
    first = "Use `requests.codes.teapot` for 429."
    chat = ScriptedChat(
        answer_json((first, ["c2"]), ("The table maps 429.", ["c2"])),
        # The second reply follows the hint but still cites c2 only: it is verified as given,
        # so the backticked name is still missing from its citation (layer 5).
        answer_json((first, ["c2"]), ("The table maps 429.", ["c2"])),
    )
    outcome = run(chat, {})
    assert isinstance(outcome, Refused) and outcome.refusal.reason == "unsupported"
    user = chat.calls[1]["messages"][1]["content"]
    assert f'1. "{first}" (cited c2): `requests.codes.teapot` is not in c2; it is in c1.' in user
    assert [f.model_dump() for f in outcome.diagnostics.failed_sentences] == [
        {"text": first, "reason": "`requests.codes.teapot` is not in the cited chunks"}
    ]


def test_a_partially_verified_answer_lists_its_failed_sentence_capped() -> None:
    long = "Unsupported " + "detail " * 80 + "here."
    chat = ScriptedChat(
        answer_json(("The table maps 429.", ["c2"]), ("Docs exist.", ["c1"]), (long, ["c1"]))
    )
    outcome = run(chat, {long: 0.2})
    assert isinstance(outcome, Answered) and outcome.badge == "partially_verified"
    (failed,) = outcome.diagnostics.failed_sentences
    assert len(failed.text) == 300 and failed.text.endswith("…")
    assert failed.reason == "checker score 0.20 < 0.5"
    assert len(chat.calls) == 1


def test_a_verified_answer_has_no_failed_sentences() -> None:
    chat = ScriptedChat(answer_json(("The table maps 429.", ["c2"])))
    outcome = run(chat, {})
    assert isinstance(outcome, Answered) and outcome.diagnostics.failed_sentences == []


def test_the_first_answer_prompt_has_no_feedback() -> None:
    found = [code("d", CODES_DOC, 1, 3, "src/requests/status_codes.py")]
    failed = [FailedSentence("x", ("c1",))]
    plain = answer_messages(QUESTION, found, failed=failed)
    assert all(FEEDBACK_HEADING not in m["content"] for m in plain)  # only the regeneration
    again = answer_messages(QUESTION, found, regenerate=True, failed=failed)
    assert FEEDBACK_HEADING in again[1]["content"]  # in the user message (D-079)
    assert again[0] == plain[0]  # the system message is the same on both attempts


@pytest.mark.parametrize(
    ("check", "reason"),
    [
        (SentenceCheck(0.2, (0.2,), passed=False), "checker score 0.20 < 0.5"),
        (
            SentenceCheck(0.2, (0.2,), passed=False, judge_reason="the judge found no support"),
            "checker score 0.20 < 0.5; judge: the judge found no support",
        ),
        (
            SentenceCheck(0.9, (0.9,), passed=False, vetoed=True, judge_reason="x"),
            "judge veto: x",
        ),
        (
            SentenceCheck(0.9, (0.9,), problems=("`a` is not in the cited chunks",), passed=False),
            "`a` is not in the cited chunks",
        ),
        (SentenceCheck(None, (), passed=False), "no citations"),
        (SentenceCheck(0.9, (0.9,)), ""),
    ],
)
def test_failure_reasons(check: SentenceCheck, reason: str) -> None:
    assert check.failure(0.5) == reason
