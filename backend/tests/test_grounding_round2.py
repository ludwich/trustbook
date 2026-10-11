"""Gate 3 round 2: anchor matching (D-072), the confirmation veto on the cited chunks (D-073),
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
    Anchor,
    CodeJudge,
    Statement,
    absent_from_cited,
    assess,
    chunk_names,
    code_anchors,
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
        return {f"src/requests/{name}": [(1, 2000)] for name in names}


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
    assert normalize_inline("use `socks5h`, *really* or _maybe_") == "use socks5h, really or maybe"
    # Code is left alone: dunders, star arguments, products, private names.
    for text in ("def __init__(self, *args, **kwargs):", "y = 2*x*y", "_codes = {}", "a_b_c"):
        assert normalize_inline(text) == text


def test_quoted_phrases_match_through_markup_but_not_with_another_word() -> None:
    quote = "not a check to see if the response code is 200 OK."
    assert quote not in OK  # the raw text has **not** and ``200 OK``
    assert quoted_in(quote, OK)
    assert not quoted_in("not a check to see if the status code is 200 OK.", OK)
    assert not quoted_in("a check to see if the response code is 200 OK.", "is **not**")
    assert not quoted_in("not a check to see if the response code is 201 OK.", OK)


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


# --- D-073: the confirmation veto looks at the whole cited chunks -----------------------------

# src/requests/adapters.py, HTTPAdapter's class lines (excerpt).
ADAPTER = """\
File: src/requests/adapters.py · HTTPAdapter (lines 158-166)
class HTTPAdapter(BaseAdapter):
    \"\"\"The built-in HTTP Adapter for urllib3.

    :param max_retries: The maximum number of retries each connection
        should attempt. Note, this applies only to failed DNS lookups, socket
        connections and connection timeouts, never to requests where data has
        made it to the server. By default, Requests does not retry failed
        connections.
\"\"\"
"""
MAX_RETRIES = "When max_retries is left at its default, Requests does not retry failed connections."


def adapter_chunk() -> RetrievedChunk:
    return code("a", ADAPTER, 158, 166, "src/requests/adapters.py", "HTTPAdapter")


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


def test_a_pass_whose_evidence_lines_are_too_narrow_is_kept_when_the_cited_chunk_has_the_name() -> (
    None
):
    # The judge names lines 164-165 ("By default, Requests does not retry failed connections"),
    # which lack max_retries; the cited chunk has it on line 161 (measured, multi-01).
    narrow = verdicts((1, True, [("c1", 164, 165)]))
    check, calls = confirm(MAX_RETRIES, [adapter_chunk()], narrow)
    assert check.passed and not check.vetoed and check.method == "checker"
    assert calls == 1  # no veto, so nothing to ask again


def test_a_pass_naming_something_absent_from_every_cited_chunk_is_vetoed() -> None:
    sentence = "When max_retries is left at its default, Requests uses DEFAULT_RETRIES."
    narrow = verdicts((1, True, [("c1", 164, 165)]))
    check, calls = confirm(sentence, [adapter_chunk()], narrow, alone=narrow)
    assert not check.passed and check.vetoed and calls == 2
    assert check.missing == ("DEFAULT_RETRIES",)
    assert check.judge_reason == "not in the cited chunks: DEFAULT_RETRIES"


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
    narrow = verdicts((1, True, [("c1", 500, 500)]))  # the judge names the wrong line
    check, _ = confirm(sentence, [session], narrow)
    assert check.passed and not check.vetoed


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
    # Checker FAILED: the strict D-045 anchoring applies, the rest of the chunk does not count.
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
    assert check.judge_reason == "not in the evidence lines: max_retries (all in the cited chunks)"
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
    system = chat.calls[1]["messages"][0]["content"]
    assert f'1. "{first}" (cited c2): `requests.codes.teapot` is not in c2; it is in c1.' in system
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
    plain = answer_messages(QUESTION, found, failed=failed)[0]["content"]
    assert FEEDBACK_HEADING not in plain  # only the regeneration carries it
    assert (
        FEEDBACK_HEADING
        in answer_messages(QUESTION, found, regenerate=True, failed=failed)[0]["content"]
    )


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
