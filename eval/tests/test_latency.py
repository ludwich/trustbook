"""Latency and judge calls per answer (D-080) and parent context fields (D-077) in the runner:
the report table, sidecar version 4 and outline-only changes in ``--compare``."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import runner
from runner import Citation, Question, QuestionResult
from test_rework import good_api, run
from test_runner import cit, sse


def q(qid: str) -> Question:
    return Question(
        id=qid,
        question=f"{qid}?",
        type="answerable",
        category="code",
        expected_sources=("requests",),
    )


def res(
    qid: str,
    latency: float,
    *,
    judge: int | None = 1,
    answer_calls: int | None = 1,
    outcome: str = "answered",
    **kw: Any,
) -> QuestionResult:
    return QuestionResult(
        question=q(qid),
        outcome=outcome,
        latency_s=latency,
        judge_calls=judge,
        judge_seconds=None if not judge else 2.0 * judge,
        answer_calls=answer_calls,
        **kw,
    )


def info() -> runner.RunInfo:
    return runner.RunInfo(
        "http://tb", "S", "sp1", ("q.yaml",), None, runner.datetime.now(runner.UTC)
    )


def test_allowed_over_follows_the_p95_interpolation() -> None:
    assert runner.allowed_over(45) == 2
    assert runner.allowed_over(20) == 0
    assert runner.allowed_over(0) == 0
    # With 45 answers, 2 over 60 s keep p95 at or under 60 s; 3 do not.
    ok = [10.0] * 43 + [100.0, 100.0]
    bad = [10.0] * 42 + [100.0] * 3
    assert runner.percentile(ok, 95) == pytest.approx(10.0)
    assert (runner.percentile(bad, 95) or 0) > 60


def test_the_report_shows_judge_calls_per_answer_and_answers_over_60_s() -> None:
    results = [
        res("a", 10.0, judge=0),
        res("b", 20.0, judge=1),
        res("c", 30.0, judge=2, regenerated=True, answer_calls=2),
        res(
            "d",
            70.0,
            judge=5,
            regenerated=True,
            answer_calls=3,
            timings={"generation_s": [30.0, 25.0, 5.0], "verification_s": [4.0, 4.0]},
            prompt_tokens=[3000, 3100, 3200],
            promoted_tokens=60,
            outline_tokens=80,
            outline_only=[1, 0],
        ),
        res("e", 5.0, judge=None, answer_calls=0, outcome="refused", refusal_reason="x"),
    ]
    text = runner.render_report(info(), results, {})
    assert "#### Latency and judge calls" in text
    assert "| Answers that reached generation (N) | 4 (1 refused before generation: `e`) |" in text
    # judge calls 0, 1, 2, 5: mean 2, p50 1.5, p95 4.55, max 5
    assert "| Judge calls per answer: mean / p50 / p95 / max | 2 / 1.5 / 4.6 / 5 |" in text
    assert "| Answers with 0 / 1 / 2 / ≥3 judge calls | 1 / 1 / 1 / 1 |" in text
    assert "| Judge seconds per answer: p50 / p95 | 3.0 s / 9.1 s |" in text
    assert "| Judge share of answer time | 12.3% |" in text  # 16 s of 130 s
    assert "| Answer calls per answer: mean / max | 1.75 / 3 |" in text
    assert "| Regenerations | 2/4 |" in text
    assert "| Answers over 60 s | 1/5 (at most 0 guarantee p95 <= 60 s) **p95 over 60 s**" in text
    assert "| Prompt tokens of the first answer call: p50 / p95 | 3000 / 3000 |" in text
    assert "| Outline-only failures | 1 sentence(s) in 1 answer(s), 1 of them regenerated |" in text
    assert (
        "- `d`: 70.0 s; generation 60.0 s in 3 call(s), verification 8.0 s, judge 10.0 s in 5 "
        "call(s), regenerated" in text
    )


@pytest.mark.parametrize(
    ("tail", "flagged"),
    [
        # Review finding (slice B): 3 of 45 over 60 s, yet p95 = 50 + 0.8 * (62 - 50) = 59.6 s.
        ([50.0, 62.0, 70.0, 80.0], False),
        ([61.0, 62.0, 70.0, 80.0], True),  # p95 = 61.8 s
    ],
)
def test_answers_over_60_s_are_flagged_only_when_p95_is_over(
    tail: list[float], flagged: bool
) -> None:
    latencies = [30.0] * 41 + tail
    results = [res(f"q{i:02d}", v) for i, v in enumerate(latencies)]
    text = runner.render_report(info(), results, {})
    over = sum(1 for v in latencies if v > 60)
    row = next(line for line in text.splitlines() if line.startswith("| Answers over 60 s |"))
    assert row.startswith(f"| Answers over 60 s | {over}/45 (at most 2 guarantee p95 <= 60 s)")
    assert ("**p95 over 60 s**" in row) is flagged
    p95 = runner.percentile(latencies, 95)
    assert p95 is not None and (p95 > 60) is flagged


def test_promoted_citations_count_correct_ones() -> None:
    r = res("a", 1.0, expansions=[{"chunk_id": "p1", "kind": "class_head", "referrer": "c"}])
    r.citations = [Citation(1, "p1", "s", "t", {}), Citation(2, "c", "s", "t", {})]
    r.correct = [True, False]
    assert runner.promoted_citations(r) == {"n": 1, "correct": 1}
    assert runner.promoted_citations(res("b", 1.0)) is None


def diag(**extra: Any) -> dict[str, Any]:
    return {
        "top_rerank": 0.8,
        "sentence_scores": [0.9],
        "regenerated": False,
        "retrieval_fingerprint": "fp-abc",
        "generation": {"temperature": 0.0, "seed": 42},
        "judge_calls": 2,
        "judge_calls_per_pass": [2],
        "judge_seconds": 4.5,
        "answer_calls": 1,
        "timings": {
            "retrieval_s": 0.2,
            "generation_s": [12.5],
            "verification_s": [5.0],
            "citation_s": 0.1,
            "gate_s": 0.8,
            "total_s": 18.0,
        },
        "prompt_tokens": [3300],
        "completion_tokens": [900],
        "expansions": [{"chunk_id": "src1-c1", "kind": "class_head", "referrer": "x"}],
        "promoted_tokens": 60,
        "outline_tokens": 85,
        "outline_fingerprint": "of-1",
        "outline_only": [0],
        "promoted_cited": [1, 0],
        "windows": [{"source_id": "src2", "numbered": ["t3"], "chunk_ids": ["t2"], "tokens": 40}],
        "window_tokens": 40,
        "window_fingerprint": "wf-1",
        "window_only": [0],
    } | extra


def test_the_sidecar_v5_keeps_numbers_and_ids(tmp_path: Path) -> None:
    api = good_api()
    message = {
        "id": "m",
        "content": {
            "sentences": [
                {
                    "text": "30 [1].",
                    "citations": [
                        cit(
                            1,
                            "src1",
                            "requests",
                            file="src/requests/models.py",
                            start_line=100,
                            end_line=106,
                        )
                    ],
                }
            ]
        },
        "badge": "verified",
        "removed_sentences": [],
        "diagnostics": diag(),
    }
    api.answers["Max redirects?"] = sse(("done", {"message": message}))
    code, text, _, sidecar = run(api, tmp_path)
    assert code == 0, text
    data = json.loads(sidecar.read_text())
    assert data["version"] == 5
    assert data["retrieval_settings"] is None  # the fake API does not report them
    row = {r["id"]: r for r in data["questions"]}["code-1"]
    assert row["judge_calls"] == 2 and row["judge_calls_per_pass"] == [2]
    assert row["answer_calls"] == 1 and row["timings"]["generation_s"] == [12.5]
    assert row["prompt_tokens"] == [3300] and row["completion_tokens"] == [900]
    assert row["expansions"] == [{"chunk_id": "src1-c1", "kind": "class_head", "referrer": "x"}]
    assert row["outline_fingerprint"] == "of-1" and row["outline_tokens"] == 85
    assert row["promoted_citations"] == {"n": 1, "correct": 1}
    # Transcript windows (D-091): ids and numbers only.
    assert row["windows"] == [
        {"source_id": "src2", "numbered": ["t3"], "chunk_ids": ["t2"], "tokens": 40}
    ]
    assert row["window_tokens"] == 40 and row["window_fingerprint"] == "wf-1"
    assert row["window_only"] == [0]
    latency = data["latency"]
    assert latency["generated"] == 1 and latency["judge_calls"]["max"] == 2
    assert latency["judge_calls"]["distribution"] == {"0": 0, "1": 0, "2": 1, "3+": 0}
    assert latency["allowed_over_60_s"] == runner.allowed_over(latency["timed"])
    assert latency["window_tokens"]["p50"] == 40
    assert latency["window_only"] == {"answers": [], "sentences": 0, "regenerated": 0}
    assert "| Transcript window tokens: p50 / p95 | 40 / 40 |" in text
    assert "| Citations to promoted chunks | 1 (1 inside an expected locator) |" in text
    # No question text and no answer text besides failed sentences.
    assert "Max redirects?" not in sidecar.read_text()


def test_the_run_records_the_retrieval_switches(tmp_path: Path) -> None:
    api = good_api()
    original = api.handler

    def with_settings(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/settings":
            body = {"thresholds": {"refuse": 0.15, "verify": 0.5}}
            return httpx.Response(
                200, json=body | {"retrieval": {"parent_context": True, "outline": False}}
            )
        return original(request)

    api.handler = with_settings  # type: ignore[method-assign]
    code, text, _, sidecar = run(api, tmp_path)
    assert code == 0, text
    assert json.loads(sidecar.read_text())["retrieval_settings"] == {
        "parent_context": True,
        "outline": False,
    }


def test_compare_reports_outline_only_changes_separately(tmp_path: Path) -> None:
    def row(qid: str, outcome: str, fp: str, ofp: str | None, **kw: Any) -> dict[str, Any]:
        return {
            "id": qid,
            "type": "answerable",
            "outcome": outcome,
            "retrieval_fingerprint": fp,
            "outline_fingerprint": ofp,
        } | kw

    a = {
        "version": 4,
        "questions": [
            row("outline", "answered", "f1", "o1", citations=1, citations_correct=1),
            row("model", "answered", "f2", None, citations=1, citations_correct=1),
            row("pipe", "answered", "f3", None, citations=1, citations_correct=1),
        ],
    }
    b = {
        "version": 4,
        "questions": [
            row("outline", "refused", "f1", "o2", refusal_reason="unsupported"),
            row("model", "refused", "f2", None, refusal_reason="unsupported"),
            row("pipe", "refused", "f9", None, refusal_reason="unsupported"),
        ],
    }
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    out = io.StringIO()
    assert runner.main(["--compare", str(pa), str(pb)], out=out, log=io.StringIO()) == 0
    text = out.getvalue()
    assert (
        "| outline | answered 1/1 | refused (unsupported) | same chunks, different outline "
        "| outline only |" in text
    )
    assert "| model | answered 1/1 | refused (unsupported) | same | model / sampling |" in text
    assert (
        "3 of 3 questions differ: 1 with the same retrieval (model / sampling), 1 with the same "
        "chunks and a different outline (outline only), 1 with different retrieval" in text
    )
    assert "Only the outline differs on 1 question(s): `outline`." in text
    # Version 3 sidecars have no outline fingerprint: the old classification holds.
    for data in (a, b):
        for r in data["questions"]:
            r.pop("outline_fingerprint")
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    out = io.StringIO()
    runner.main(["--compare", str(pa), str(pb)], out=out, log=io.StringIO())
    assert (
        "| outline | answered 1/1 | refused (unsupported) | same | model / sampling |"
        in out.getvalue()
    )


def test_the_report_shows_window_tokens_and_window_only_failures() -> None:
    results = [
        res("a", 10.0, window_tokens=0, window_only=[0]),
        res("b", 20.0, window_tokens=900, window_only=[1, 2], regenerated=True, answer_calls=2),
        res("c", 30.0, window_tokens=1500, window_only=[0]),
    ]
    text = runner.render_report(info(), results, {})
    assert "| Transcript window tokens: p50 / p95 | 900 / 1440 |" in text
    assert "| Window-only failures | 3 sentence(s) in 1 answer(s), 1 of them regenerated |" in text
    assert text.index("| Outline tokens") < text.index("| Transcript window tokens")
    assert text.index("| Outline-only failures") < text.index("| Window-only failures")
    data = runner.latency_json(results)
    assert data["window_only"] == {"answers": ["b"], "sentences": 3, "regenerated": 1}
    assert data["window_tokens"]["max"] == 1500


def test_compare_reports_window_only_changes_separately(tmp_path: Path) -> None:
    def row(qid: str, outcome: str, ofp: str | None, wfp: str | None) -> dict[str, Any]:
        extra: dict[str, Any] = {"citations": 1, "citations_correct": 1}
        if outcome == "refused":
            extra = {"refusal_reason": "unsupported"}
        return {
            "id": qid,
            "type": "answerable",
            "outcome": outcome,
            "retrieval_fingerprint": f"f-{qid}",
            "outline_fingerprint": ofp,
            "window_fingerprint": wfp,
        } | extra

    a = {
        "version": 5,
        "questions": [row("talk", "answered", None, "w1"), row("code", "answered", "o1", "w1")],
    }
    b = {
        "version": 5,
        "questions": [row("talk", "refused", None, "w2"), row("code", "refused", "o2", "w2")],
    }
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    out = io.StringIO()
    assert runner.main(["--compare", str(pa), str(pb)], out=out, log=io.StringIO()) == 0
    text = out.getvalue()
    assert (
        "| talk | answered 1/1 | refused (unsupported) | same chunks, different transcript window "
        "| transcript window only |" in text
    )
    # The outline is checked first: a changed outline is reported as the outline.
    assert (
        "| code | answered 1/1 | refused (unsupported) | same chunks, different outline "
        "| outline only |" in text
    )
    assert (
        "1 with the same chunks and a different outline (outline only), 1 with the same chunks "
        "and a different transcript window (transcript window only), 0 with different retrieval"
        in text
    )
    assert "Only the transcript windows differ on 1 question(s): `talk`." in text
    # Version 4 sidecars have no window fingerprint: the old classification holds.
    for data in (a, b):
        for r in data["questions"]:
            r.pop("window_fingerprint")
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    out = io.StringIO()
    runner.main(["--compare", str(pa), str(pb)], out=out, log=io.StringIO())
    assert (
        "| talk | answered 1/1 | refused (unsupported) | same | model / sampling |"
        in out.getvalue()
    )
    assert "transcript window" not in out.getvalue()
