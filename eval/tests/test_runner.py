"""Tests for eval/runner.py against an in-memory fake of the Trustbook API (no network)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import runner
from runner import (
    Citation,
    Locator,
    Question,
    QuestionResult,
    SourceInfo,
    citation_correct,
    compute_metrics,
    iter_sse,
    percentile,
)

SMOKE_DIR = Path(__file__).resolve().parent.parent / "smoke"
QS_URL = "https://requests.readthedocs.io/en/stable/user/quickstart/"
QS_TITLE = "Quickstart — Requests 2.34.2 documentation"


# ---------------------------------------------------------------------------------------------
# Fake API
# ---------------------------------------------------------------------------------------------


def sse(*events: tuple[str, Any]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def cit(n: int, source_id: str, title: str, **locator: Any) -> dict[str, Any]:
    return {
        "n": n,
        "chunk_id": f"{source_id}-c{n}",
        "source_id": source_id,
        "source_title": title,
        "locator": locator,
    }


def answer(*sentences: tuple[str, list[dict[str, Any]]]) -> str:
    events: list[tuple[str, Any]] = [("status", {"stage": "retrieving"})]
    for i, (text, cits) in enumerate(sentences):
        events.append(("sentence", {"index": i, "text": text, "citations": cits}))
    message = {
        "id": "m1",
        "role": "assistant",
        "created_at": "2026-10-10T00:00:00Z",
        "content": {"sentences": [{"text": t, "citations": c} for t, c in sentences]},
        "badge": None,
        "removed_sentences": [],
        "sources_used": [],
    }
    events.append(("done", {"message": message}))
    return sse(*events)


def refusal() -> str:
    refused = {"reason": "not_in_sources", "message": "Not in your sources", "suggestion": None}
    message = {"id": "m2", "role": "assistant", "content": {"sentences": []}, "badge": "refused"}
    return sse(
        ("status", {"stage": "retrieving"}), ("refused", refused), ("done", {"message": message})
    )


class FakeApi:
    """Just enough of docs/ARCHITECTURE.md's API for the runner."""

    def __init__(self) -> None:
        self.spaces: list[dict[str, Any]] = []
        self.sources: dict[str, list[dict[str, Any]]] = {}
        self.answers: dict[str, str | int] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_origins: set[str] = set()
        self.conversations = 0
        # Ingest jobs: one per source added through the API; a job ends like its source unless
        # ``job_outcome[origin]`` says otherwise (Gate 3 F1: source ready, job failed).
        self.jobs: list[dict[str, Any]] = []
        self.job_outcome: dict[str, str] = {}
        self.jobs_status = 200
        self.refresh_refused: set[str] = set()
        self.message_bodies: list[dict[str, Any]] = []
        self.chat_config: dict[str, Any] = {
            "enabled": True,
            "preset": "llama_server",
            "base_url": "http://host.docker.internal:8080/v1",
            "model": "small-model",
            "api_version": None,
            "api_key_set": False,
            "api_key_hint": None,
        }
        self.chat_puts: list[dict[str, Any]] = []
        self.chat_test_ok = True

    def add_space(self, name: str, sid: str = "sp1") -> str:
        self.spaces.append({"id": sid, "slug": name.lower().replace(" ", "-"), "name": name})
        self.sources[sid] = []
        return sid

    def add_source(
        self, sid: str, kind: str, origin: str, title: str, status: str = "ready"
    ) -> str:
        src_id = f"src{sum(len(v) for v in self.sources.values()) + 1}"
        self.sources[sid].append(
            {
                "id": src_id,
                "type": kind,
                "title": title,
                "origin": origin,
                "status": status,
                "progress": None,
                "failure_reason": None,
            }
        )
        return src_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        parts = path.strip("/").split("/")
        body = json.loads(request.content) if request.content else {}
        if path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "version": "phase-1-test"})
        if path == "/api/spaces" and method == "GET":
            return httpx.Response(200, json=self.spaces)
        if path == "/api/spaces" and method == "POST":
            sid = self.add_space(body["name"], sid=f"sp{len(self.spaces) + 1}")
            return httpx.Response(201, json=self.spaces[-1] | {"id": sid})
        if path == "/api/jobs" and method == "GET":
            if self.jobs_status != 200:
                return httpx.Response(self.jobs_status, json={"detail": "nope"})
            self._advance_jobs()
            return httpx.Response(200, json={"jobs": self.jobs, "summary": {}})
        if path == "/api/capabilities/chat":
            return self._chat(method, body)
        if path == "/api/capabilities/chat/test" and method == "POST":
            ok = self.chat_test_ok
            message = "Chat works." if ok else "Connection refused"
            return httpx.Response(200, json={"ok": ok, "message": message, "latency_ms": 5})
        if len(parts) >= 4 and parts[:2] == ["api", "spaces"]:
            sid = parts[2]
            if sid not in self.sources:
                return httpx.Response(404, json={"detail": "Space not found"})
            rest = parts[3:]
            if rest == ["sources"] and method == "GET":
                self._advance(sid)
                return httpx.Response(200, json=self.sources[sid])
            if len(rest) == 2 and rest[0] == "sources" and method == "POST":
                origin = body.get("path") or body.get("url")
                title = origin.rstrip("/").rsplit("/", 1)[-1]
                src_id = self.add_source(sid, rest[1], origin, title, status="queued")
                self._new_job(self.sources[sid][-1])
                return httpx.Response(201, json=self.sources[sid][-1] | {"id": src_id})
            if len(rest) == 3 and rest[::2] == ["sources", "refresh"] and method == "POST":
                src = next((x for x in self.sources[sid] if x["id"] == rest[1]), None)
                if src is None:
                    return httpx.Response(404, json={"detail": "Source not found"})
                if src["origin"] in self.refresh_refused:
                    return httpx.Response(409, json={"detail": "can't be refreshed"})
                src["status"], src["failure_reason"] = "queued", None
                self._new_job(src)
                return httpx.Response(200, json=src)
            if rest == ["conversations"] and method == "POST":
                self.conversations += 1
                return httpx.Response(201, json={"id": f"c{self.conversations}", "title": "t"})
            if len(rest) == 3 and rest[0] == "conversations" and rest[2] == "messages":
                self.message_bodies.append(body)
                reply = self.answers.get(body["content"], refusal())
                if isinstance(reply, int):
                    return httpx.Response(reply, json={"detail": "boom"})
                return httpx.Response(
                    200, content=reply.encode(), headers={"content-type": "text/event-stream"}
                )
        return httpx.Response(404, json={"detail": "Not Found"})

    def _new_job(self, src: dict[str, Any], status: str = "queued") -> str:
        job_id = f"job{len(self.jobs) + 1}"
        src["job_id"] = job_id
        error = "No active exception to reraise" if status == "failed" else None
        self.jobs.append(
            {"id": job_id, "kind": "ingest", "status": status, "error": error}
            | {"_source": src["id"], "_origin": src["origin"]}
        )
        return job_id

    def _advance(self, sid: str) -> None:
        flow = {"queued": "reading", "reading": "indexing", "indexing": "ready"}
        for s in self.sources[sid]:
            if s["status"] in flow:
                nxt = flow[s["status"]]
                if nxt == "ready" and s["origin"] in self.fail_origins:
                    s["status"], s["failure_reason"] = "failed", "blocked site"
                else:
                    s["status"] = nxt

    def _advance_jobs(self) -> None:
        """A job is running while its source is busy and ends one poll after it."""
        status = {s["id"]: s["status"] for v in self.sources.values() for s in v}
        for job in self.jobs:
            source = status.get(job["_source"])
            if job["status"] == "queued" and source not in (None, "queued"):
                job["status"] = "running"
            elif job["status"] == "running" and source in ("ready", "failed"):
                end = "done" if source == "ready" else "failed"
                job["status"] = self.job_outcome.get(job["_origin"], end)
                if job["status"] == "failed":
                    job["error"] = "No active exception to reraise"

    def _chat(self, method: str, body: dict[str, Any]) -> httpx.Response:
        if method == "PUT":
            self.chat_puts.append(body)
            for name, value in body.items():
                self.chat_config[name] = value
        cfg = self.chat_config
        return httpx.Response(200, json={"id": "chat", "config": cfg, "status": "ready"})

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://tb", transport=httpx.MockTransport(self.handler))


