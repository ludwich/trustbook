"""Commands against a fake API (httpx.MockTransport): exit codes, the one-document JSON
contract and edge cases of the answer stream that the backend tests can't easily produce."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from trustbook_cli import eval_cmd
from trustbook_cli.client import TrustbookClient, TrustbookError
from trustbook_cli.main import main
from trustbook_cli.wait import wait_for_sources

SPACE = {"id": "s1", "slug": "acme", "name": "Acme", "description": "", "source_count": 1}
SOURCE = {"id": "src1", "title": "Pump", "origin": "work/pump", "type": "folder", "status": "ready"}
CODE_CONTEXT = {
    "chunk": {"id": "k1", "text": "File: pump.py\n..."},
    "source_type": "folder",
    "view": "code",
    "code": {
        "file": "pump.py",
        "commit": None,
        "language": "python",
        "first_line": 8,
        "lines": ["# before", "def größe():", "    return 1", "# after"],
        "highlight": [9, 10],
    },
}
TEXT_CONTEXT = {
    "chunk": {"id": "k2", "text": "chunk"},
    "source_type": "youtube",
    "view": "text",
    "text": {"label": None, "before": "a", "highlight": "We start the pump.", "after": "b"},
}


def sse(*events: tuple[str, Any]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def cit(n: int, chunk: str, **locator: Any) -> dict[str, Any]:
    return {
        "n": n,
        "chunk_id": chunk,
        "source_id": "src1",
        "source_title": "Pump",
        "locator": locator,
    }


class FakeApi:
    def __init__(self, stream: str = "") -> None:
        self.stream = stream
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append((method, path))
        if path == "/api/spaces":
            return httpx.Response(200, json=[SPACE])
        if path == "/api/spaces/s1/conversations":
            return httpx.Response(201, json={"id": "c1"})
        if path == "/api/spaces/s1/conversations/c1/messages":
            return httpx.Response(
                200, text=self.stream, headers={"content-type": "text/event-stream"}
            )
        if path == "/api/spaces/s1/sources":
            return httpx.Response(200, json=[SOURCE])
        if path == "/api/spaces/s1/chunks/k1/context":
            return httpx.Response(200, json=CODE_CONTEXT)
        if path == "/api/spaces/s1/chunks/k2/context":
            return httpx.Response(200, json=TEXT_CONTEXT)
        return httpx.Response(404, json={"detail": "Not found."})


def run(api: Callable[[httpx.Request], httpx.Response], *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    client = httpx.Client(base_url="http://tb.test", transport=httpx.MockTransport(api))
    code = main(list(argv), client=client, stdout=out, stderr=err, sleep=lambda _s: None)
    return code, out.getvalue(), err.getvalue()


def done(content: dict[str, Any], badge: str | None, **extra: Any) -> tuple[str, Any]:
    message = {"id": "m1", "content": content, "badge": badge, "removed_sentences": [], **extra}
    return ("done", {"message": message})


def test_answer_with_code_and_transcript_citations() -> None:
    code_cit = cit(1, "k1", file="pump.py", start_line=9, end_line=10, symbol="größe")
    talk_cit = cit(2, "k2", start_s=65.0, end_s=80.5, speaker="Anna")
    sentences = [
        {"text": "größe returns 1.", "citations": [code_cit]},
        {"text": "The pump starts.", "citations": [talk_cit, cit(1, "k1")]},
    ]
    api = FakeApi(
        sse(("status", {"stage": "retrieving"}), done({"sentences": sentences}, "verified"))
    )
    code, out, err = run(api, "--json", "ask", "Wie groß?", "--space", "acme")
    assert code == 0, err
    assert out.isascii() and out.count("\n") == 1 and out.endswith("}\n")
    doc = json.loads(out)
    assert doc["answer"] == "größe returns 1. [1] The pump starts. [2][1]"
    assert doc["sentences"][1]["citations"] == [2, 1]
    first, second = doc["citations"]
    assert (first["n"], first["file"], first["lines"], first["symbol"]) == (
        1,
        "pump.py",
        [9, 10],
        "größe",
    )
    assert first["text"] == "def größe():\n    return 1"
    assert first["origin"] == "work/pump"
    assert (second["start_s"], second["end_s"], second["speaker"]) == (65.0, 80.5, "Anna")
    assert second["text"] == "We start the pump."

    code, out, _err = run(api, "ask", "Wie groß?", "--space", "acme")
    assert "[1] Pump - pump.py lines 9-10 (größe)" in out
    assert "[2] Pump - 1:05-1:20 (Anna)" in out


def test_chitchat_reply_is_a_refusal() -> None:
    api = FakeApi(sse(done({"text": "Hi! Ask me about your sources."}, None)))
    code, out, _err = run(api, "ask", "hello", "--space", "Acme", "--json")
    doc = json.loads(out)
    assert code == 2
    assert doc["verdict"] == "refused"
    assert doc["refusal"] == {
        "reason": "chitchat",
        "message": "Hi! Ask me about your sources.",
        "suggestion": None,
    }


def test_refused_event_and_suggestion() -> None:
    refusal = {
        "reason": "not_in_sources",
        "message": "Not in your sources.",
        "suggestion": "Add one.",
    }
    api = FakeApi(sse(("refused", refusal), done({"text": "Not in your sources."}, "refused")))
    code, out, _err = run(api, "ask", "q", "--space", "Acme", "--json")
    assert code == 2 and json.loads(out)["refusal"] == refusal
    assert ("GET", "/api/spaces/s1/sources") not in api.requests  # nothing cited, nothing read
    code, out, _err = run(api, "ask", "q", "--space", "Acme")
    assert out == "Not answered (not_in_sources): Not in your sources.\nAdd one.\n"


@pytest.mark.parametrize(
    ("stream", "message"),
    [
        (
            sse(("error", {"message": "The chat model did not answer."})),
            "The chat model did not answer.",
        ),
        (sse(("status", {"stage": "retrieving"})), "ended without a result"),
        ("event: done\ndata: {broken\n\n", "bad done event"),
    ],
)
def test_broken_streams_are_errors(stream: str, message: str) -> None:
    code, out, err = run(FakeApi(stream), "ask", "q", "--space", "Acme", "--json")
    doc = json.loads(out)
    assert code == 1 and doc["schema"] == "trustbook.error/1" and message in doc["message"]
    assert message in err


@pytest.mark.parametrize(
    ("stream", "status"),
    [
        # The model endpoint rejected the key: the app's error event carries its status.
        (sse(("error", {"message": "The API key was rejected (401).", "http_status": 401})), 401),
        # No endpoint status (e.g. it could not be reached): the API's own response status.
        (sse(("error", {"message": "Cannot reach llm.local."})), 200),
        (sse(("status", {"stage": "retrieving"})), 200),
        ("event: done\ndata: {broken\n\n", 200),
    ],
)
def test_stream_errors_report_the_http_status(stream: str, status: int) -> None:
    code, out, _err = run(FakeApi(stream), "ask", "q", "--space", "Acme", "--json")
    doc = json.loads(out)
    assert code == 1 and doc["schema"] == "trustbook.error/1"
    assert doc["http_status"] == status


def test_unusable_bodies_report_the_http_status() -> None:
    def not_json(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy</html>")

    code, out, _err = run(not_json, "spaces", "list", "--json")
    assert code == 1 and json.loads(out)["http_status"] == 200

    def wrong_shape(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"spaces": []})

    code, out, _err = run(wrong_shape, "spaces", "list", "--json")
    assert code == 1 and json.loads(out)["http_status"] == 200


def test_unreachable_api() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    code, out, err = run(refuse, "spaces", "list", "--json")
    assert code == 1
    assert json.loads(out) == {
        "schema": "trustbook.error/1",
        "message": "list spaces: ConnectError: connection refused",
        "http_status": None,
    }
    assert err.startswith("trustbook: error: ")


@pytest.mark.parametrize("json_mode", [True, False])
def test_bad_base_url_is_an_error_document_not_a_traceback(json_mode: bool) -> None:
    out, err = io.StringIO(), io.StringIO()
    argv = ["--base-url", "http://[::1", "spaces", "list"] + (["--json"] if json_mode else [])
    code = main(argv, stdout=out, stderr=err)
    assert code == 1
    assert "Traceback" not in err.getvalue()
    assert err.getvalue().startswith("trustbook: error: InvalidURL: ")
    if json_mode:
        doc = json.loads(out.getvalue())
        assert doc["schema"] == "trustbook.error/1" and doc["message"].startswith("InvalidURL: ")
    else:
        assert out.getvalue() == ""


def test_unexpected_errors_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any) -> int:
        raise RuntimeError("token sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 leaked")

    monkeypatch.setattr("trustbook_cli.commands.spaces_list", boom)
    for extra in (["--json"], []):
        code, out, err = run(FakeApi(), "spaces", "list", *extra)
        assert code == 1 and "abcdefghijklmnop" not in out + err and "Traceback" not in err
        assert "RuntimeError: token" in err


def test_add_url_failing_part_way_lists_what_was_created() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/spaces":
            return httpx.Response(200, json=[SPACE])
        url = json.loads(request.content)["url"]
        if url == "https://bad.test":
            return httpx.Response(400, json={"detail": "Not a web address."})
        return httpx.Response(201, json={**SOURCE, "id": "u1", "title": url, "status": "queued"})

    argv = ("sources", "add", "url", "https://a.test", "https://bad.test", "https://c.test")
    code, out, err = run(api, *argv, "--space", "Acme", "--json")
    doc = json.loads(out)
    assert code == 1 and doc["schema"] == "trustbook.error/1" and doc["http_status"] == 400
    assert [(s["id"], s["title"], s["status"]) for s in doc["created"]] == [
        ("u1", "https://a.test", "queued")
    ]
    assert "already added before the error: https://a.test (u1)" in err
    code, out, _err = run(
        api, "sources", "add", "url", "https://bad.test", "--space", "Acme", "--json"
    )
    assert code == 1 and "created" not in json.loads(out)


def test_text_output_survives_a_narrow_console_encoding() -> None:
    sentences = [
        {"text": "größe returns 1 → ok.", "citations": [cit(1, "k1", file="pump.py", start_line=9)]}
    ]
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii")
    client = httpx.Client(
        base_url="http://tb.test",
        transport=httpx.MockTransport(FakeApi(sse(done({"sentences": sentences}, "verified")))),
    )
    code = main(["ask", "q", "--space", "Acme"], client=client, stdout=out, stderr=io.StringIO())
    out.flush()
    assert code == 0
    assert "gr??e returns 1 ? ok. [1]" in raw.getvalue().decode("ascii")


def test_wait_reports_progress_and_times_out() -> None:
    states = iter([{"id": "a", "title": "Doc", "status": "reading", "progress": 0.5}] * 10)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(states))

    client = TrustbookClient(
        client=httpx.Client(base_url="http://tb.test", transport=httpx.MockTransport(handler))
    )
    notes: list[str] = []
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(TrustbookError, match="still processing after 5 s"):
        wait_for_sources(
            client, "s1", [{"id": "a", "title": "Doc", "status": "queued"}],
            note=notes.append, sleep=sleep, clock=lambda: now[0], timeout_s=5, poll_s=2,
        )  # fmt: skip
    assert notes == ["[0s] Doc: queued", "[2s] Doc: reading 50%"]


def test_wait_says_why_there_is_no_progress_yet() -> None:
    doc = {"id": "a", "title": "Doc"}
    states = iter(
        [
            {**doc, "status": "reading", "progress": 0.0, "stage": "waiting_for_calibration"},
            {**doc, "status": "indexing", "progress": 0.4, "stage": "loading_models"},
            {**doc, "status": "indexing", "progress": 0.7, "stage": None},
            {**doc, "status": "ready", "progress": None, "stage": None},
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(states))

    client = TrustbookClient(
        client=httpx.Client(base_url="http://tb.test", transport=httpx.MockTransport(handler))
    )
    notes: list[str] = []
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    wait_for_sources(
        client, "s1", [{**doc, "status": "queued"}],
        note=notes.append, sleep=sleep, clock=lambda: now[0], poll_s=2,
    )  # fmt: skip
    assert notes == [
        "[0s] Doc: queued",
        "[2s] Doc: waiting for calibration (reading 0%)",
        "[4s] Doc: loading models (indexing 40%)",
        "[6s] Doc: indexing 70%",
        "[8s] Doc: ready",
    ]


def test_sources_list_and_queue_show_the_stage() -> None:
    loading = {**SOURCE, "status": "indexing", "progress": 0.4, "stage": "loading_models"}
    job = {
        "id": "j1", "kind": "ingest", "title": "Pump", "space_id": "s1", "space_name": "Acme",
        "status": "running", "progress": 0.0, "position": 1, "stage": "waiting_for_calibration",
    }  # fmt: skip

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/spaces":
            return httpx.Response(200, json=[SPACE])
        if request.url.path == "/api/jobs":
            return httpx.Response(200, json={"jobs": [job], "summary": {}})
        return httpx.Response(200, json=[loading])

    code, out, _err = run(api, "sources", "list", "--space", "Acme")
    assert code == 0 and "loading models (indexing 40%)" in out
    code, out, _err = run(api, "sources", "list", "--space", "Acme", "--json")
    assert json.loads(out)["sources"][0]["stage"] == "loading_models"
    code, out, _err = run(api, "queue")
    assert code == 0 and "0% waiting for calibration" in out
    code, out, _err = run(api, "queue", "--json")
    assert json.loads(out)["jobs"][0]["stage"] == "waiting_for_calibration"


def test_eval_runner_lookup(tmp_path: Path) -> None:
    runner = tmp_path / "eval" / "runner.py"
    runner.parent.mkdir()
    runner.write_text("")
    assert eval_cmd.find_runner({"TRUSTBOOK_EVAL_RUNNER": str(runner)}) == runner
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert eval_cmd.find_runner({}, cwd=nested) == runner
    with pytest.raises(TrustbookError, match="is not a file"):
        eval_cmd.find_runner({"TRUSTBOOK_EVAL_RUNNER": str(tmp_path / "nope.py")})
    command = eval_cmd.runner_command(runner, ["--base-url=http://x", "--space", "S"], "http://y")
    assert command[1:] == [str(runner), "--base-url=http://x", "--space", "S"]


def test_eval_without_pyyaml_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eval_cmd, "EVAL_MODULES", {"trustbook_no_such_module": "PyYAML"})
    code, out, err = run(FakeApi(), "eval", "--space", "S", "--questions", "q.yaml", "--json")
    doc = json.loads(out)
    assert code == 1 and doc["schema"] == "trustbook.error/1"
    assert 'pip install "trustbook-cli[eval]"' in doc["message"]
    assert "PyYAML" in err and "Traceback" not in err
    eval_cmd.check_eval_dependencies(lambda _name: object())  # installed: no error


@pytest.mark.parametrize(
    ("runner_code", "cli_code"),
    [(0, 0), (1, 4), (2, 1), (130, 130), (-2, 130), (3, 1), (-9, 1)],
)
def test_eval_exit_codes_follow_the_cli_contract(runner_code: int, cli_code: int) -> None:
    assert eval_cmd.cli_exit_code(runner_code) == cli_code


def test_eval_maps_the_runners_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text("import sys\nprint('report')\nsys.exit(int(sys.argv[-1]))\n")
    monkeypatch.setenv("TRUSTBOOK_EVAL_RUNNER", str(runner))
    monkeypatch.setattr(eval_cmd, "EVAL_MODULES", {"json": "json"})  # PyYAML may be absent here
    assert run(FakeApi(), "eval", "--questions", "q.yaml", "1")[:2] == (4, "report\n")
    assert run(FakeApi(), "eval", "--questions", "q.yaml", "2")[0] == 1  # not "refused"


def test_version() -> None:
    code, out, _err = run(FakeApi(), "--version")
    assert code == 0 and out.startswith("trustbook-cli ")
