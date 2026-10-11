"""Code claim verification (D-045): the code judge, evidence anchoring and anchor matching."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.adapters.base import ChatMessage, ChatResult
from app.chat.pipeline import Answered, answer
from app.grounding.code_checks import LineRange, SnapshotFiles
from app.grounding.code_judge import (
    JUDGE_SCHEMA,
    Anchor,
    CodeJudge,
    Statement,
    accept,
    code_anchors,
    content_share,
    content_words,
    judge_messages,
    number_chunk,
    parse_judge_prompt,
    parse_verdicts,
    prepare,
    split_identifier,
    terms_match,
)
from app.grounding.verify import GroundingSettings, Verifier, build_verifier
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.chat_helpers import ScriptedChat, answer_json

# src/requests/auth.py lines 34-75 of psf/requests v2.34.2 (Apache-2.0), the smoke corpus.
BASIC_AUTH = '''\
File: src/requests/auth.py (lines 34-75)
def _basic_auth_str(username: bytes | str, password: bytes | str) -> str:
    """Returns a Basic Auth string."""

    # "I want us to put a big-ol' comment on top of it that
    # says that this behaviour is dumb but we need to preserve
    # it because people are relying on it."
    #    - Lukasa
    #
    # These are here solely to maintain backwards compatibility
    # for things like ints. This will be removed in 3.0.0.
    if not isinstance(username, basestring):  # type: ignore[reportUnnecessaryIsInstance]
        warnings.warn(
            "Non-string usernames will no longer be supported in Requests "
            f"3.0.0. Please convert the object you've passed in ({username!r}) to "
            "a string or bytes object in the near future to avoid "
            "problems.",
            category=DeprecationWarning,
        )
        username = str(username)

    if not isinstance(password, basestring):  # type: ignore[reportUnnecessaryIsInstance]
        warnings.warn(
            "Non-string passwords will no longer be supported in Requests "
            f"3.0.0. Please convert the object you've passed in ({type(password)!r}) to "
            "a string or bytes object in the near future to avoid "
            "problems.",
            category=DeprecationWarning,
        )
        password = str(password)
    # -- End Removal --

    if isinstance(username, str):
        username = username.encode("latin1")

    if isinstance(password, str):
        password = password.encode("latin1")

    authstr = "Basic " + to_native_string(
        b64encode(b":".join((username, password))).strip()
    )

    return authstr
'''

TRUE_CLAIM = (
    "Basic authentication joins the username and password with a colon and base64-encodes them."
)
SEMICOLON = "Basic authentication joins the username and password with a semicolon."
SHA1 = "Basic authentication hashes the username and password with SHA-1."


def code_chunk(
    cid: str = "auth",
    text: str = BASIC_AUTH,
    start: int = 34,
    end: int = 75,
    file: str = "src/requests/auth.py",
) -> RetrievedChunk:
    locator = {"file": file, "start_line": start, "end_line": end, "symbol": None}
    return RetrievedChunk(cid, "s1", "requests", text, locator, 400, 0.9)


def prose_chunk(cid: str = "doc") -> RetrievedChunk:
    return RetrievedChunk(
        cid, "s2", "Docs", "Basic Auth sends a user name and password.", {}, 10, 0.9
    )


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
    """The indexed snapshot of the requests folder source ``s1`` (layer 5 passes)."""

    def __init__(self) -> None:
        super().__init__(None)

    def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
        return {"src/requests/auth.py": [(1, 400)]} if source_id == "s1" else {}


SNAPSHOT = Snapshot()


def space_folder(tmp: Path) -> Path:
    """A space folder whose source ``s1`` has auth.py in its indexed snapshot."""
    folder = tmp / "sources" / "s1"
    folder.mkdir(parents=True)
    segment = {"file": "src/requests/auth.py", "start_line": 1, "end_line": 400}
    (folder / "map.json").write_text(json.dumps({"segments": [segment]}))
    return tmp


def low(pairs: Sequence[tuple[str, str]]) -> list[float]:
    """HHEM on claims about code: everything scores low (D-044)."""
    return [0.05 for _ in pairs]


def judged(
    reply: str, sentences: list[tuple[str, list[RetrievedChunk]]]
) -> tuple[list[bool], list[str | None], ScriptedChat]:
    chat = ScriptedChat(reply)
    verifier = Verifier(low, threshold=0.5, files=SNAPSHOT, code_judge=CodeJudge(lambda: chat))
    checked = verifier.check(sentences)
    return [c.passed for c in checked.checks], list(checked.methods), chat


# --- the prompt ------------------------------------------------------------------------------


def test_judge_sees_only_cited_code_with_real_line_numbers() -> None:
    chunks, _statements = prepare([(TRUE_CLAIM, [code_chunk()])])
    numbered = chunks["c1"]
    assert (numbered.first, numbered.last) == (34, 75)
    assert numbered.lines[0].startswith("def _basic_auth_str(")  # the File: header is not numbered
    assert numbered.text(71, 72).splitlines()[1].strip().startswith('b64encode(b":".join')
    _, _, chat = judged(verdicts((1, True, [("c1", 71, 73)])), [(TRUE_CLAIM, [code_chunk()])])
    call = chat.calls[0]
    assert (call["temperature"], call["think"], call["schema"]) == (0.0, False, JUDGE_SCHEMA)
    user = call["messages"][1]["content"]
    assert "[c1] src/requests/auth.py, lines 34-75" in user
    assert '71 |     authstr = "Basic " + to_native_string(' in user
    assert "34 | def _basic_auth_str(" in user
    assert "File: " not in user
    assert f"1. (cites c1) {TRUE_CLAIM}" in user
    system = call["messages"][0]["content"]
    assert "naming conventions" in system and "is not support" in system
    # The mock adapter reads the prompt back.
    parsed_chunks, parsed = parse_judge_prompt(user)
    assert parsed == [Statement(TRUE_CLAIM, ("c1",))]
    assert parsed_chunks["c1"][37] == (71, '    authstr = "Basic " + to_native_string(')


def test_true_claim_passes_via_the_judge() -> None:
    passed, methods, _ = judged(
        verdicts((1, True, [("c1", 71, 73)])), [(TRUE_CLAIM, [code_chunk()])]
    )
    assert (passed, methods) == ([True], ["code_judge"])


@pytest.mark.parametrize("claim", [SEMICOLON, SHA1])
def test_false_claims_fail_even_when_the_judge_says_supported(claim: str) -> None:
    passed, methods, _ = judged(verdicts((1, True, [("c1", 71, 73)])), [(claim, [code_chunk()])])
    assert (passed, methods) == ([False], [None])


def test_false_claims_fail_however_much_evidence_the_judge_names() -> None:
    whole = verdicts((1, True, [("c1", 34, 75)]))
    for claim in (SEMICOLON, SHA1):
        assert judged(whole, [(claim, [code_chunk()])])[0] == [False]


def test_evidence_may_span_several_ranges_inside_the_cited_chunk() -> None:
    reply = verdicts((1, True, [("c1", 71, 71), ("c1", 72, 73)]))
    assert judged(reply, [(TRUE_CLAIM, [code_chunk()])])[0] == [True]


@pytest.mark.parametrize(
    "evidence",
    [
        [("c1", 30, 73)],  # starts before the chunk
        [("c1", 71, 76)],  # ends after it
        [("c1", 73, 71)],  # backwards
        [("c9", 71, 73)],  # a chunk that was never shown
        [],  # no evidence
    ],
)
def test_evidence_outside_the_cited_ranges_fails(evidence: list[tuple[str, int, int]]) -> None:
    assert judged(verdicts((1, True, evidence)), [(TRUE_CLAIM, [code_chunk()])])[0] == [False]


def test_evidence_in_a_chunk_the_sentence_does_not_cite_fails() -> None:
    other = code_chunk("other", BASIC_AUTH.replace("lines 34-75", "lines 134-175"), 134, 175)
    reply = verdicts((1, True, [("c2", 171, 173)]), (2, False, []))
    passed, _, chat = judged(reply, [(TRUE_CLAIM, [code_chunk()]), ("Other claim.", [other])])
    assert passed == [False, False]
    assert "1. (cites c1)" in chat.calls[0]["messages"][1]["content"]


def test_unsupported_verdicts_and_missing_verdicts_fail() -> None:
    sentences = [(TRUE_CLAIM, [code_chunk()]), (TRUE_CLAIM + " Again.", [code_chunk()])]
    assert judged(verdicts((1, False, [("c1", 71, 73)])), sentences)[0] == [False, False]


# --- what goes to the judge ------------------------------------------------------------------


def test_every_sentence_that_failed_only_the_checker_is_judged() -> None:
    # D-056: rescue no longer depends on the citation type; prose chunks are shown too.
    chat = ScriptedChat(verdicts((1, True, [("c1", 71, 73)]), (2, False, [])))
    scores = {TRUE_CLAIM: 0.05, "Prose claim.": 0.05, "Checker passes this.": 0.9}

    def checker(pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [scores.get(h, 0.05) for _, h in pairs]

    verifier = Verifier(
        checker,
        threshold=0.5,
        files=SNAPSHOT,
        code_judge=CodeJudge(lambda: chat),
        confirm_below=0.8,
    )
    checked = verifier.check(
        [
            (TRUE_CLAIM, [code_chunk(), prose_chunk()]),  # judged with both chunks
            ("Prose claim.", [prose_chunk()]),  # judged, not supported
            ("Checker passes this.", [code_chunk()]),  # passed above the confirmation band
            ("Uses `no_such_name` here.", [code_chunk()]),  # layer 5 failure
        ]
    )
    assert [c.passed for c in checked.checks] == [True, False, True, False]
    assert checked.methods == ["code_judge", None, "checker", None]
    assert len(chat.calls) == 1  # one call per answer
    user = chat.calls[0]["messages"][1]["content"]
    assert user.count("(cites") == 2 and "Basic Auth sends" in user
    assert "1. (cites c1, c2)" in user and "2. (cites c2) Prose claim." in user
    assert verifier.judge_calls == 1 and verifier.judge_seconds >= 0


# --- confirmation of borderline checker passes (D-056) ----------------------------------------

SOCKS = (
    "Using the scheme socks5 causes the DNS resolution to happen on the client, rather than on "
    "the proxy server. This is in line with curl, which uses the scheme to decide whether to do "
    "the DNS resolution on the client or proxy. If you want to resolve the domains on the proxy "
    "server, use socks5h as the scheme."
)


def socks_chunk() -> RetrievedChunk:
    return RetrievedChunk("socks", "w", "Advanced Usage", SOCKS, {"heading": "SOCKS"}, 80, 0.9)


SWAPPED = "With socks5h, DNS resolution happens on the client."
RIGHT = "With socks5h, the domains are resolved on the proxy server."


def confirmed(reply: str, score: float, confirm_below: float = 0.8) -> tuple[bool, bool, int]:
    chat = ScriptedChat(reply)
    verifier = Verifier(
        lambda pairs: [score for _ in pairs],
        threshold=0.5,
        code_judge=CodeJudge(lambda: chat),
        confirm_below=confirm_below,
    )
    check = verifier.check([(SWAPPED, [socks_chunk()])]).checks[0]
    return check.passed, check.vetoed, len(chat.calls)


def test_a_borderline_pass_the_judge_contradicts_fails() -> None:
    passed, vetoed, calls = confirmed(verdicts((1, False, [])), 0.6)
    assert (passed, vetoed, calls) == (False, True, 1)


def test_a_borderline_pass_with_evidence_outside_its_citations_fails() -> None:
    passed, vetoed, _ = confirmed(verdicts((1, True, [("c2", 1, 1)])), 0.6)
    assert (passed, vetoed) == (False, True)


def test_a_confirmed_pass_and_a_judge_without_an_answer_keep_the_pass() -> None:
    assert confirmed(verdicts((1, True, [("c1", 3, 3)])), 0.6)[:2] == (True, False)
    assert confirmed(verdicts(), 0.6)[:2] == (True, False)  # no verdict: no veto
    assert confirmed("not json", 0.6)[:2] == (True, False)  # judge failed: checker stands


def test_passes_at_or_above_the_bound_are_not_sent() -> None:
    assert confirmed(verdicts((1, False, [])), 0.8) == (True, False, 0)


def test_vetoed_sentences_score_zero_for_the_threshold_sweep() -> None:
    chat = ScriptedChat(verdicts((1, False, [])))
    verifier = Verifier(
        lambda pairs: [0.6 for _ in pairs],
        threshold=0.5,
        code_judge=CodeJudge(lambda: chat),
        confirm_below=0.8,
    )
    checked = verifier.check([(SWAPPED, [socks_chunk()])])
    assert checked.vetoed == [0] and checked.checks[0].diagnostic_score == 0.0
    assert checked.methods == [None] and checked.refuse


def test_prose_is_numbered_a_sentence_per_line_under_its_title() -> None:
    chunks, _ = prepare([(RIGHT, [socks_chunk()])])
    numbered = chunks["c1"]
    assert (numbered.first, numbered.last) == (1, 3)
    assert (
        numbered.lines[2]
        == "If you want to resolve the domains on the proxy server, use socks5h as the scheme."
    )
    user = judge_messages(list(chunks.values()), [Statement(RIGHT, ("c1",))])[1]["content"]
    assert "[c1] Advanced Usage (section “SOCKS”), lines 1-3" in user
    assert "3 | If you want to resolve" in user


def test_prose_claim_is_rescued_only_with_its_words_in_the_evidence_sentence() -> None:
    chunk = socks_chunk()
    reply = verdicts((1, True, [("c1", 3, 3)]))
    chat = ScriptedChat(reply)
    verifier = Verifier(low, threshold=0.5, code_judge=CodeJudge(lambda: chat))
    assert (
        verifier.check([("Use `socks5h` to resolve domains on the proxy.", [chunk])])
        .checks[0]
        .passed
    )
    chat = ScriptedChat(reply)
    verifier = Verifier(low, threshold=0.5, code_judge=CodeJudge(lambda: chat))
    # socks5 is not in line 3: anchoring rejects the judge's support.
    check = verifier.check([("Use `socks5` to resolve domains on the proxy.", [chunk])]).checks[0]
    assert not check.passed


def test_judge_off_is_phase_2_behaviour(tmp_path: Path) -> None:
    chat = ScriptedChat()  # any call would fail the test
    verifier = build_verifier(
        GroundingSettings(code_judge=False), low, space_folder(tmp_path), CodeJudge(lambda: chat)
    )
    checked = verifier.check([(TRUE_CLAIM, [code_chunk()])])
    assert ([c.passed for c in checked.checks], checked.methods) == ([False], [None])
    assert chat.calls == []


def test_checker_off_never_calls_the_judge(tmp_path: Path) -> None:
    chat = ScriptedChat()
    verifier = build_verifier(
        GroundingSettings(enabled=False), low, space_folder(tmp_path), CodeJudge(lambda: chat)
    )
    checked = verifier.check([(TRUE_CLAIM, [code_chunk()])])
    assert ([c.passed for c in checked.checks], checked.methods) == ([True], [None])
    assert chat.calls == []


class Broken:
    def chat(self, messages: Sequence[ChatMessage], **_: Any) -> ChatResult:
        raise RuntimeError("endpoint down")

    def list_models(self) -> list[str]:
        return []


class Slow:
    def __init__(self) -> None:
        self.release = threading.Event()

    def chat(self, messages: Sequence[ChatMessage], **_: Any) -> ChatResult:
        self.release.wait(5)
        return ChatResult(verdicts((1, True, [("c1", 71, 73)])))

    def list_models(self) -> list[str]:
        return []


def test_judge_failures_and_timeouts_keep_the_checker_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    items = [(TRUE_CLAIM, [code_chunk()])]
    assert CodeJudge(lambda: Broken())(items) is None
    assert CodeJudge(lambda: ScriptedChat("not json"))(items) is None

    def not_set_up() -> Any:
        raise ValueError("Set up the chat model in Settings first.")

    assert CodeJudge(not_set_up)(items) is None
    slow = Slow()
    try:
        assert CodeJudge(lambda: slow, timeout_s=0.05)(items) is None
    finally:
        slow.release.set()
    assert "longer than" in caplog.text
    verifier = Verifier(low, threshold=0.5, files=SNAPSHOT, code_judge=CodeJudge(lambda: Broken()))
    checked = verifier.check(items)
    assert ([c.passed for c in checked.checks], checked.methods) == ([False], [None])


def test_the_judge_rescues_a_sentence_in_the_answer_pipeline() -> None:
    chunk = code_chunk()
    found = Retrieval(chunks=[chunk], ranked=[chunk], source_ids=["s1"])
    chat = ScriptedChat(answer_json((TRUE_CLAIM, ["c1"])), verdicts((1, True, [("c1", 71, 73)])))
    outcome = answer(
        "How does requests build the Basic auth header?",
        retrieve=lambda _q: found,
        chat_adapter=lambda: chat,
        refuse_threshold=0.15,
        verifier=Verifier(low, threshold=0.5, files=SNAPSHOT, code_judge=CodeJudge(lambda: chat)),
    )
    assert isinstance(outcome, Answered) and outcome.badge == "verified"
    assert outcome.diagnostics.sentence_methods == ["code_judge"]
    assert outcome.diagnostics.sentence_scores == [0.05]  # the checker's score is kept


# --- parsing and anchors ---------------------------------------------------------------------


def test_parse_verdicts() -> None:
    parsed = parse_verdicts(
        '```json\n{"verdicts": [{"index": 1, "supported": true, "evidence": '
        '[{"chunk": "C1", "start_line": 3, "end_line": 4}]}, {"index": "2", "supported": true},'
        ' {"index": 3, "supported": true, "evidence": [{"chunk": "c1", "start_line": 0, '
        '"end_line": 2}]}, {"index": 1, "supported": false, "evidence": []}]}\n```'
    )
    assert parsed[1].supported and parsed[1].evidence is not None
    assert parsed[1].evidence[0].chunk == "c1"  # labels are normalised, first index wins
    assert 2 not in parsed  # unusable index
    assert parsed[3].evidence is None  # malformed evidence → fails in accept()
    assert not accept(Statement(TRUE_CLAIM, ("c1",)), parsed[3], {})[0]
    with pytest.raises(ValueError):
        parse_verdicts('{"something": []}')


def test_code_anchors() -> None:
    anchors = code_anchors(
        'The `HTTPAdapter` sets pool_maxsize to DEFAULT_POOLSIZE, which is 10, uses "Basic " and '
        "MD5 in auth.py; HTTPDigestAuth.build_digest_header is False by default, "
        "joined with colons."
    )
    assert anchors == [
        Anchor("code", "HTTPAdapter"),
        Anchor("string", "Basic"),
        Anchor("identifier", "pool_maxsize"),
        Anchor("identifier", "DEFAULT_POOLSIZE"),
        Anchor("identifier", "MD5"),
        Anchor("file", "auth.py"),
        Anchor("identifier", "HTTPDigestAuth.build_digest_header"),
        Anchor("number", "10"),
        Anchor("constant", "False"),
        Anchor("character", "colon"),
    ]
    # Plain prose has no anchors: capitalised words, acronyms, "e.g." and apostrophes are not code.
    assert code_anchors("Requests, e.g. the Session, doesn't retry HTTP or JSON.") == []
    assert code_anchors("It returns True if the host changes.") == []  # behaviour, not a value


def test_content_words_and_identifier_splitting() -> None:
    assert split_identifier("b64encode") == ["b64", "encode"]
    assert split_identifier("HTTPDigestAuth") == ["http", "digest", "auth"]
    assert split_identifier("_basic_auth_str") == ["basic", "auth", "str"]
    words = content_words(TRUE_CLAIM, code_anchors(TRUE_CLAIM))
    assert words == [
        "basic",
        "authentication",
        "joins",
        "username",
        "password",
        "base64",
        "encodes",
    ]


def test_a_sentence_without_anchors_needs_half_of_its_words_in_the_evidence() -> None:
    chunks, _ = prepare([(TRUE_CLAIM, [code_chunk()])])
    supported = parse_verdicts(verdicts((1, True, [("c1", 71, 73)])))[1]
    ok = Statement("It base64-encodes the username and password.", ("c1",))
    assert accept(ok, supported, chunks) == (True, "")
    vague = Statement("It protects the credentials against eavesdropping attacks.", ("c1",))
    passed, why = accept(vague, supported, chunks)
    assert not passed and "words" in why


def test_header_lines_are_not_numbered_and_missing_start_line_counts_from_one() -> None:
    chunk = RetrievedChunk("x", "s", "T", "File: a.py\nx = 1\ny = 2\n\n", {"file": "a.py"}, 5, 0.9)
    numbered = number_chunk("c1", chunk)
    assert (numbered.first, numbered.last, numbered.lines) == (1, 2, ("x = 1", "y = 2"))


DIGEST = """\
File: src/requests/auth.py · HTTPDigestAuth.build_digest_header (lines 168-171)
        if algorithm is None:
            _algorithm = "MD5"
        else:
            _algorithm = algorithm.upper()