class Clock:
    def __init__(self, step: float = 1.0) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def q(qid: str = "q1", qtype: str = "answerable", **kw: Any) -> Question:
    return Question(id=qid, question=kw.pop("question", f"question {qid}?"), type=qtype, **kw)


# ---------------------------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------------------------


def test_iter_sse_basic_multiline_comments_and_crlf() -> None:
    lines = [
        ": keep-alive",
        "event: sentence\r",
        'data: {"a":',
        "data:1}",
        "",
        "data: plain",
        "",
        "event: done",
        'data: {"x": 2}',  # no trailing blank line
    ]
    events = list(iter_sse(lines))
    assert [e.event for e in events] == ["sentence", "message", "done"]
    assert json.loads(events[0].data) == {"a": 1}
    assert events[1].data == "plain"
    assert json.loads(events[2].data) == {"x": 2}


def test_iter_sse_ignores_event_without_data() -> None:
    assert list(iter_sse(["event: status", "", "id: 3", "retry: 10", ""])) == []


# ---------------------------------------------------------------------------------------------
# Question format
# ---------------------------------------------------------------------------------------------


def test_parse_questions_and_locators() -> None:
    data = {
        "questions": [
            {
                "id": "a",
                "question": "Q?",
                "type": "answerable",
                "category": "code",
                "expected_sources": ["requests"],
                "expected_locators": [
                    {"file": "src/requests/api.py", "lines": [10, 20]},
                    {"page": 3},
                    {"pages": [4, 5]},
                    {"heading": "Quickstart", "source": QS_URL},
                    {"start_s": 1, "end_s": 2.5},
                ],
                "must_contain": "30",
            },
            {"id": 2, "question": "U?", "type": "unanswerable"},
        ]
    }
    qs = runner.parse_questions(data, "f.yaml")
    assert [x.id for x in qs] == ["a", "2"]
    locs = qs[0].expected_locators
    assert locs[0] == Locator(file="src/requests/api.py", lines=(10, 20))
    assert locs[1].pages == frozenset({3}) and locs[2].pages == frozenset({4, 5})
    assert locs[3].kind == "heading" and locs[3].source == QS_URL
    assert locs[4].seconds == (1.0, 2.5)
    assert qs[0].must_contain == ("30",)
    assert qs[1].expected_sources == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "x", "question": "Q", "type": "answerable"},  # no expected_sources
        {"id": "x", "question": "Q", "type": "maybe", "expected_sources": ["s"]},
        {"id": "x", "question": "", "type": "unanswerable"},
        {"id": "x", "question": "Q", "type": "unanswerable", "category": "misc"},
        {
            "id": "x",
            "question": "Q",
            "type": "answerable",
            "expected_sources": ["s"],
            "expected_locators": [{"lines": [1, 2]}],
        },
        {
            "id": "x",
            "question": "Q",
            "type": "answerable",
            "expected_sources": ["s"],
            "expected_locators": [{"file": "a.py", "lines": [9, 2]}],
        },
    ],
)
def test_parse_questions_rejects_invalid(bad: dict[str, Any]) -> None:
    with pytest.raises(runner.EvalError):
        runner.parse_questions([bad], "f.yaml")


