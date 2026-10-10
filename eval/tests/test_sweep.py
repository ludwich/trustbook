"""Diagnostics, the JSON sidecar, the threshold sweep and private question sets (no network)."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import runner
from runner import Citation, Question, QuestionResult, SourceInfo, Thresholds
from test_runner import Clock, FakeApi, cit, sse

REPO = Path(__file__).resolve().parents[2]


def message(
    sentences: list[tuple[str, list[dict[str, Any]]]],
    *,
    badge: str | None = "verified",
    removed: list[str] | None = None,
    diagnostics: dict[str, Any] | None = None,
    content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "m",
        "role": "assistant",
        "content": content
        if content is not None
        else {"sentences": [{"text": t, "citations": c} for t, c in sentences]},
        "badge": badge,
        "removed_sentences": removed or [],
        "sources_used": [],
        "diagnostics": diagnostics,
    }


def answered(sentences: list[tuple[str, list[dict[str, Any]]]], **kwargs: Any) -> str:
    events: list[tuple[str, Any]] = [("status", {"stage": "checking"})]
    for i, (text, cits) in enumerate(sentences):
        events.append(("sentence", {"index": i, "text": text, "citations": cits}))
    events.append(("done", {"message": message(sentences, **kwargs)}))
    return sse(*events)


def refused(reason: str, diagnostics: dict[str, Any]) -> str:
    data = {"reason": reason, "message": "Not in your sources", "suggestion": None}
    msg = message([], badge="refused", diagnostics=diagnostics)
    msg["content"] = {"text": "Not in your sources", "sentences": []}
    return sse(("refused", data), ("done", {"message": msg}))


class SettingsApi(FakeApi):
    """The fake API plus ``GET /api/settings`` with grounding thresholds."""

    def __init__(self, thresholds: dict[str, float] | None) -> None:
        super().__init__()
        self.thresholds = thresholds

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/settings" and self.thresholds is not None:
            return httpx.Response(200, json={"thresholds": self.thresholds, "power": "normal"})
        return super().handler(request)


def ask(api: FakeApi, question: str, sid: str = "sp1") -> QuestionResult:
    with api.client() as client:
        return runner.ask(
            client,
            sid,
            Question(id="q", question=question, type="answerable", expected_sources=("requests",)),
            clock=Clock(),
        )


# --- reading diagnostics ---------------------------------------------------------------------


def test_ask_reads_badge_removed_and_diagnostics() -> None:
    api = FakeApi()
    sid = api.add_space("S")
    c1 = cit(1, "src1", "requests", file="a.py", start_line=1, end_line=2)
    api.answers["Q?"] = answered(
        [("Kept.", [c1])],
        badge="partially_verified",
        removed=["Gone."],
        diagnostics={"top_rerank": 0.8, "sentence_scores": [0.9, 0.1], "regenerated": False},
    )
    r = ask(api, "Q?", sid)
    assert (r.outcome, r.badge, r.removed_sentences) == ("answered", "partially_verified", 1)
    assert (r.top_rerank, r.sentence_scores, r.regenerated) == (0.8, [0.9, 0.1], False)
    assert [[c.n for c in cits] for cits in r.sentence_citations] == [[1]]


def test_ask_without_diagnostics_keeps_them_unknown() -> None:
    api = FakeApi()
    sid = api.add_space("S")
    api.answers["Q?"] = answered([("A.", [])], badge=None)
    r = ask(api, "Q?", sid)
    assert r.outcome == "answered"
    assert (r.top_rerank, r.sentence_scores, r.regenerated) == (None, None, None)


def test_gate_chitchat_reply_counts_as_not_answered() -> None:
    api = FakeApi()
    sid = api.add_space("S")
    reply = message([], badge=None, content={"text": "I answer questions from the sources…"})
    reply["diagnostics"] = {"top_rerank": None, "sentence_scores": [], "regenerated": False}
    api.answers["hello"] = sse(("status", {"stage": "retrieving"}), ("done", {"message": reply}))
    r = ask(api, "hello", sid)
    assert (r.outcome, r.refusal_reason) == ("refused", "chitchat")


def test_unsupported_refusal_keeps_scores() -> None:
    api = FakeApi()
    sid = api.add_space("S")
    diag = {"top_rerank": 0.6, "sentence_scores": [0.2, 0.7], "regenerated": True}
    api.answers["Q?"] = refused("unsupported", diag)
    r = ask(api, "Q?", sid)
    assert (r.outcome, r.refusal_reason, r.sentence_scores, r.regenerated) == (
        "refused",
        "unsupported",
        [0.2, 0.7],
        True,
    )


# --- estimation rules ------------------------------------------------------------------------


def result(
    qtype: str = "answerable",
    outcome: str = "answered",
    *,
    top: float | None = 0.5,
    scores: list[float | None] | None = None,
    reason: str | None = None,
    citations: list[list[Citation]] | None = None,
) -> QuestionResult:
    q = Question(
        id="x",
        question="x?",
        type=qtype,
        expected_sources=("good",) if qtype == "answerable" else (),
    )
    r = QuestionResult(question=q, outcome=outcome, refusal_reason=reason)
    r.top_rerank, r.sentence_scores = top, scores
    r.sentence_citations = citations or []
    r.citations = list({c.key: c for cits in r.sentence_citations for c in cits}.values())
    return r


@pytest.mark.parametrize(
    ("kwargs", "refuse", "verify", "expected"),
    [
        ({"top": 0.12}, 0.15, 0.5, "refused"),
        ({"top": 0.12}, 0.10, 0.5, "answered"),
        ({"top": None}, 0.40, 0.5, "answered"),  # no diagnostics: the real outcome
        ({"scores": [0.9, 0.4]}, 0.15, 0.5, "answered"),  # exactly half fail
        ({"scores": [0.9, 0.4]}, 0.15, 0.95, "refused"),  # first fails
        ({"scores": [0.4, 0.9]}, 0.15, 0.5, "refused"),  # first fails
        ({"scores": [0.9, 0.4, 0.4]}, 0.15, 0.5, "refused"),  # more than half fail
        ({"scores": [None, 0.0]}, 0.15, 0.5, "answered"),  # None = not checked = passes
        ({"outcome": "refused", "reason": "unsupported", "scores": [0.3]}, 0.15, 0.3, "answered"),
        ({"outcome": "refused", "reason": "unsupported", "scores": [0.3]}, 0.15, 0.5, "refused"),
        ({"outcome": "refused", "reason": "invalid_output", "scores": [0.9]}, 0.15, 0.5, "refused"),
        ({"outcome": "refused", "reason": "not_in_sources", "top": 0.1}, 0.05, 0.5, "refused"),
        ({"outcome": "refused", "reason": "chitchat", "top": None}, 0.05, 0.5, "refused"),
        ({"outcome": "error"}, 0.05, 0.5, "error"),
    ],
)
def test_outcome_at(kwargs: dict[str, Any], refuse: float, verify: float, expected: str) -> None:
    assert runner.outcome_at(result(**kwargs), refuse, verify) == expected


def test_kept_scores_aligns_kept_sentences() -> None:
    c = [Citation(1, "k1", "s", "t", {})]
    both = result(scores=[0.9, 0.2, 0.7], citations=[c, c])
    assert runner.kept_scores(both, 0.5) == [0.9, 0.7]
    same = result(scores=[0.9], citations=[c])
    assert runner.kept_scores(same, 0.5) == [0.9]
    mismatch = result(scores=[0.9, 0.8, 0.7], citations=[c, c])
    assert runner.kept_scores(mismatch, 0.5) == [None, None]
    unchecked = result(scores=[None, 0.0], citations=[c])
    assert runner.kept_scores(unchecked, 0.5) == [None]


def test_sweep_grid_and_values() -> None:
    sources = {
        "good": SourceInfo("good", "good", None, "paste", "ready"),
        "bad": SourceInfo("bad", "bad", None, "paste", "ready"),
    }
    right = Citation(1, "k1", "good", "good", {})
    wrong = Citation(2, "k2", "bad", "bad", {})
    results = [
        # answered: a strong sentence citing the right source, a weak one citing the wrong one
        result(top=0.6, scores=[0.9, 0.55], citations=[[right], [wrong]]),
        # answerable but weak retrieval
        result(top=0.12, scores=[0.8], citations=[[right]]),
        # unanswerable: refused before generating, and one answered with weak support
        result("unanswerable", "refused", top=0.08, reason="not_in_sources"),
        result("unanswerable", "answered", top=0.3, scores=[0.45], citations=[[wrong]]),
    ]
    rows = runner.sweep(results, sources, Thresholds(refuse=0.15, verify=0.5))
    assert [(r.refuse, r.verify) for r in rows][:6] == [
        (0.05, 0.3),
        (0.05, 0.4),
        (0.05, 0.5),
        (0.05, 0.6),
        (0.05, 0.7),
        (0.1, 0.3),
    ]
    assert len(rows) == 8 * 5 and rows[-1].refuse == 0.4 and rows[-1].verify == 0.7
    at = {(r.refuse, r.verify): r for r in rows}

    low = at[(0.05, 0.3)]
    assert (low.correct_refusal.num, low.correct_refusal.den) == (1, 2)
    assert (low.answered.num, low.answered.den) == (2, 2)
    assert (low.precision.num, low.precision.den) == (
        2,
        3,
    )  # right twice (dedup per answer), wrong once

    run = at[(0.15, 0.5)]
    assert (run.correct_refusal.num, run.answered.num) == (2, 1)
    assert (run.precision.num, run.precision.den) == (1, 2)

    strict = at[(0.15, 0.6)]  # the 0.55 sentence drops out of the first answer: half fail
    assert (strict.answered.num, strict.precision.num, strict.precision.den) == (1, 1, 1)


# --- report, sidecar and private sets ----------------------------------------------------------


def private_set(folder: Path, marker: str) -> None:
    folder.mkdir()
    (folder / "a.yaml").write_text(
        "questions:\n"
        f"  - id: p-1\n    question: {marker} redirects?\n    type: answerable\n"
        "    expected_sources: [requests]\n"
    )
    (folder / "b.yml").write_text(
        f"- id: p-2\n  question: {marker} websockets?\n  type: unanswerable\n"
    )
    (folder / "notes.txt").write_text("not a question file")


def test_private_question_directory_report_and_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = f"PRIVATE-{time.time_ns()}"
    started = time.time()
    qdir = tmp_path / "golden"
    private_set(qdir, marker)
    out_dir = tmp_path / "reports"
    out_dir.mkdir()
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    api = SettingsApi({"refuse": 0.15, "verify": 0.5})
    sid = api.add_space("Private")
    src = api.add_source(sid, "folder", "/host/requests", "requests")
    c1 = cit(1, src, "requests", file="a.py", start_line=1, end_line=3)
    api.answers[f"{marker} redirects?"] = answered(
        [("Thirty.", [c1])],
        diagnostics={"top_rerank": 0.7, "sentence_scores": [0.92, 0.3], "regenerated": False},
        badge="partially_verified",
        removed=["Something else."],
    )
    api.answers[f"{marker} websockets?"] = refused(
        "not_in_sources", {"top_rerank": 0.04, "sentence_scores": [], "regenerated": False}
    )
    report = out_dir / "private.md"
    out = io.StringIO()
    code = runner.main(
        ["--space", "Private", "--questions", str(qdir), "--report", str(report)],
        transport=httpx.MockTransport(api.handler),
        out=out,
        log=io.StringIO(),
        clock=Clock(),
    )
    assert code == 0, out.getvalue()
    text = report.read_text()
    assert "#### Threshold sweep" in text
    assert "this run used refuse 0.15, verify 0.5." in text
    assert "| 0.15 **(run)** | 0.5 | 100% (1/1) | 100% (1/1) | 100% (1/1) |" in text
    assert text.count("\n| 0.") == 40

    data = json.loads((out_dir / "private.json").read_text())
    assert data["thresholds"] == {"refuse": 0.15, "verify": 0.5}
    assert [
        (q["id"], q["outcome"], q["top_rerank"], q["sentence_scores"]) for q in data["questions"]
    ] == [
        ("p-1", "answered", 0.7, [0.92, 0.3]),
        ("p-2", "refused", 0.04, []),
    ]
    assert data["questions"][0]["badge"] == "partially_verified"
    assert data["questions"][0]["removed_sentences"] == 1
    assert len(data["sweep"]) == 40
    assert data["sweep"][0]["precision_proxy"] == {"num": 1, "den": 1, "value": 1.0}
    # The sidecar holds ids and numbers only, never the question text.
    assert marker not in (out_dir / "private.json").read_text()

    # Only the chosen report folder was written to: not the cwd, the question folder or the repo.
    assert sorted(p.name for p in out_dir.iterdir()) == ["private.json", "private.md"]
    assert list(workdir.iterdir()) == []
    assert sorted(p.name for p in qdir.iterdir()) == ["a.yaml", "b.yml", "notes.txt"]
    skip = {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "test-results",
        "playwright-report",
    }
    leaked = []
    for path in REPO.rglob("*"):
        if any(part in skip for part in path.relative_to(REPO).parts) or not path.is_file():
            continue
        if path.stat().st_mtime < started or path.stat().st_size > 2_000_000:
            continue
        if marker in path.read_text("utf-8", errors="ignore"):
            leaked.append(path)
    assert leaked == []


def test_sidecar_path() -> None:
    assert runner.sidecar_path("out.md") == Path("out.json")
    assert runner.sidecar_path("dir/Report.MD") == Path("dir/Report.json")
    assert runner.sidecar_path("out.txt") == Path("out.txt.json")


def test_report_without_diagnostics_says_so(tmp_path: Path) -> None:
    info = runner.RunInfo(
        base_url="http://tb",
        space_name="S",
        space_id="sp1",
        question_files=("q.yaml",),
        api_version=None,
        started_at=runner.datetime(2026, 10, 10, tzinfo=runner.UTC),
    )
    text = runner.render_report(info, [result(top=None)], {})
    assert "No answer diagnostics were returned" in text
