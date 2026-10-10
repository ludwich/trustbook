"""Gate 3 additions to the runner: false refusals per category and on code questions, p95
latency target and flag, cited ranges valid against a checkout, code-judge sentences in the
sweep."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import runner
from runner import Citation, Locator, Question, QuestionResult, compute_metrics
from test_runner import QS_URL, Clock, FakeApi, answer, cit, write_questions


def q(qid: str, category: str | None = None, *locs: Locator, qtype: str = "answerable") -> Question:
    return Question(
        id=qid,
        question=f"{qid}?",
        type=qtype,
        category=category,
        expected_sources=("requests",) if qtype == "answerable" else (),
        expected_locators=locs,
    )


def res(question: Question, outcome: str, latency: float = 1.0) -> QuestionResult:
    return QuestionResult(question=question, outcome=outcome, latency_s=latency)


CODE_LOC = Locator(file="src/requests/auth.py", lines=(34, 75))
DOC_LOC = Locator(file="docs/user/quickstart.rst")


def test_code_questions_are_category_code_or_expect_a_code_file() -> None:
    assert runner.expects_code(q("a", "code"))
    assert runner.expects_code(q("b", "multi", CODE_LOC))
    assert runner.expects_code(q("c", None, Locator(file="Makefile")))
    assert not runner.expects_code(q("d", "multi", DOC_LOC, Locator(heading="Install")))
    assert not runner.expects_code(q("e", "docs"))
    assert runner.is_code_file("src\\requests\\auth.py")
    assert not runner.is_code_file("HISTORY.md") and not runner.is_code_file("README.rst")


def test_false_refusals_per_category_and_on_code_questions() -> None:
    results = [
        res(q("docs-1", "docs"), "refused"),
        res(q("docs-2", "docs"), "answered"),
        res(q("code-1", "code"), "refused"),
        res(q("code-2", "code"), "answered"),
        res(q("multi-1", "multi", CODE_LOC), "refused"),  # counts as a code question
        res(q("multi-2", "multi", DOC_LOC), "refused"),  # docs only: not a code question
        res(q("near-1", None), "answered"),
        res(q("un-1", "unanswerable", qtype="unanswerable"), "refused"),  # not answerable
    ]
    m = compute_metrics(results)
    by = {k: (v.num, v.den) for k, v in m.false_refusal_by_group.items()}
    assert by == {"docs": (1, 2), "code": (1, 2), "multi": (2, 2), "other": (0, 1)}
    assert (m.false_refusal_code.num, m.false_refusal_code.den) == (2, 3)
    assert runner.thresholds_met(m, None, None, max_false_refusals_code=2)
    assert not runner.thresholds_met(m, None, None, max_false_refusals_code=1)
    # Nothing to measure fails the target.
    assert not runner.thresholds_met(compute_metrics([]), None, None, max_false_refusals_code=1)


def test_p95_target_and_flag_above_60_s() -> None:
    results = [res(q(f"a{i}", "docs"), "answered", latency=10.0 * i) for i in range(1, 9)]
    m = compute_metrics(results)
    assert m.latency_p95 == pytest.approx(76.5)
    assert runner.thresholds_met(m, None, None, max_p95=80)
    assert not runner.thresholds_met(m, None, None, max_p95=60)
    info = runner.RunInfo(
        "http://tb", "S", "sp1", ("q.yaml",), None, runner.datetime.now(runner.UTC)
    )
    text = runner.render_report(info, results, {})
    assert "| 45.0 s / 76.5 s **above 60 s** | - | - |" in text  # flagged without the flag
    strict = runner.RunInfo(
        "http://tb", "S", "sp1", ("q.yaml",), None, runner.datetime.now(runner.UTC), max_p95=60
    )
    assert "76.5 s **above 60 s** | <= 60 s | **FAIL** |" in runner.render_report(
        strict, results, {}
    )
    fast = [res(q("f", "docs"), "answered", latency=5.0)]
    assert "above" not in runner.render_report(info, fast, {})


def cited(question: Question, *locators: dict[str, Any]) -> QuestionResult:
    r = res(question, "answered")
    r.citations = [
        Citation(i + 1, f"k{i}", "src1", "requests", loc) for i, loc in enumerate(locators)
    ]
    r.correct = [True] * len(r.citations)
    return r


def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "requests"
    (root / "src" / "requests").mkdir(parents=True)
    (root / "src" / "requests" / "auth.py").write_text(
        "".join(f"line {i}\n" for i in range(1, 101))
    )
    (tmp_path / "secret.py").write_text("x = 1\n")
    return root


def test_cited_ranges_are_checked_against_the_checkout(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    results = [
        cited(
            q("code-1", "code"),
            {"file": "src/requests/auth.py", "start_line": 34, "end_line": 75},  # valid
            {"file": "src/requests/auth.py", "start_line": 90, "end_line": 101},  # past the end
            {"heading": "Quickstart"},  # not a file citation: not counted
        ),
        cited(
            q("code-2", "code"),
            {"file": "src/requests/missing.py", "start_line": 1, "end_line": 2},
            {"file": "../secret.py", "start_line": 1, "end_line": 1},  # outside the root
            {"file": "src/requests/auth.py"},  # file only: exists
            {"file": "src/requests/auth.py", "start_line": 5, "end_line": 4},
        ),
    ]
    report = runner.check_ranges(results, str(root))
    assert (report.valid.num, report.valid.den) == (2, 6)
    reasons = [(b.question_id, b.file, b.reason) for b in report.bad]
    assert reasons == [
        ("code-1", "src/requests/auth.py", "file has 100 lines"),
        ("code-2", "src/requests/missing.py", "file not found"),
        ("code-2", "../secret.py", "file not found"),
        ("code-2", "src/requests/auth.py", "invalid line range"),
    ]
    m = compute_metrics(results)
    assert not runner.thresholds_met(m, None, None, min_ranges_valid=1.0, ranges=report)
    assert runner.thresholds_met(m, None, None, min_ranges_valid=0.3, ranges=report)
    assert not runner.thresholds_met(m, None, None, min_ranges_valid=0.3)  # not measured
    info = runner.RunInfo(
        "http://tb",
        "S",
        "sp1",
        ("q.yaml",),
        None,
        runner.datetime.now(runner.UTC),
        min_ranges_valid=1.0,
    )
    text = runner.render_report(info, results, {}, ranges=report)
    assert (
        "| Cited ranges valid (file and lines exist in the checkout) | 33.3% (2/6) "
        "| >= 100% | **FAIL** |" in text
    )
    assert "- `code-1` `src/requests/auth.py` lines 90-101: file has 100 lines" in text


def test_code_judge_sentences_pass_at_any_verify_threshold_in_the_sweep() -> None:
    r = res(q("code-1", "code"), "answered")
    r.top_rerank = 0.8
    r.sentence_scores = [0.05, 0.9]
    r.sentence_methods = ["code_judge", "checker"]
    r.sentence_citations = [[Citation(1, "k1", "src1", "requests", {})]] * 2
    assert runner.outcome_at(r, 0.15, 0.7) == "answered"
    assert runner.kept_scores(r, 0.5) == [None, 0.9]
    r.sentence_methods = None  # an older API: the low score decides
    assert runner.outcome_at(r, 0.15, 0.5) == "refused"


def test_main_with_gate_3_targets(tmp_path: Path) -> None:
    api = FakeApi()
    root = checkout(tmp_path)
    (root / "src" / "requests" / "models.py").write_text("x\n" * 200)
    loc = {"file": "src/requests/models.py", "start_line": 100, "end_line": 106}
    api.answers["Max redirects?"] = answer(
        ("The default limit is 30 redirects [1].", [cit(1, "src1", "requests", **loc)])
    )
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(f"sources:\n  - folder: trustbook-eval/requests\n  - url: {QS_URL}\n")
    argv = ["--base-url", "http://tb", "--space", "Smoke", "--setup", str(corpus)]
    argv += ["--questions", str(write_questions(tmp_path)), "--report", str(tmp_path / "r.md")]
    gate3 = ["--max-false-refusals-code", "1", "--max-p95", "60", "--code-root", str(root)]
    gate3 += ["--min-ranges-valid", "1.0"]
    out = io.StringIO()

    def run(extra: list[str]) -> int:
        return runner.main(
            [*argv, *extra],
            transport=httpx.MockTransport(api.handler),
            out=out,
            log=io.StringIO(),
            sleep=lambda _: None,
            clock=Clock(),
        )

    assert run(gate3) == 0, out.getvalue()
    text = out.getvalue()
    assert "| False refusals: docs | 1/1 | - | - |" in text  # the fake refuses unknown questions
    assert "| False refusals: code | 0/1 | - | - |" in text
    assert (
        "| False refusals on code questions (category code, or expecting a code file) | 0/1 "
        "| <= 1 | pass |" in text
    )
    assert "| <= 60 s | pass |" in text
    assert "| 100.0% (1/1) | >= 100% | pass |" in text
    data = json.loads((tmp_path / "r.json").read_text())
    assert data["cited_ranges"] == {"num": 1, "den": 1, "value": 1.0}
    assert data["false_refusals_code"] == {"num": 0, "den": 1, "value": 0.0}
    assert data["false_refusals"]["docs"]["num"] == 1

    (root / "src" / "requests" / "models.py").write_text("x\n" * 50)  # lines 100-106 are gone
    out = io.StringIO()
    assert run(gate3) == 1
    assert "lines 100-106: file has 50 lines" in out.getvalue()
    assert run(["--max-p95", "0.5"]) == 1  # the fake clock makes every answer take 1 s
    with pytest.raises(SystemExit) as exc:
        run(["--min-ranges-valid", "1"])
    assert exc.value.code == 2
    assert run(["--code-root", str(tmp_path / "nope")]) == 2