def test_load_questions_dir_skips_corpus_and_rejects_duplicates(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("- {id: one, question: 'Q?', type: unanswerable}\n")
    (tmp_path / "corpus.yaml").write_text("sources: []\n")
    assert [x.id for x in runner.load_questions([str(tmp_path)])] == ["one"]
    (tmp_path / "b.yml").write_text("- {id: one, question: 'Q2?', type: unanswerable}\n")
    with pytest.raises(runner.EvalError, match="duplicate"):
        runner.load_questions([str(tmp_path)])


def test_smoke_questions_file_is_valid() -> None:
    qs = runner.load_questions([str(SMOKE_DIR / "questions.yaml")])
    assert len(qs) == 15
    cats = [x.category for x in qs]
    assert cats.count("docs") == 5 and cats.count("code") == 5
    assert cats.count("multi") == 2 and cats.count("unanswerable") == 3
    assert all((x.type == "unanswerable") == (x.category == "unanswerable") for x in qs)
    for x in qs:
        if x.category == "code":
            assert any(loc.lines for loc in x.expected_locators), x.id
    corpus = runner.load_corpus(str(SMOKE_DIR / "corpus.yaml"))
    assert [e.kind for e in corpus.entries].count("folder") == 1


# ---------------------------------------------------------------------------------------------
# Citation precision
# ---------------------------------------------------------------------------------------------

SOURCES = {
    "s-code": SourceInfo("s-code", "requests", "/host/trustbook-eval/requests", "folder", "ready"),
    "s-qs": SourceInfo("s-qs", "Quickstart", QS_URL, "url", "ready"),
    "s-other": SourceInfo("s-other", "Other", "https://example.org/x", "url", "ready"),
}


def c(source_id: str, n: int = 1, **locator: Any) -> Citation:
    title = SOURCES[source_id].title if source_id in SOURCES else "Gone"
    return Citation(n, f"{source_id}-{n}", source_id, title, locator)


def test_precision_source_only_by_title_url_and_path() -> None:
    by_title = q(expected_sources=("REQUESTS",))
    by_url = q(expected_sources=(QS_URL.rstrip("/") + "#top",))
    by_path = q(expected_sources=("trustbook-eval/requests",))
    assert citation_correct(by_title, c("s-code", file="src/requests/api.py"), SOURCES)
    assert citation_correct(by_url, c("s-qs", heading="Timeouts"), SOURCES)
    assert citation_correct(by_path, c("s-code", file="x.py"), SOURCES)
    assert not citation_correct(by_url, c("s-other"), SOURCES)
    assert not citation_correct(by_title, c("s-qs"), SOURCES)
    # title with HTML entity and em dash matches the plain title
    q_dash = q(expected_sources=("Quick&#8212;start",))
    info = {"s": SourceInfo("s", "Quick—start", None, "url", "ready")}
    assert citation_correct(q_dash, Citation(1, "x", "s", "Quick—start", {}), info)


def test_precision_with_file_locators() -> None:
    qq = q(
        expected_sources=("requests",),
        expected_locators=(Locator(file="src/requests/sessions.py", lines=(400, 480)),),
    )
    ok = c("s-code", file="requests/src/requests/sessions.py", start_line=470, end_line=520)
    edge = c("s-code", file="src\\requests\\sessions.py", start_line=480, end_line=480)
    before = c("s-code", file="src/requests/sessions.py", start_line=300, end_line=399)
    other_file = c("s-code", file="src/requests/models.py", start_line=400, end_line=480)
    no_lines = c("s-code", file="src/requests/sessions.py")
    assert citation_correct(qq, ok, SOURCES)
    assert citation_correct(qq, edge, SOURCES)
    assert not citation_correct(qq, before, SOURCES)
    assert not citation_correct(qq, other_file, SOURCES)
    assert not citation_correct(qq, no_lines, SOURCES)
    # file locators do not apply to a web citation: judged on the source alone
    multi = q(expected_sources=("requests", QS_URL), expected_locators=qq.expected_locators)
    assert citation_correct(multi, c("s-qs", heading="Anything"), SOURCES)


def test_precision_with_page_heading_time_and_source_scoped_locators() -> None:
    pages = q(expected_sources=("Quickstart",), expected_locators=(Locator(pages=frozenset({3})),))
    assert citation_correct(pages, c("s-qs", pages=[2, 3]), SOURCES)
    assert not citation_correct(pages, c("s-qs", pages=[4]), SOURCES)
    head = q(expected_sources=("Quickstart",), expected_locators=(Locator(heading="Timeouts"),))
    assert citation_correct(head, c("s-qs", heading="timeouts¶", offset=9), SOURCES)
    assert citation_correct(head, c("s-qs", heading="Quickstart > Timeouts"), SOURCES)
    assert not citation_correct(head, c("s-qs", heading="Errors and Exceptions"), SOURCES)
    secs = q(expected_sources=("Quickstart",), expected_locators=(Locator(seconds=(60, 90)),))
    assert citation_correct(secs, c("s-qs", start_s=80.0, end_s=120.0), SOURCES)
    assert not citation_correct(secs, c("s-qs", start_s=91.0, end_s=95.0), SOURCES)
    scoped = q(
        expected_sources=("requests", "Quickstart"),
        expected_locators=(Locator(heading="Timeouts", source="requests"),),
    )
    # the heading locator is scoped to the folder source, so the web citation is source-only
    assert citation_correct(scoped, c("s-qs", heading="Errors"), SOURCES)


def test_precision_falls_back_to_citation_title_for_unknown_source() -> None:
    qq = q(expected_sources=("Gone",))
    assert citation_correct(qq, c("s-missing"), SOURCES)


# ---------------------------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------------------------


def test_percentiles() -> None:
    assert percentile([], 50) is None
    assert percentile([7.0], 95) == 7.0
    assert percentile([5.0, 1.0, 3.0, 2.0, 4.0], 50) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95) == pytest.approx(4.8)
    assert percentile([1.0, 2.0], 50) == 1.5


