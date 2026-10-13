"""The ``trustbook`` CLI (cli/, D-087..D-089) against the real API: every command through
FastAPI's TestClient, the mock chat model and queued jobs run in-thread while ``--wait`` sleeps."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from trustbook_cli.main import main

from app.capabilities.adapters.mock import UNSUPPORTED_SENTENCE
from app.capabilities.bundled import ProviderRegistry
from app.config import Paths
from app.index.memory import InMemoryIndexRegistry
from app.parse.documents import DocumentParser
from app.services import Services
from tests.chat_helpers import KeywordEmbedder, OverlapReranker
from tests.conftest import AppFactory
from tests.fakes import Router
from tests.ingest_helpers import FakeYoutube, fake_scanner, public_resolver, run_jobs

GUIDE = (
    "# Requests guide\n\n"
    "Requests is an HTTP library for Python. Proxies are configured with the proxies argument. "
    "Sessions keep cookies between requests, and timeouts are given in seconds."
)
ARTICLE = (
    "<html><head><title>Pump manual</title></head><body><article><h1>Pump manual</h1>"
    + "".join(
        f"<p>Step {i}: set pump {i} to {100 + i} rpm and log the reading.</p>" for i in range(8)
    )
    + "</article></body></html>"
)
API_KEY = "tb-test-key-7f3a9c2e5b1d"  # not shaped like a known key: only the CLI keeps it out
STT_KEY = "tb-stt-key-4c8e1a7b9d2f"  # an optional endpoint key (G3b-2)


@dataclass
class Run:
    code: int
    out: str
    err: str

    @property
    def doc(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.out)
        return data


@dataclass
class Cli:
    client: TestClient
    services: Services
    paths: Paths
    runs: list[Run] = field(default_factory=list)

    def __call__(self, *argv: str, stdin: str = "") -> Run:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            list(argv),
            client=self.client,
            stdout=out,
            stderr=err,
            sleep=lambda _s: run_jobs(self.services),
            stdin=io.StringIO(stdin),
        )
        run = Run(code, out.getvalue(), err.getvalue())
        self.runs.append(run)
        return run

    def json(self, *argv: str, stdin: str = "") -> Run:
        run = self(*argv, "--json", stdin=stdin)
        assert run.out.isascii() and run.out.count("\n") == 1
        json.loads(run.out)  # exactly one document, on one line
        return run


@pytest.fixture
def cli(
    make_client: AppFactory,
    fake_providers: ProviderRegistry,
    paths: Paths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Cli:
    for name in ("TRUSTBOOK_URL", "TRUSTBOOK_SPACE", "TRUSTBOOK_BROWSE_ROOT"):
        monkeypatch.delenv(name, raising=False)
    # The redactor reads settings.json from here; empty, so tests see what the CLI itself prints.
    monkeypatch.setenv("TRUSTBOOK_DATA", str(tmp_path / "cli-data"))
    fake_providers.override(embedder=KeywordEmbedder(), reranker=OverlapReranker())
    client = make_client(indexes=InMemoryIndexRegistry())
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    tools = services.ingest.tools
    tools.web.resolver = public_resolver
    tools.web.renderer = None
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    tools.youtube = FakeYoutube({"title": "Unused"})
    assert client.put("/api/capabilities/chat", json={"preset": "mock"}).status_code == 200
    return Cli(client, services, paths)


def _space(cli: Cli, name: str = "Acme") -> dict[str, Any]:
    run = cli.json("spaces", "create", name)
    assert run.code == 0, run.err
    space: dict[str, Any] = run.doc["space"]
    return space


def _guide(cli: Cli) -> dict[str, Any]:
    _space(cli)
    run = cli.json("sources", "add", "paste", "-", "--space", "Acme", "--wait", stdin=GUIDE)
    assert run.code == 0, run.err
    source: dict[str, Any] = run.doc["sources"][0]
    assert source["status"] == "ready"
    return source


def _project(paths: Paths) -> Path:
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "api.py").write_text(
        "def get(url):\n    return request('get', url)\n\n\ndef post(url):\n    return 1\n"
    )
    (root / "README.md").write_text("# Proj\n\nA tiny HTTP library.")
    return root


# --- basics ----------------------------------------------------------------------------------


def test_version_help_and_usage_errors(cli: Cli) -> None:
    run = cli("--version")
    assert run.code == 0 and run.out.startswith("trustbook-cli ")
    run = cli("--help")
    assert run.code == 0 and "Exit codes" in run.out
    # argparse's own exit code 2 would read as "refused": usage errors are 1, as JSON with --json
    run = cli.json("ask")
    assert run.code == 1
    assert run.doc["schema"] == "trustbook.error/1" and run.doc["http_status"] is None
    assert "question" in run.doc["message"]
    run = cli("spaces", "list", "--no-such-flag")
    assert run.code == 1 and "unrecognized arguments" in run.err


# --- spaces ----------------------------------------------------------------------------------


def test_spaces_create_list_delete(cli: Cli) -> None:
    space = _space(cli, "Acme docs")
    assert set(space) == {"id", "slug", "name", "description", "source_count", "created_at"}
    listed = cli.json("spaces", "list")
    assert listed.doc["schema"] == "trustbook.spaces/1"
    assert [s["name"] for s in listed.doc["spaces"]] == ["Acme docs"]
    assert "Acme docs" in cli("spaces", "list").out

    refused = cli.json("spaces", "delete", "acme DOCS")
    assert refused.code == 1 and "--yes" in refused.doc["message"]
    deleted = cli.json("spaces", "delete", "acme DOCS", "--yes")
    assert deleted.code == 0
    assert deleted.doc == {
        "schema": "trustbook.deleted/1",
        "kind": "space",
        "id": space["id"],
        "name": "Acme docs",
    }
    assert cli.json("spaces", "list").doc["spaces"] == []
    missing = cli.json("sources", "list", "--space", "Acme docs")
    assert missing.code == 1 and "No space named" in missing.doc["message"]


# --- sources ---------------------------------------------------------------------------------


def test_paste_wait_list_status_refresh_remove(cli: Cli) -> None:
    source = _guide(cli)
    assert cli.runs[-1].err.count("Requests guide: ") >= 2  # progress on stderr while waiting
    assert source["type"] == "paste" and source["title"] == "Requests guide"

    listed = cli.json("sources", "list", "--space", "Acme")
    assert listed.doc["schema"] == "trustbook.sources/1"
    assert listed.doc["space"]["name"] == "Acme"
    assert [s["id"] for s in listed.doc["sources"]] == [source["id"]]

    status = cli.json("sources", "status", "requests GUIDE", "--space", "Acme")
    assert status.code == 0 and status.doc["schema"] == "trustbook.source/1"
    assert status.doc["source"]["status"] == "ready"

    snapshot = cli.json("sources", "refresh", source["id"], "--space", "Acme")
    assert snapshot.code == 1 and snapshot.doc["http_status"] == 409  # pasted text is fixed

    assert cli.json("sources", "remove", source["id"], "--space", "Acme").code == 1
    removed = cli.json("sources", "remove", source["id"], "--space", "Acme", "--yes")
    assert removed.doc["kind"] == "source" and removed.doc["name"] == "Requests guide"
    assert cli.json("sources", "list", "--space", "Acme").doc["sources"] == []


def test_space_from_environment(cli: Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    _space(cli)
    assert cli.json("sources", "list").code == 1
    monkeypatch.setenv("TRUSTBOOK_SPACE", "Acme")
    assert cli.json("sources", "list").doc["sources"] == []


def test_add_folder_relative_windows_and_absolute(cli: Cli) -> None:
    _space(cli)
    _project(cli.paths)
    run = cli.json("sources", "add", "folder", "work\\proj", "--space", "Acme", "--wait")
    assert run.code == 0, run.err
    assert run.doc["sources"][0]["origin"] == "work/proj"
    assert run.doc["sources"][0]["status"] == "ready"
    refreshed = cli.json("sources", "refresh", "work/proj", "--space", "Acme", "--wait")
    assert refreshed.code == 0, refreshed.err
    assert refreshed.doc["schema"] == "trustbook.source/1"
    assert refreshed.doc["source"]["status"] == "ready"
    assert refreshed.doc["source"]["job_id"] != run.doc["sources"][0]["job_id"]

    # The laptop's path, mapped through the folder that compose mounts at /host
    absolute = cli.json(
        "sources", "add", "folder", "c:\\Users\\Anna\\work\\proj", "--space", "Acme",
        "--browse-root", "C:/Users/anna",
    )  # fmt: skip
    assert absolute.code == 0 and absolute.doc["sources"][0]["origin"] == "work/proj"

    no_root = cli.json("sources", "add", "folder", "C:\\Users\\anna\\work", "--space", "Acme")
    assert no_root.code == 1 and "--browse-root" in no_root.doc["message"]
    outside = cli.json(
        "sources", "add", "folder", "D:\\work", "--space", "Acme", "--browse-root", "C:/Users/anna"
    )
    assert outside.code == 1 and "not inside" in outside.doc["message"]
    missing = cli.json("sources", "add", "folder", "work/nope", "--space", "Acme")
    assert missing.code == 1
    assert missing.doc["http_status"] == 404
    assert "does not exist" in missing.doc["message"]


def test_add_url_and_failed_source_exit_3(cli: Cli, router: Router) -> None:
    _space(cli)
    router.add("GET", "https://example.com/pump", lambda _r: httpx.Response(200, html=ARTICLE))
    router.add("GET", "https://example.com/private", lambda _r: httpx.Response(403))
    ok = cli.json("sources", "add", "url", "example.com/pump", "--space", "Acme", "--wait")
    assert ok.code == 0 and ok.doc["sources"][0]["title"] == "Pump manual"

    bad = cli.json("sources", "add", "url", "https://example.com/private", "--space", "Acme")
    assert bad.code == 0 and bad.doc["sources"][0]["status"] == "queued"  # not waited for
    waited = cli.json(
        "sources", "status", "https://example.com/private", "--space", "Acme", "--wait"
    )
    assert waited.code == 3
    assert "HTTP 403" in waited.doc["source"]["failure_reason"]
    assert "HTTP 403" in cli("sources", "list", "--space", "Acme").out
    assert cli.json("sources", "list", "--space", "Acme").code == 3


def test_add_file_upload(cli: Cli, tmp_path: Path) -> None:
    _space(cli)
    notes = tmp_path / "notes.md"
    notes.write_text(GUIDE)
    run = cli.json("sources", "add", "file", str(notes), "--space", "Acme", "--wait")
    assert run.code == 0, run.err
    assert [(s["type"], s["title"], s["status"]) for s in run.doc["sources"]] == [
        ("upload", "notes.md", "ready")
    ]
    missing = cli.json("sources", "add", "file", str(tmp_path / "nope.md"), "--space", "Acme")
    assert missing.code == 1 and "Cannot read" in missing.doc["message"]


def test_add_search_lists_then_picks(cli: Cli, router: Router) -> None:
    _space(cli)
    results = [
        {"title": f"Result {i}", "url": f"https://example.com/r{i}", "content": f"s{i}"}
        for i in range(3)
    ]
    router.json("GET", "http://searxng:8080/search", {"results": results})
    listed = cli.json("sources", "add", "search", "pumps", "--space", "Acme")
    assert listed.code == 0 and listed.doc["schema"] == "trustbook.search/1"
    assert listed.doc["results"][1] == {
        "n": 2,
        "title": "Result 1",
        "url": "https://example.com/r1",
        "snippet": "s1",
    }
    assert cli.json("sources", "list", "--space", "Acme").doc["sources"] == []

    router.add("GET", "https://example.com/r1", lambda _r: httpx.Response(200, html=ARTICLE))
    picked = cli.json(
        "sources", "add", "search", "pumps", "--space", "Acme", "--pick", "2", "--wait"
    )
    assert picked.code == 0, picked.err
    assert [(s["type"], s["title"], s["status"]) for s in picked.doc["sources"]] == [
        ("search", "Result 1", "ready")
    ]
    assert cli.json("sources", "add", "search", "pumps", "--space", "Acme", "--pick", "9").code == 1


# --- ask -------------------------------------------------------------------------------------


def test_ask_verified_with_cited_text(cli: Cli) -> None:
    source = _guide(cli)
    run = cli.json("ask", "How are proxies configured?", "--space", "Acme")
    assert run.code == 0, run.err
    doc = run.doc
    assert doc["schema"] == "trustbook.ask/1"
    assert doc["verdict"] == "verified"
    assert doc["refusal"] is None and doc["removed_statements"] == []
    assert doc["sentences"] == [
        {"text": "Proxies are configured with the proxies argument.", "citations": [1]}
    ]
    assert doc["answer"] == "Proxies are configured with the proxies argument. [1]"
    [citation] = doc["citations"]
    assert citation["n"] == 1 and citation["source"] == "Requests guide"
    assert citation["source_id"] == source["id"] and citation["origin"] is None
    assert citation["heading"] == "Requests guide"
    assert (citation["file"], citation["lines"], citation["pages"]) == (None, None, None)
    assert "Proxies are configured with the proxies argument." in citation["text"]
    assert set(citation) == {
        "n", "source", "source_id", "origin", "chunk_id", "file", "lines", "pages", "start_s",
        "end_s", "speaker", "heading", "symbol", "text",
    }  # fmt: skip
    assert isinstance(doc["latency_s"], float)
    # The conversation is a normal one: visible in the UI with the answer
    space_id = doc["space"]["id"]
    conversations = cli.client.get(f"/api/spaces/{space_id}/conversations").json()
    assert [c["id"] for c in conversations] == [doc["conversation_id"]]
    assert conversations[0]["title"] == "CLI: How are proxies configured?"
    messages = cli.client.get(
        f"/api/spaces/{space_id}/conversations/{doc['conversation_id']}/messages"
    ).json()
    assert messages[-1]["id"] == doc["message_id"] and messages[-1]["badge"] == "verified"

    text = cli("ask", "-", "--space", "Acme", stdin="How are proxies configured?\n")
    assert text.code == 0
    assert text.out.startswith("Verified answer:")
    assert "[1] Requests guide" in text.out and "| " in text.out


def test_ask_code_citation_has_file_lines_and_text(cli: Cli) -> None:
    _space(cli)
    _project(cli.paths)
    assert cli.json("sources", "add", "folder", "work/proj", "--space", "Acme", "--wait").code == 0
    run = cli.json("ask", "What does get return? request get url", "--space", "Acme")
    assert run.code == 0, run.err
    cited = [c for c in run.doc["citations"] if c["file"] == "src/api.py"]
    assert cited, run.doc
    assert cited[0]["lines"] == [1, 2] and cited[0]["symbol"] == "get"
    assert cited[0]["origin"] == "work/proj"
    assert cited[0]["text"] == "def get(url):\n    return request('get', url)"


def test_ask_refused_exits_2(cli: Cli) -> None:
    _guide(cli)
    run = cli.json("ask", "What is the capital of Mongolia?", "--space", "Acme")
    assert run.code == 2
    doc = run.doc
    assert doc["verdict"] == "refused"
    assert doc["refusal"]["reason"] == "not_in_sources" and doc["refusal"]["message"]
    assert (doc["answer"], doc["sentences"], doc["citations"]) == ("", [], [])
    assert doc["removed_statements"] == []
    text = cli("ask", "What is the capital of Mongolia?", "--space", "Acme")
    assert text.code == 2 and text.out.startswith("Not answered (not_in_sources):")


def test_ask_partially_verified_lists_removed_statements(cli: Cli) -> None:
    _guide(cli)
    run = cli.json("ask", "How are proxies configured? TB-UNSUPPORTED", "--space", "Acme")
    assert run.code == 0, run.err
    assert run.doc["verdict"] == "partially_verified"
    removed = run.doc["removed_statements"]
    assert [r["text"] for r in removed] == [UNSUPPORTED_SENTENCE]
    assert removed[0]["reason"]
    assert UNSUPPORTED_SENTENCE not in run.doc["answer"]
    assert all(s["text"] != UNSUPPORTED_SENTENCE for s in run.doc["sentences"])


def test_ask_errors(cli: Cli, make_client: AppFactory) -> None:
    _space(cli)
    empty = cli.json("ask", "Anything?", "--space", "Acme")
    assert empty.code == 2 and empty.doc["refusal"]["reason"] == "no_sources"
    assert cli.json("ask", "   ", "--space", "Acme").code == 1
    # Chat not set up: the API's 409 becomes an error document with its status
    assert cli.client.put("/api/capabilities/chat", json={"preset": "openai"}).status_code == 200
    run = cli.json("ask", "Anything?", "--space", "Acme")
    assert run.code == 1 and run.doc["http_status"] == 409


# --- queue, gate log, eval -------------------------------------------------------------------


def test_queue_and_gate_log(cli: Cli) -> None:
    _space(cli)
    cli.json("sources", "add", "paste", GUIDE, "--space", "Acme", "--title", "Guide")
    queue = cli.json("queue")
    assert queue.doc["schema"] == "trustbook.jobs/1"
    assert [(j["status"], j["space_name"]) for j in queue.doc["jobs"]] == [("queued", "Acme")]
    assert queue.doc["summary"]["queued"] == 1
    assert "queued" in cli("queue").out

    log = cli.json("gate-log", "--space", "Acme")
    assert log.code == 0 and log.doc["schema"] == "trustbook.gate_log/1"
    assert log.doc["entries"] == []
    assert cli("gate-log", "--space", "Acme").out.strip() == "No entries."


def test_eval_runs_the_runner_with_the_base_url(
    cli: Cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys\nprint(' '.join(sys.argv[1:]))\nprint('progress', file=sys.stderr)\n"
        "sys.exit(1)\n"
    )
    monkeypatch.setenv("TRUSTBOOK_EVAL_RUNNER", str(runner))
    run = cli("eval", "--space", "Smoke", "--questions", "q.yaml", "--help")
    assert run.code == 4  # the runner's 1 (a target missed), mapped to the CLI's codes
    assert run.out.strip() == (
        "--base-url http://127.0.0.1:8484 --space Smoke --questions q.yaml --help"
    )
    assert run.err.strip() == "progress"


# --- secrets ---------------------------------------------------------------------------------


def test_api_key_from_settings_never_printed(cli: Cli, router: Router) -> None:
    body = {"provider": "brave", "api_key": API_KEY}
    assert cli.client.put("/api/capabilities/web_search", json=body).status_code == 200
    assert API_KEY in cli.paths.settings_file.read_text()  # the key is really saved
    # G3b-2: the optional key of an OpenAI-compatible speech-to-text endpoint, saved the same way.
    stt = {"mode": "endpoint", "base_url": "http://stt.example", "model": "w", "api_key": STT_KEY}
    assert cli.client.put("/api/capabilities/stt", json=stt).status_code == 200
    assert STT_KEY in cli.paths.settings_file.read_text()

    _guide(cli)
    commands: list[tuple[str, ...]] = [
        ("spaces", "list"),
        ("sources", "list", "--space", "Acme"),
        ("sources", "status", "Requests guide", "--space", "Acme"),
        ("sources", "add", "search", "pumps", "--space", "Acme"),
        ("ask", "How are proxies configured?", "--space", "Acme"),
        ("ask", "What is the capital of Mongolia?", "--space", "Acme"),
        ("queue",),
        ("gate-log", "--space", "Acme"),
        ("sources", "add", "folder", "nope", "--space", "Acme"),
    ]
    for argv in commands:
        cli(*argv)
        cli.json(*argv)
    assert len(cli.runs) > 2 * len(commands)
    for run in cli.runs:
        assert API_KEY not in run.out and API_KEY not in run.err
        assert API_KEY[-8:] not in run.out + run.err
        assert STT_KEY not in run.out + run.err and STT_KEY[-8:] not in run.out + run.err


def test_rejected_key_reports_http_status_401(cli: Cli, router: Router) -> None:
    # A wrong key: the chat endpoint answers 401 while the app streams the answer.
    llm = "http://llm.example:8080/v1"
    router.add("POST", llm, lambda _r: httpx.Response(401, json={"error": "invalid key"}))
    _guide(cli)
    body = {"preset": "custom", "base_url": llm, "model": "m", "api_key": API_KEY}
    assert cli.client.put("/api/capabilities/chat", json=body).status_code == 200
    run = cli.json("ask", "How are proxies configured?", "--space", "Acme")
    assert run.code == 1
    assert run.doc["schema"] == "trustbook.error/1"
    assert run.doc["http_status"] == 401
    assert "The API key was rejected (401)." in run.doc["message"]
    assert router.requests[-1].headers["authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in run.out + run.err


def test_redactor_masks_a_saved_key_in_any_output(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The safety net: had the API echoed the key, the CLI would still not print it.
    body = {"provider": "brave", "api_key": API_KEY}
    assert cli.client.put("/api/capabilities/web_search", json=body).status_code == 200
    monkeypatch.setenv("TRUSTBOOK_DATA", str(cli.paths.data))
    _space(cli)
    url = f"https://example.com/leak/{API_KEY}"  # echoed back as the source's origin
    run = cli.json("sources", "add", "url", url, "--space", "Acme")
    assert run.code == 0
    assert API_KEY not in run.out and "https://example.com/leak/[redacted]" in run.out
