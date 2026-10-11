"""Gate 3 rework (D-055): generation override, chat model override with restore, ingest job
status checks, retrieval fingerprints in the sidecar and comparing two runs."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import runner
from runner import JobInfo, SourceInfo
from test_runner import QS_URL, Clock, FakeApi, answer, cit, sse, write_questions

CODE_LOC = {"file": "src/requests/models.py", "start_line": 100, "end_line": 106}


def corpus_file(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "corpus.yaml"
    path.write_text(f"sources:\n  - folder: trustbook-eval/requests\n  - url: {QS_URL}\n{extra}")
    return path


def good_api() -> FakeApi:
    api = FakeApi()
    api.answers["Max redirects?"] = answer(
        ("The default limit is 30 redirects [1].", [cit(1, "src1", "requests", **CODE_LOC)])
    )
    api.answers["Redirects for HEAD?"] = answer(
        ("All verbs except HEAD [1].", [cit(1, "src2", "stable", heading="Redirection")])
    )
    return api


def run(
    api: FakeApi, tmp_path: Path, *extra: str, setup: bool = True
) -> tuple[int, str, str, Path]:
    report = tmp_path / "report.md"
    argv = ["--base-url", "http://tb", "--space", "Smoke", "--questions"]
    argv += [str(write_questions(tmp_path)), "--report", str(report)]
    if setup:
        argv += ["--setup", str(corpus_file(tmp_path))]
    out, log = io.StringIO(), io.StringIO()
    code = runner.main(
        [*argv, *extra],
        transport=httpx.MockTransport(api.handler),
        out=out,
        log=log,
        sleep=lambda _: None,
        clock=Clock(),
    )
    return code, out.getvalue(), log.getvalue(), tmp_path / "report.json"


# ---------------------------------------------------------------------------------------------
# F5: generation override
# ---------------------------------------------------------------------------------------------


def test_default_run_sends_temperature_0_and_seed_42_and_says_so(tmp_path: Path) -> None:
    api = good_api()
    code, text, _, sidecar = run(api, tmp_path)
    assert code == 0, text
    assert api.message_bodies
    assert all(b["generation"] == {"temperature": 0.0, "seed": 42} for b in api.message_bodies)
    assert "- Generation: temperature 0, seed 42 (eval override" in text
    assert "do not honour a seed" in text
    data = json.loads(sidecar.read_text())
    assert data["version"] == 2
    assert data["generation_requested"] == {"temperature": 0.0, "seed": 42}


def test_temperature_and_seed_flags(tmp_path: Path) -> None:
    api = good_api()
    code, text, _, _ = run(api, tmp_path, "--temperature", "0.3", "--seed", "7")
    assert code == 0, text
    assert all(b["generation"] == {"temperature": 0.3, "seed": 7} for b in api.message_bodies)
    assert "- Generation: temperature 0.3, seed 7" in text


def test_app_default_sends_no_override_and_is_labelled_real_use(tmp_path: Path) -> None:
    api = good_api()
    code, text, _, sidecar = run(api, tmp_path, "--app-default")
    assert code == 0, text
    assert all("generation" not in b for b in api.message_bodies)
    assert "**real-use run**" in text and "temperature 0.2 without a seed" in text
    assert json.loads(sidecar.read_text())["generation_requested"] is None


def test_app_default_conflicts_with_temperature(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run(FakeApi(), tmp_path, "--app-default", "--seed", "1")


def test_api_without_the_override_fails_clearly(tmp_path: Path) -> None:
    api = good_api()
    original = api.handler

    def strict(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            body = json.loads(request.content)
            if "generation" in body:
                detail = [{"loc": ["body", "generation"], "msg": "Extra inputs are not permitted"}]
                return httpx.Response(422, json={"detail": detail})
        return original(request)

    api.handler = strict  # type: ignore[method-assign]
    code, _, log, _ = run(api, tmp_path)
    assert code == 2
    assert "does not accept the eval generation override" in log and "--app-default" in log
    code, text, _, _ = run(api, tmp_path, "--app-default")
    assert code == 0, text


def test_generation_and_fingerprint_from_diagnostics(tmp_path: Path) -> None:
    api = good_api()
    diag = {
        "top_rerank": 0.8,
        "sentence_scores": [0.9],
        "regenerated": False,
        "retrieval_fingerprint": "fp-abc",
        "generation": {"temperature": 0.0, "seed": 42},
    }
    message = {
        "id": "m",
        "content": {"sentences": [{"text": "30 [1].", "citations": [cit(1, "src1", "r")]}]},
        "badge": "verified",
        "removed_sentences": [],
        "diagnostics": diag,
    }
    api.answers["Max redirects?"] = sse(("done", {"message": message}))
    code, text, _, sidecar = run(api, tmp_path)
    assert code == 0, text
    assert "- Generation reported by the app: temperature 0, seed 42 (1/3 answers)" in text
    rows = {q["id"]: q for q in json.loads(sidecar.read_text())["questions"]}
    assert rows["code-1"]["retrieval_fingerprint"] == "fp-abc"
    assert rows["code-1"]["generation"] == {"temperature": 0.0, "seed": 42}
    assert rows["docs-1"]["retrieval_fingerprint"] is None
    assert rows["code-1"]["must_contain_total"] == 1


# ---------------------------------------------------------------------------------------------
# F6: chat model override
# ---------------------------------------------------------------------------------------------


def test_chat_override_is_applied_tested_reported_and_restored(tmp_path: Path) -> None:
    api = good_api()
    seen: list[str] = []
    original = api.handler

    def spy(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            seen.append(api.chat_config["model"])  # the model in use while asking
        return original(request)

    api.handler = spy  # type: ignore[method-assign]
    code, text, log, sidecar = run(
        api,
        tmp_path,
        "--chat-endpoint",
        "http://192.168.1.5:8081/v1",
        "--chat-model",
        "Big-27B",
    )
    assert code == 0, text
    assert seen and set(seen) == {"Big-27B"}
    put, restore = api.chat_puts
    assert put == {"preset": "custom", "base_url": "http://192.168.1.5:8081/v1", "model": "Big-27B"}
    assert restore == {
        "preset": "llama_server",
        "base_url": "http://host.docker.internal:8080/v1",
        "model": "small-model",
        "api_version": None,
    }
    assert "api_key" not in put and "api_key" not in restore
    assert ("POST", "/api/capabilities/chat/test") in api.calls
    assert api.chat_config["model"] == "small-model"
    assert "- Chat model: `Big-27B` at `192.168.1.5:8081` (preset custom; set for this run" in text
    assert "chat settings restored" in log
    chat = json.loads(sidecar.read_text())["chat"]
    assert chat == {
        "model": "Big-27B",
        "endpoint_host": "192.168.1.5:8081",
        "preset": "custom",
        "overridden": True,
    }


def test_without_override_the_header_names_the_saved_model(tmp_path: Path) -> None:
    api = good_api()
    code, text, _, _ = run(api, tmp_path)
    assert code == 0
    assert (
        "- Chat model: `small-model` at `host.docker.internal:8080` "
        "(preset llama_server; saved settings)" in text
    )
    assert api.chat_puts == []


def test_chat_override_restored_on_error_and_on_failed_test(tmp_path: Path) -> None:
    api = good_api()
    # No --setup and no space: the run fails after the override was saved.
    code, _, log, _ = run(api, tmp_path, "--chat-model", "Big-27B", setup=False)
    assert code == 2 and "space not found" in log
    assert [p.get("model") for p in api.chat_puts] == ["Big-27B", "small-model"]
    assert api.chat_config["model"] == "small-model"

    api = good_api()
    api.chat_test_ok = False
    code, _, log, _ = run(api, tmp_path, "--chat-endpoint", "http://10.0.0.9:9/v1")
    assert code == 2 and "Connection refused" in log
    assert api.chat_config["base_url"] == "http://host.docker.internal:8080/v1"
    assert not [m for m, p in api.calls if p.endswith("/messages")]


def test_chat_override_restored_on_ctrl_c(tmp_path: Path) -> None:
    api = good_api()
    original = api.handler

    def interrupt(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            raise KeyboardInterrupt
        return original(request)

    api.handler = interrupt  # type: ignore[method-assign]
    code, _, log, _ = run(api, tmp_path, "--chat-model", "Big-27B")
    assert code == runner.EXIT_INTERRUPTED
    assert "interrupted" in log and api.chat_config["model"] == "small-model"


def test_chat_override_never_loses_a_saved_api_key(tmp_path: Path) -> None:
    api = good_api()
    api.chat_config |= {
        "preset": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt",
        "api_key_set": True,
        "api_key_hint": "…a1b2",
    }
    code, text, log, _ = run(api, tmp_path, "--chat-endpoint", "http://10.0.0.9:8080/v1")
    assert code == 2 and "API key" in log
    assert api.chat_puts == []  # nothing changed on the server
    # Same server, other model: the key stays where it is, so this is allowed.
    code, text, log, _ = run(api, tmp_path, "--chat-model", "gpt-big")
    assert code == 0, text
    assert "a1b2" not in text + log


def test_override_body_and_hosts() -> None:
    saved = runner.ChatConfig("llama_server", "http://h:8080/v1", "m", None)
    assert runner.chat_override_body(saved, None, "x") == {"model": "x"}
    with pytest.raises(runner.EvalError):
        runner.chat_override_body(saved, "not a url", None)
    keyed = runner.ChatConfig("openai", "https://api.openai.com/v1", "m", None, api_key_set=True)
    body = runner.chat_override_body(keyed, "https://API.openai.com/v2", None)
    assert body["preset"] == "custom"  # same origin: the key is kept by the server
    assert runner.endpoint_host("http://user:pw@10.1.2.3:8080/v1") == "10.1.2.3:8080"
    assert runner.endpoint_host("https://api.example.com/v1") == "api.example.com"


# ---------------------------------------------------------------------------------------------
# B: ingest job status during --setup
# ---------------------------------------------------------------------------------------------


def test_setup_reports_job_statuses_and_passes_when_all_done(tmp_path: Path) -> None:
    code, text, log, sidecar = run(good_api(), tmp_path)
    assert code == 0, text
    assert "| Ingest jobs ended as expected (setup) | 2/2 | all | pass |" in text
    assert "| requests | ready | done | ready | ok |" in text
    assert "job for requests: done, source ready" in log
    setup = json.loads(sidecar.read_text())["setup"]
    assert setup["jobs_ok"] is True
    assert [j["job_status"] for j in setup["jobs"]] == ["done", "done"]


def test_source_ready_but_job_failed_is_a_mismatch(tmp_path: Path) -> None:
    api = good_api()
    api.job_outcome[QS_URL] = "failed"  # Gate 3 F1: the job failed after the source was ready
    code, text, _, sidecar = run(api, tmp_path)
    assert code == 1, text
    assert "| Ingest jobs ended as expected (setup) | 1/2 | all | **FAIL** |" in text
    assert "**job/source mismatch: source ready, job failed: No active exception" in text
    assert json.loads(sidecar.read_text())["setup"]["jobs_ok"] is False


def test_expected_failures_pass_unexpected_ones_fail(tmp_path: Path) -> None:
    blocked = "https://example.org/blocked"
    api = good_api()
    api.fail_origins.add(blocked)
    corpus = runner.load_corpus(
        str(corpus_file(tmp_path, f"  - url: {blocked}\n    expect: failed\n"))
    )
    assert corpus.entries[-1].expect == "failed"
    with api.client() as client:
        _, rep = runner.setup_corpus(
            runner.Api(client),
            "Smoke",
            corpus,
            timeout_s=100,
            log=io.StringIO(),
            sleep=lambda _: None,
            clock=Clock(),
        )
    assert [(j.job_status, j.ok) for j in rep.jobs] == [("done", True)] * 2 + [("failed", True)]

    api = good_api()
    api.fail_origins.add(QS_URL)  # expected to be ready
    code, text, _, _ = run(api, tmp_path)
    assert code == 1
    assert "**source and job failed: blocked site**" in text


def test_jobs_api_failure_and_old_jobs(tmp_path: Path) -> None:
    api = good_api()
    api.jobs_status = 500
    code, text, _, _ = run(api, tmp_path)
    assert code == 1 and "job statuses unavailable" in text

    # Sources that were already in the space: their old jobs may no longer be listed.
    api = good_api()
    sid = api.add_space("Smoke")
    api.add_source(sid, "folder", "trustbook-eval/requests", "requests")
    api.add_source(sid, "url", QS_URL, "Quickstart")
    api.sources[sid][0]["job_id"] = "gone"
    code, text, _, _ = run(api, tmp_path)
    assert code == 0, text
    assert "| requests | ready | not listed | ready | ok (older job, no longer listed) |" in text


def test_load_corpus_rejects_bad_expect(tmp_path: Path) -> None:
    path = corpus_file(tmp_path, "  - url: https://x.org/\n    expect: maybe\n")
    with pytest.raises(runner.EvalError, match="expect"):
        runner.load_corpus(str(path))
    path = corpus_file(tmp_path, "  - url: https://x.org/\n    expected: failed\n")
    with pytest.raises(runner.EvalError, match="unknown keys"):
        runner.load_corpus(str(path))


@pytest.mark.parametrize(
    ("source", "job", "expect", "problem"),
    [
        ("ready", "done", "ready", None),
        ("failed", "failed", "failed", None),
        ("ready", "failed", "ready", "job/source mismatch: source ready, job failed: boom"),
        ("ready", "failed", "failed", "job/source mismatch: source ready, job failed: boom"),
        ("failed", "done", "ready", "job/source mismatch: source failed, job done"),
        ("ready", "running", "ready", "job still running"),
        ("ready", "cancelled", "ready", "job cancelled"),
        ("failed", "failed", "ready", "source and job failed: boom"),
        ("ready", "done", "failed", "expected to fail, but the job is done and the source ready"),
    ],
)
def test_check_job_matrix(source: str, job: str, expect: str, problem: str | None) -> None:
    src = SourceInfo("s1", "Doc", "https://x.org/", "url", source, job_id="j1")
    info = JobInfo("j1", "ingest", job, error="boom" if job == "failed" else None)
    assert runner.check_job(src, info, expect, added=True).problem == problem


def test_check_job_without_a_listed_job() -> None:
    src = SourceInfo("s1", "Doc", "https://x.org/", "url", "ready", job_id="j1")
    assert runner.check_job(src, None, "ready", added=True).problem == (
        "ingest job not listed in GET /api/jobs"
    )
    old = runner.check_job(src, None, "ready", added=False)
    assert old.ok and old.note == "older job, no longer listed"
    bare = SourceInfo("s1", "Doc", "https://x.org/", "url", "ready")
    assert runner.check_job(bare, None, "ready", added=True).problem == "source has no ingest job"


# ---------------------------------------------------------------------------------------------
# --compare
# ---------------------------------------------------------------------------------------------


def sidecar_with(questions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"version": 2, "date": "2026-10-11T10:00:00Z", "questions": questions} | extra


def qrow(qid: str, outcome: str, **kw: Any) -> dict[str, Any]:
    return {"id": qid, "type": "answerable", "outcome": outcome} | kw


def test_compare_separates_model_from_retrieval_differences(tmp_path: Path) -> None:
    a = sidecar_with(
        [
            qrow("same", "answered", citations=2, citations_correct=2, retrieval_fingerprint="f1"),
            qrow("model", "answered", citations=2, citations_correct=1, retrieval_fingerprint="f2"),
            qrow("pipe", "refused", refusal_reason="not_in_sources", retrieval_fingerprint="f3"),
            qrow("score", "answered", citations=1, citations_correct=1, top_rerank=0.5),
            qrow("onlya", "answered"),
        ],
        chat={"model": "Small", "endpoint_host": "h:1"},
        generation_requested={"temperature": 0.0, "seed": 42},
    )
    b = sidecar_with(
        [
            qrow("same", "answered", citations=2, citations_correct=2, retrieval_fingerprint="f1"),
            qrow("model", "answered", citations=2, citations_correct=2, retrieval_fingerprint="f2"),
            qrow("pipe", "answered", citations=1, citations_correct=1, retrieval_fingerprint="f9"),
            qrow("score", "refused", refusal_reason="unsupported", top_rerank=0.5),
        ],
        chat={"model": "Big", "endpoint_host": "h:2"},
        generation_requested=None,
    )
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))
    out = io.StringIO()
    assert runner.main(["--compare", str(pa), str(pb)], out=out, log=io.StringIO()) == 0
    text = out.getvalue()
    assert "`Small` at `h:1`, temperature 0, seed 42" in text
    assert "`Big` at `h:2`, app default (real-use)" in text
    assert "| model | answered 1/2 | answered 2/2 | same | model / sampling |" in text
    assert (
        "| pipe | refused (not_in_sources) | answered 1/1 | different | pipeline / index |" in text
    )
    assert (
        "| score | answered 1/1 | refused (unsupported) | same top score | model / sampling |"
        in text
    )
    assert "\n| same |" not in text  # identical outcomes are not listed
    assert "3 of 4 questions differ: 2 with the same retrieval" in text
    assert "Only in A: `onlya`." in text


def test_compare_rejects_bad_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    log = io.StringIO()
    assert runner.main(["--compare", str(bad), str(bad)], out=io.StringIO(), log=log) == 2
    assert "not a runner sidecar" in log.getvalue()


def test_space_and_questions_required_without_compare() -> None:
    with pytest.raises(SystemExit):
        runner.main(["--space", "x"], out=io.StringIO(), log=io.StringIO())