def result(
    question: Question, outcome: str, latency: float | None, correct: list[bool]
) -> QuestionResult:
    r = QuestionResult(question=question, outcome=outcome, latency_s=latency)
    r.citations = [c("s-code", n=i + 1) for i in range(len(correct))]
    r.correct = correct
    return r


def test_compute_metrics() -> None:
    ans = q("a", expected_sources=("requests",), must_contain=("30",))
    unans = q("u", "unanswerable")
    results = [
        result(ans, "answered", 2.0, [True, True, False]),
        result(q("b", expected_sources=("x",)), "answered", 4.0, [True]),
        result(q("c", expected_sources=("x",)), "refused", 1.0, []),  # false refusal
        result(q("d", expected_sources=("x",)), "error", None, []),
        result(unans, "refused", 1.0, []),
        result(q("u2", "unanswerable"), "answered", 3.0, [False]),  # not counted in precision
    ]
    results[0].must_found = ["30"]
    m = compute_metrics(results)
    assert (m.precision.num, m.precision.den) == (3, 4)
    assert (m.correct_refusal.num, m.correct_refusal.den) == (1, 2)
    assert (m.false_refusal.num, m.false_refusal.den) == (1, 4)
    assert (m.must_contain.num, m.must_contain.den) == (1, 1)
    assert m.errors == 1
    assert m.latency_count == 5
    assert m.latency_p50 == 2.0
    assert runner.thresholds_met(m, 0.75, None)
    assert not runner.thresholds_met(m, 0.9, None)
    assert not runner.thresholds_met(m, None, 0.95)
    assert runner.thresholds_met(m, None, None, 1.0)
    empty = compute_metrics([])
    assert empty.precision.value is None
    assert not runner.thresholds_met(empty, 0.9, None)
    assert not runner.thresholds_met(empty, None, None, 0.8)  # nothing to measure fails