"""


def test_names_may_come_from_the_chunk_header_and_symbol_but_values_may_not() -> None:
    symbol = "HTTPDigestAuth.build_digest_header"
    chunk = RetrievedChunk(
        "d",
        "s1",
        "requests",
        DIGEST,
        {"file": "src/requests/auth.py", "start_line": 168, "end_line": 171, "symbol": symbol},
        50,
        0.9,
    )
    chunks, _ = prepare([("x", [chunk])])
    supported = parse_verdicts(verdicts((1, True, [("c1", 168, 169)])))[1]
    md5 = Statement("Without an algorithm, HTTPDigestAuth uses MD5.", ("c1",))
    assert accept(md5, supported, chunks) == (True, "")
    sha = Statement("Without an algorithm, HTTPDigestAuth uses SHA-256.", ("c1",))
    assert not accept(sha, supported, chunks)[0]
    # The header's line range is not a value the evidence states.
    lines = Statement("HTTPDigestAuth checks 171 things.", ("c1",))
    assert not accept(lines, supported, chunks)[0]


STATUS = """\
File: src/requests/status_codes.py (lines 84-86)
    428: ("precondition_required", "precondition"),
    429: ("too_many_requests", "too_many"),
    431: ("header_fields_too_large", "fields_too_large"),
"""
STATUS_DOC = "File: src/requests/status_codes.py (lines 1-3)\nThe ``codes`` object maps names.\n"


def test_dotted_names_need_their_last_part_in_the_evidence_and_the_rest_in_cited_text() -> None:
    table = code_chunk("t", STATUS, 84, 86, "src/requests/status_codes.py")
    doc = code_chunk("d", STATUS_DOC, 1, 2, "src/requests/status_codes.py")
    chunks, _ = prepare([("x", [table, doc])])
    supported = parse_verdicts(verdicts((1, True, [("c1", 85, 85)])))[1]
    true = Statement("Use requests.codes.too_many_requests for HTTP 429.", ("c1", "c2"))
    assert accept(true, supported, chunks) == (True, "")
    false = Statement("Use requests.codes.rate_limited for HTTP 429.", ("c1", "c2"))
    assert not accept(false, supported, chunks)[0]
    uncited = Statement("Use requests.codes.too_many_requests for HTTP 429.", ("c1",))
    assert not accept(uncited, supported, chunks)[0]  # "codes" is only in the uncited chunk


# --- digit-bearing names (D-056) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("b64", "base64"),
        ("b64encode", "base64encode"),
        ("sha256", "SHA-256"),
        ("SHA256", "sha256"),
        ("utf8", "UTF-8"),
        ("UTF_8", "utf8"),
        ("md5", "MD5"),
        ("h2", "http2"),
    ],
)
def test_digit_terms_match_in_both_directions(a: str, b: str) -> None:
    assert terms_match(a, b) and terms_match(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("sha1", "sha256"),
        ("SHA-1", "SHA-256"),
        ("utf8", "utf16"),
        ("md4", "md5"),
        ("base32", "b64"),
        ("base64", "base"),  # no digits on one side
        ("e2", "ec2"),  # a one-letter abbreviation needs a word of four letters or more
        ("s3", "sha3"),
        ("x64", "b64"),
        ("ipv4", "ipv6"),
        ("b64encode", "base64decode"),
    ],
)
def test_digit_terms_do_not_match_other_terms(a: str, b: str) -> None:
    assert not terms_match(a, b) and not terms_match(b, a)


def test_prose_terms_are_anchors_matched_in_any_spelling() -> None:
    assert Anchor("term", "SHA-256") in code_anchors("Digest auth supports SHA-256 too.")
    assert Anchor("term", "UTF-8") in code_anchors("Text is decoded as UTF-8 by default.")
    evidence = "elif _algorithm == 'SHA-256':\n    hash_utf8 = hashlib.sha256"
    from app.grounding.code_judge import anchor_found

    assert anchor_found(Anchor("term", "SHA-256"), "x = hashlib.sha256(b)", [])
    assert anchor_found(Anchor("term", "UTF-8"), "s.encode('utf8')", [])
    assert anchor_found(Anchor("identifier", "SHA256"), evidence, [])
    assert anchor_found(Anchor("identifier", "MD5"), "_algorithm = 'md5'", [])
    assert not anchor_found(Anchor("term", "SHA-1"), "x = hashlib.sha256(b)", [])
    assert not anchor_found(Anchor("identifier", "SHA512"), evidence, [])
    assert not anchor_found(Anchor("term", "UTF-16"), "s.encode('utf8')", [])
    # A backticked span stays exact.
    assert not anchor_found(Anchor("code", "sha256"), "SHA-256", [])


def test_content_words_match_digit_abbreviations_both_ways() -> None:
    assert content_share(["base64"], "b64encode(b':'.join(x))") == 1.0
    assert content_share(["b64"], "The value is base64 encoded.") == 1.0
    assert content_share(["utf8"], "Bodies are decoded as UTF-8.") == 1.0
    assert content_share(["base32"], "b64encode(x)") == 0.0
    assert content_share(["sha1"], "hashlib.sha256(x)") == 0.0
