"""Comparison models in the same command (--also-model / --also-endpoint, D-115, G3-3): the main
model decides the result and the exit code; every comparison model is asked the same questions
afterwards and reported next to it, never as a criterion. The saved chat settings come back in
every case."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
import runner
from test_rework import corpus_file, good_api
from test_runner import Clock, FakeApi, answer, cit, write_questions

SAVED = {
    "preset": "llama_server",
    "base_url": "http://host.docker.internal:8080/v1",
    "model": "small-model",
    "api_version": None,
}


def code_root(tmp_path: Path) -> Path:
    root = tmp_path / "requests"
    (root / "src" / "requests").mkdir(parents=True)
    (root / "src" / "requests" / "models.py").write_text("x = 1\n" * 120)
    return root


def per_model(api: FakeApi, replies: dict[str, dict[str, str]]) -> list[str]:
    """Answer by the chat model in use (``replies[model][question]``; the fake's own answers
    otherwise). Returns the model seen by every question, in order."""
    seen: list[str] = []
    original = api.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            model = str(api.chat_config["model"])
            seen.append(model)
            content = json.loads(request.content)["content"]
            reply = replies.get(model, {}).get(content)
            if reply is not None:
                return httpx.Response(
                    200, content=reply.encode(), headers={"content-type": "text/event-stream"}
                )
        return original(request)

    api.handler = handler  # type: ignore[method-assign]
    return seen


def run(api: FakeApi, tmp_path: Path, *extra: str) -> tuple[int, str, str, Path]:
    report = tmp_path / "report.md"
    argv = ["--base-url", "http://tb", "--space", "Smoke", "--setup", str(corpus_file(tmp_path))]
    argv += ["--questions", str(write_questions(tmp_path)), "--report", str(report)]
    argv += ["--min-precision", "0.9", "--min-refusal", "0.95"]
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


# A comparison model that cites the wrong lines for the code question and answers the
# unanswerable one: it fails both targets, which must not change the exit code.
WEAK = {
    "Other-9B": {
        "Max redirects?": answer(
            (
                "It is 30 [1].",
                [
                    cit(
                        1,
                        "src1",
                        "requests",
                        file="src/requests/models.py",
                        start_line=1,
                        end_line=5,
                    )
                ],
            )
        ),
        "WebSockets?": answer(("Use websocket-client [1].", [cit(1, "src2", "stable")])),
    }
}


def test_comparison_model_is_reported_next_to_the_main_model_not_judged(tmp_path: Path) -> None:
    api = good_api()
    seen = per_model(api, WEAK)
    root = code_root(tmp_path)
    code, text, log, sidecar = run(
        api, tmp_path, "--code-root", str(root), "--also-model", "Other-9B"
    )
    assert code == runner.EXIT_OK, text  # the main model met every target
    assert seen == ["small-model"] * 3 + ["Other-9B"] * 3  # main run first, then the comparison
    assert api.chat_puts == [{"model": "Other-9B"}, SAVED]  # switched once, then restored
    assert api.chat_config["model"] == "small-model"
    assert "chat settings restored" in log

    head, section = text.split("### Comparison models (reported, not a criterion)")
    assert "- Comparison models: `Other-9B`, asked the same questions after this run" in head
    assert "| Citation precision (answerable) | 100.0% (2/2) | >= 90% | pass |" in head
    assert (
        "| `small-model` at `host.docker.internal:8080` | main | 100.0% (2/2) | 1/1 "
        "| 0/1 · 0/1 · 0/0 | 1.0 s / 1.0 s | 1/1 | pass |"
    ) in section
    assert (
        "| `Other-9B` at `host.docker.internal:8080` | comparison | 50.0% (1/2) | 0/1 "
        "| 0/1 · 0/1 · 0/0 | 1.0 s / 1.0 s | 1/1 | reported, not a criterion |"
    ) in section
    model = section.split(
        "#### Comparison model: `Other-9B` at `host.docker.internal:8080` "
        "(reported, not a criterion)"
    )[1]
    assert "| Citation precision (answerable) | 50.0% (1/2) |" in model
    assert "| Correct-refusal rate (unanswerable) | 0.0% (0/1) |" in model
    assert "| Cited ranges valid (file and lines exist in the checkout) | 100.0% (1/1) |" in model
    assert "| un-1 | unanswerable | - | answered **should refuse** |" in model
    assert "- `code-1` [1] requests: src/requests/models.py" in model  # its wrong citation
    assert "FAIL" not in section  # nothing about a comparison model is a pass or fail

    data = json.loads(sidecar.read_text())
    assert data["version"] == 6
    assert data["summary"]["precision"] == {"num": 2, "den": 2, "value": 1.0}
    assert data["summary"]["cited_ranges"] == {"num": 1, "den": 1, "value": 1.0}
    [other] = data["comparison"]
    assert other["model_requested"] == "Other-9B"
    assert other["status"] == "done" and other["reason"] is None
    assert other["criterion"] is False
    assert other["chat"] == {
        "model": "Other-9B",
        "endpoint_host": "host.docker.internal:8080",
        "preset": "llama_server",
        "overridden": True,
    }
    assert other["summary"]["precision"]["value"] == 0.5
    assert other["summary"]["correct_refusal"]["value"] == 0.0
    assert [q["outcome"] for q in other["questions"]] == ["answered", "answered", "answered"]
    assert data["chat"]["model"] == "small-model"  # the main run's model


def test_exit_code_depends_on_the_main_model_only(tmp_path: Path) -> None:
    # The main model fails precision; a perfect comparison model does not rescue it.
    api = good_api()
    good = {q: str(api.answers[q]) for q in ("Max redirects?", "Redirects for HEAD?")}
    api.answers["Max redirects?"] = answer(("It is 30 [1].", [cit(1, "src2", "stable")]))
    per_model(api, {"Strong-30B": good})
    code, text, _, sidecar = run(api, tmp_path, "--also-model", "Strong-30B")
    assert code == runner.EXIT_THRESHOLD
    assert "| `small-model` at `host.docker.internal:8080` | main | 50.0% (1/2)" in text
    assert "| **FAIL** |" in text.split("### Comparison models")[1].splitlines()[6]
    assert "| `Strong-30B` at `host.docker.internal:8080` | comparison | 100.0% (2/2)" in text
    assert json.loads(sidecar.read_text())["comparison"][0]["summary"]["precision"]["value"] == 1


def test_also_endpoint_and_several_models(tmp_path: Path) -> None:
    api = good_api()
    seen = per_model(api, {})
    code, text, _, _ = run(
        api,
        tmp_path,
        "--also-model",
        "gemma-x",
        "--also-model",
        "phi-y",
        "--also-endpoint",
        "http://10.0.0.7:8081/v1",
    )
    assert code == runner.EXIT_OK, text
    assert seen == ["small-model"] * 3 + ["gemma-x"] * 3 + ["phi-y"] * 3
    switch = {"preset": "custom", "base_url": "http://10.0.0.7:8081/v1"}
    assert api.chat_puts == [switch | {"model": "gemma-x"}, switch | {"model": "phi-y"}, SAVED]
    assert "| `gemma-x` at `10.0.0.7:8081` | comparison |" in text
    assert "| `phi-y` at `10.0.0.7:8081` | comparison |" in text
    assert api.chat_config == api.chat_config | SAVED


def test_comparison_endpoint_defaults_to_the_main_runs_override(tmp_path: Path) -> None:
    api = good_api()
    per_model(api, {})
    endpoint = "http://192.168.1.5:8081/v1"
    code, text, _, _ = run(
        api, tmp_path, "--chat-endpoint", endpoint, "--chat-model", "Main-35B", "--also-model", "B"
    )
    assert code == runner.EXIT_OK, text
    main, other, restore = api.chat_puts
    assert main == {"preset": "custom", "base_url": endpoint, "model": "Main-35B"}
    assert other == {"preset": "custom", "base_url": endpoint, "model": "B"}
    assert restore == SAVED
    assert "| `Main-35B` at `192.168.1.5:8081` | main |" in text
    assert "| `B` at `192.168.1.5:8081` | comparison |" in text


def test_a_failing_comparison_model_is_reported_and_the_next_one_runs(tmp_path: Path) -> None:
    api = good_api()
    seen = per_model(api, {})
    original = api.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/capabilities/chat/test":
            api.chat_test_ok = api.chat_config["model"] != "missing-model"
        return original(request)

    api.handler = handler  # type: ignore[method-assign]
    code, text, log, sidecar = run(
        api, tmp_path, "--also-model", "missing-model", "--also-model", "phi-y"
    )
    assert code == runner.EXIT_OK, text
    assert seen == ["small-model"] * 3 + ["phi-y"] * 3
    assert (
        "| `missing-model` | comparison | - | - | - | - | - | failed: the chat endpoint from "
        "--also-model missing-model failed: Connection refused |"
    ) in text
    assert "#### Comparison model: `missing-model`" not in text
    assert "#### Comparison model: `phi-y`" in text
    assert api.chat_config["model"] == "small-model" and "chat settings restored" in log
    failed, done = json.loads(sidecar.read_text())["comparison"]
    assert failed["status"] == "failed" and failed["summary"] is None
    assert failed["questions"] == []
    assert done["status"] == "done"


def test_ctrl_c_during_the_comparison_keeps_the_main_report(tmp_path: Path) -> None:
    api = good_api()
    original = api.handler
    asked = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages") and api.chat_config["model"] == "Other-9B":
            asked["n"] += 1
            if asked["n"] == 2:
                raise KeyboardInterrupt
        return original(request)

    api.handler = handler  # type: ignore[method-assign]
    code, text, log, sidecar = run(
        api, tmp_path, "--also-model", "Other-9B", "--also-model", "never-run"
    )
    assert code == runner.EXIT_OK, text  # the main run is complete and passed
    assert "| Citation precision (answerable) | 100.0% (2/2) | >= 90% | pass |" in text
    assert "| interrupted: interrupted after 1 of 3 questions |" in text
    assert "| `never-run` | comparison | - | - | - | - | - | skipped: interrupted |" in text
    assert api.chat_config["model"] == "small-model"
    assert "interrupted: writing the report without the rest of the comparison" in log
    other, never = json.loads(sidecar.read_text())["comparison"]
    assert other["status"] == "interrupted" and len(other["questions"]) == 1
    assert other["summary"] is None
    assert never["status"] == "skipped"


def test_ctrl_c_during_the_main_run_restores_and_skips_the_comparison(tmp_path: Path) -> None:
    api = good_api()
    original = api.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            raise KeyboardInterrupt
        return original(request)

    api.handler = handler  # type: ignore[method-assign]
    code, _, log, _ = run(api, tmp_path, "--also-model", "Other-9B")
    assert code == runner.EXIT_INTERRUPTED
    assert api.chat_puts == [SAVED]  # never switched; the saved settings are put back anyway
    assert "chat settings restored" in log


def test_comparison_options_are_checked_before_anything_changes(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run(good_api(), tmp_path, "--also-endpoint", "http://h:1/v1")

    api = good_api()
    code, _, log, _ = run(api, tmp_path, "--also-model", "x", "--also-endpoint", "not a url")
    assert code == runner.EXIT_UNAVAILABLE and "--also-endpoint is not a URL" in log
    assert api.chat_puts == []  # nothing changed on the server
    assert not [p for _, p in api.calls if p.endswith("/messages")]

    api = good_api()
    api.chat_config |= {"base_url": "https://api.openai.com/v1", "api_key_set": True}
    code, _, log, _ = run(api, tmp_path, "--also-model", "x", "--also-endpoint", "http://h:1/v1")
    assert code == runner.EXIT_UNAVAILABLE and "--also-endpoint would lose it" in log
    assert not [p for _, p in api.calls if p.endswith("/messages")]

    code, _, log, _ = run(good_api(), tmp_path, "--also-model", " ")
    assert code == runner.EXIT_UNAVAILABLE and "--also-model must not be empty" in log


def test_without_comparison_models_the_report_has_no_comparison(tmp_path: Path) -> None:
    api = good_api()
    code, text, _, sidecar = run(api, tmp_path)
    assert code == runner.EXIT_OK
    assert "Comparison models" not in text
    data = json.loads(sidecar.read_text())
    assert data["comparison"] == [] and api.chat_puts == []


def test_compare_reads_new_and_older_sidecars(tmp_path: Path) -> None:
    api = good_api()
    per_model(api, WEAK)
    run(api, tmp_path, "--also-model", "Other-9B")
    new = tmp_path / "report.json"
    old = tmp_path / "old.json"
    data = json.loads(new.read_text())
    for key in ("summary", "comparison"):
        del data[key]
    old.write_text(json.dumps(data | {"version": 5}))
    out, log = io.StringIO(), io.StringIO()
    code = runner.main(["--compare", str(old), str(new)], out=out, log=log)
    assert code == runner.EXIT_OK, log.getvalue()
    assert "No outcome differences." in out.getvalue()