def test_must_contain_hit_rate_counts_misses_and_refusals() -> None:
    results = [
        result(q("a", must_contain=("10", "False")), "answered", 1.0, [True]),
        result(q("b", must_contain=("10",)), "answered", 1.0, [True]),
        result(q("c", must_contain=("30",)), "refused", 1.0, []),
        result(q("d"), "answered", 1.0, [True]),  # no keywords: not counted
    ]
    results[0].must_found = ["10"]  # one of two keywords is not enough
    results[1].must_found = ["10"]
    m = compute_metrics(results)
    assert (m.must_contain.num, m.must_contain.den) == (1, 3)
    assert not runner.thresholds_met(m, None, None, 0.8)
    assert runner.thresholds_met(m, None, None, 0.3)


# ---------------------------------------------------------------------------------------------
# Asking questions over SSE
# ---------------------------------------------------------------------------------------------


def make_api() -> tuple[FakeApi, str, str, str]:
    api = FakeApi()
    sid = api.add_space("requests smoke")
    code = api.add_source(sid, "folder", "trustbook-eval/requests", "requests")
    docs = api.add_source(sid, "url", QS_URL, QS_TITLE)
    return api, sid, code, docs


def test_ask_answered_dedupes_citations_and_uses_done_message() -> None:
    api, sid, code, docs = make_api()
    c1 = cit(1, code, "requests", file="src/requests/models.py", start_line=100, end_line=110)
    c2 = cit(2, docs, QS_TITLE, heading="Redirection and History", offset=10)
    api.answers["Max redirects?"] = answer(("It is 30 [1].", [c1]), ("Yes [1][2].", [c1, c2]))
    clock = Clock(step=0.5)
    with api.client() as client:
        r = runner.ask(client, sid, q(question="Max redirects?"), clock=clock)
    assert r.outcome == "answered"
    assert r.latency_s == 0.5
    assert r.sentences == ["It is 30 [1].", "Yes [1][2]."]
    assert [x.n for x in r.citations] == [1, 2]
    assert r.citations[0].locator["start_line"] == 100
    assert ("POST", f"/api/spaces/{sid}/conversations") in api.calls


def test_ask_refused_then_done() -> None:
    api, sid, _, _ = make_api()
    with api.client() as client:
        r = runner.ask(client, sid, q("u", "unanswerable"), clock=Clock())
    assert r.outcome == "refused"
    assert r.refusal_reason == "not_in_sources"
    assert r.latency_s == 1.0
    assert r.citations == []


def test_ask_done_with_refused_badge_only() -> None:
    api, sid, _, _ = make_api()
    msg = {"id": "m", "content": {"sentences": []}, "badge": "refused"}
    api.answers["x?"] = sse(("done", {"message": msg}))
    with api.client() as client:
        r = runner.ask(client, sid, q(question="x?"), clock=Clock())
    assert r.outcome == "refused"
    assert r.refusal_reason == "refused"  # a message stored without a reason (before D-118)
    api.answers["y?"] = sse(("done", {"message": {**msg, "refusal_reason": "unsupported"}}))
    with api.client() as client:
        r = runner.ask(client, sid, q(question="y?"), clock=Clock())
    assert (r.outcome, r.refusal_reason) == ("refused", "unsupported")  # the stored reason


@pytest.mark.parametrize(
    ("reply", "fragment"),
    [
        (sse(("status", {"stage": "generating"}), ("error", {"message": "LLM down"})), "LLM down"),
        (sse(("sentence", {"index": 0, "text": "a", "citations": []})), "without done"),
        ("event: done\ndata: {not json\n\n", "bad done"),
        (503, "HTTP 503"),
    ],
)
def test_ask_errors(reply: str | int, fragment: str) -> None:
    api, sid, _, _ = make_api()
    api.answers["x?"] = reply
    with api.client() as client:
        r = runner.ask(client, sid, q(question="x?"), clock=Clock())
    assert r.outcome == "error"
    assert r.latency_s is None
    assert fragment in (r.error or "")


# ---------------------------------------------------------------------------------------------
# Setup (corpus ingestion)
# ---------------------------------------------------------------------------------------------


def write_corpus(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.yaml"
    path.write_text(
        "space_description: smoke\n"
        "sources:\n"
        "  - folder: trustbook-eval/requests\n"
        f"  - url: {QS_URL}\n"
        "  - url: https://example.org/blocked\n"
    )
    return path


def test_setup_creates_space_adds_sources_waits_and_is_idempotent(tmp_path: Path) -> None:
    api = FakeApi()
    api.fail_origins.add("https://example.org/blocked")
    corpus = runner.load_corpus(str(write_corpus(tmp_path)))
    sleeps: list[float] = []
    log = io.StringIO()
    with api.client() as client:
        space, rep = runner.setup_corpus(
            runner.Api(client),
            "Smoke",
            corpus,
            timeout_s=100,
            poll_s=2,
            log=log,
            sleep=sleeps.append,
            clock=Clock(),
        )
    assert rep.space_created and space["name"] == "Smoke"
    assert len(rep.added) == 3 and rep.skipped == []
    assert sleeps and all(s == 2 for s in sleeps)
    assert rep.failed == ["blocked: blocked site"]
    assert not rep.timed_out
    assert "2/3 ready, 1 failed" in log.getvalue()
    posts = [p for m, p in api.calls if m == "POST" and "/sources/" in p]
    assert len(posts) == 3

    # second run: nothing is added again, nothing to wait for
    api.calls.clear()
    with api.client() as client:
        space2, rep2 = runner.setup_corpus(
            runner.Api(client),
            "smoke",
            corpus,
            timeout_s=100,
            log=io.StringIO(),
            sleep=sleeps.append,
            clock=Clock(),
        )
    assert space2["id"] == space["id"] and not rep2.space_created
    assert rep2.added == [] and len(rep2.skipped) == 3
    assert not [p for m, p in api.calls if m == "POST"]


def test_setup_matches_existing_folder_by_absolute_origin(tmp_path: Path) -> None:
    api = FakeApi()
    sid = api.add_space("Smoke")
    api.add_source(sid, "folder", "/host/trustbook-eval/requests/", "requests")
    api.add_source(sid, "url", QS_URL.rstrip("/"), QS_TITLE)
    api.add_source(sid, "url", "https://example.org/blocked", "x", status="failed")
    corpus = runner.load_corpus(str(write_corpus(tmp_path)))
    with api.client() as client:
        _, rep = runner.setup_corpus(
            runner.Api(client),
            "Smoke",
            corpus,
            timeout_s=10,
            log=io.StringIO(),
            sleep=lambda _: None,
            clock=Clock(),
        )
    assert rep.added == [] and len(rep.skipped) == 3


def test_setup_times_out(tmp_path: Path) -> None:
    api = FakeApi()
    sid = api.add_space("Smoke")
    api.add_source(sid, "folder", "trustbook-eval/requests", "requests", status="stuck")
    path = tmp_path / "c.yaml"
    path.write_text("sources:\n  - folder: trustbook-eval/requests\n")
    with api.client() as client:
        _, rep = runner.setup_corpus(
            runner.Api(client),
            "Smoke",
            runner.load_corpus(str(path)),
            timeout_s=5,
            log=io.StringIO(),
            sleep=lambda _: None,
            clock=Clock(),
        )
    assert rep.timed_out and rep.not_ready == ["requests"]


def test_load_corpus_rejects_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("sources:\n  - {folder: a, url: https://x}\n")
    with pytest.raises(runner.EvalError):
        runner.load_corpus(str(path))
    path.write_text("sources:\n  - url: not-a-url\n")
    with pytest.raises(runner.EvalError):
        runner.load_corpus(str(path))


# ---------------------------------------------------------------------------------------------
# Report and CLI
# ---------------------------------------------------------------------------------------------


def test_render_report_lists_wrong_citations_and_escapes_pipes() -> None:
    ans = q(
        "code|1",
        expected_sources=("requests",),
        expected_locators=(Locator(file="src/requests/models.py", lines=(104, 104)),),
        category="code",
    )
    r1 = QuestionResult(question=ans, outcome="answered", latency_s=2.0, sentences=["30"])
    r1.citations = [
        c("s-code", 1, file="src/requests/models.py", start_line=100, end_line=110),
        c("s-code", 2, file="src/requests/utils.py", start_line=1, end_line=40),
    ]
    r2 = QuestionResult(question=q("u1", "unanswerable"), outcome="answered", latency_s=1.0)
    r2.sentences = ["HTTP/2 is enabled with http2=True."]
    for r in (r1, r2):
        runner.score(r, SOURCES)
    info = runner.RunInfo(
        base_url="http://tb",
        space_name="Smoke",
        space_id="sp1",
        question_files=("eval/smoke/questions.yaml",),
        api_version="phase-1-rc",
        started_at=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
        min_precision=0.9,
        min_refusal=0.95,
        min_must_contain=0.8,
    )
    text = runner.render_report(info, [r1, r2], SOURCES)
    assert (
        "| must_contain hit rate (answerable with keywords) | n/a (0/0) | >= 80% | **FAIL** |"
        in text
    )
    assert "| Citation precision (answerable) | 50.0% (1/2) | >= 90% | **FAIL** |" in text
    assert "| Correct-refusal rate (unanswerable) | 0.0% (0/1) | >= 95% | **FAIL** |" in text
    assert "| code\\|1 | answerable | code | answered | 1/2 | 1/1 | - | 2.0 s |" in text
    assert "`code|1` [2] requests: src/requests/utils.py lines 1-40" in text
    assert "expected: requests; src/requests/models.py lines 104-104" in text
    assert "should refuse" in text and "HTTP/2 is enabled" in text
    assert "2026-10-10 12:00 UTC" in text


def write_questions(tmp_path: Path) -> Path:
    data = [
        {
            "id": "code-1",
            "question": "Max redirects?",
            "type": "answerable",
            "category": "code",
            "expected_sources": ["requests"],
            "expected_locators": [{"file": "src/requests/models.py", "lines": [104, 104]}],
            "must_contain": ["30"],
        },
        {
            "id": "docs-1",
            "question": "Redirects for HEAD?",
            "type": "answerable",
            "category": "docs",
            "expected_sources": [QS_TITLE, QS_URL],
        },
        {"id": "un-1", "question": "WebSockets?", "type": "unanswerable"},
    ]
    path = tmp_path / "questions.yaml"
    path.write_text(json.dumps(data))  # JSON is valid YAML
    return path


def test_main_end_to_end_with_setup_and_thresholds(tmp_path: Path) -> None:
    api = FakeApi()
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(f"sources:\n  - folder: trustbook-eval/requests\n  - url: {QS_URL}\n")
    code_src, docs_src = "src1", "src2"  # ids the fake assigns in corpus order
    loc = {"file": "src/requests/models.py", "start_line": 100, "end_line": 106}
    api.answers["Max redirects?"] = answer(
        ("The default limit is 30 redirects [1].", [cit(1, code_src, "requests", **loc)])
    )
    api.answers["Redirects for HEAD?"] = answer(
        ("All verbs except HEAD [1].", [cit(1, docs_src, "stable", heading="Redirection")])
    )
    qpath = write_questions(tmp_path)
    report_path = tmp_path / "report.md"
    out = io.StringIO()
    argv = [
        "--base-url",
        "http://tb",
        "--space",
        "Smoke",
        "--questions",
        str(qpath),
        "--setup",
        str(corpus),
        "--report",
        str(report_path),
        "--min-precision",
        "0.9",
        "--min-refusal",
        "95",
        "--min-must-contain",
        "0.8",
    ]
    transport = httpx.MockTransport(api.handler)
    code = runner.main(
        argv, transport=transport, out=out, log=io.StringIO(), sleep=lambda _: None, clock=Clock()
    )
    text = out.getvalue()
    assert code == 0, text
    assert report_path.read_text() == text
    assert "100.0% (2/2)" in text and "| pass |" in text
    assert (
        "| must_contain hit rate (answerable with keywords) | 100.0% (1/1) | >= 80% | pass |"
        in text
    )
    assert "| un-1 | unanswerable | - | refused (not_in_sources) |" in text
    # docs source title in the fake is "stable", so only the URL entry resolves
    assert f"`{QS_TITLE}`" in text

    # a stricter threshold than achieved -> exit 1; same space, nothing re-added
    api.answers["Max redirects?"] = answer(
        ("Wrong file [1].", [cit(1, code_src, "requests", file="src/requests/api.py")])
    )
    posts_before = len([1 for m, p in api.calls if m == "POST" and "/sources/" in p])
    code = runner.main(
        argv,
        transport=transport,
        out=io.StringIO(),
        log=io.StringIO(),
        sleep=lambda _: None,
        clock=Clock(),
    )
    assert code == 1
    assert len([1 for m, p in api.calls if m == "POST" and "/sources/" in p]) == posts_before


def test_main_unreachable_and_unknown_space(tmp_path: Path) -> None:
    qpath = write_questions(tmp_path)

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    log = io.StringIO()
    argv = ["--space", "Smoke", "--questions", str(qpath)]
    assert runner.main(argv, transport=httpx.MockTransport(down), log=log) == 2
    assert "unreachable" in log.getvalue()
    api = FakeApi()
    assert runner.main(argv, transport=httpx.MockTransport(api.handler), log=log) == 2
    assert "space not found" in log.getvalue()
    assert runner.main(["--space", "x", "--questions", str(tmp_path / "nope.yaml")], log=log) == 2


def test_main_exits_1_when_must_contain_is_below_target(tmp_path: Path) -> None:
    api = FakeApi()
    loc = {"file": "src/requests/models.py", "start_line": 100, "end_line": 106}
    # Right source and lines, but the answer stops at the constant's name (Gate 1 code-03).
    api.answers["Max redirects?"] = answer(
        ("The limit is DEFAULT_REDIRECT_LIMIT [1].", [cit(1, "src1", "requests", **loc)])
    )
    api.answers["Redirects for HEAD?"] = answer(
        ("All verbs except HEAD [1].", [cit(1, "src2", "stable", heading="Redirection")])
    )
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(f"sources:\n  - folder: trustbook-eval/requests\n  - url: {QS_URL}\n")
    argv = ["--base-url", "http://tb", "--space", "Smoke", "--questions"]
    argv += [str(write_questions(tmp_path)), "--setup", str(corpus)]
    out = io.StringIO()
    code = runner.main(
        [*argv, "--min-precision", "0.9", "--min-must-contain", "0.8"],
        transport=httpx.MockTransport(api.handler),
        out=out,
        log=io.StringIO(),
        sleep=lambda _: None,
        clock=Clock(),
    )
    text = out.getvalue()
    assert code == 1, text
    assert "| Citation precision (answerable) | 100.0% (2/2) | >= 90% | pass |" in text
    assert (
        "| must_contain hit rate (answerable with keywords) | 0.0% (0/1) | >= 80% | **FAIL** |"
        in text
    )


def test_heading_locator_matches_any_listed_section() -> None:
    from runner import Locator, locator_matches

    expected = Locator(heading="Blocking Or Non-Blocking?")
    spanning = {
        "heading": "Example: Specific SSL Version",
        "headings": ["Example: Specific SSL Version", "Blocking Or Non-Blocking?"],
    }
    assert locator_matches(expected, spanning)
    assert not locator_matches(expected, {"heading": "Example: Specific SSL Version"})
