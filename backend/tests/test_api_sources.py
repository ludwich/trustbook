from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.capabilities.bundled import ProviderRegistry
from app.config import Paths
from app.db.engine import get_engine
from app.db.models import Chunk
from app.ingest import errors
from app.jobs.handlers import JobCancelledError, JobContext
from app.parse.documents import DocumentParser
from app.security.gitleaks import Finding, ScannerMissingError
from app.spaces.service import SpaceNotFoundError
from tests.conftest import AppFactory
from tests.fakes import Router
from tests.ingest_helpers import (
    FakeStt,
    FakeYoutube,
    fake_scanner,
    public_resolver,
    run_jobs,
    services_of,
    speech_wav,
    text_pdf,
)

ARTICLE = (
    "<html><head><title>Pump manual</title></head><body><article><h1>Pump manual</h1>"
    + "".join(
        f"<p>Step {i}: set pump {i} to {100 + i} rpm and log the pressure reading.</p>"
        for i in range(8)
    )
    + "</article></body></html>"
)
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.web.resolver = public_resolver
    tools.web.renderer = None
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    tools.youtube = FakeYoutube({"title": "Unused"})
    return client


@pytest.fixture
def space(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/spaces", json={"name": "Acme"})
    assert response.status_code == 201
    data: dict[str, Any] = response.json()
    return data


def _sources(client: TestClient, sid: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = client.get(f"/api/spaces/{sid}/sources").json()
    return data


def _one(client: TestClient, sid: str, source_id: str) -> dict[str, Any]:
    return next(s for s in _sources(client, sid) if s["id"] == source_id)


def _paste(client: TestClient, sid: str, text: str, title: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    if title:
        body["title"] = title
    response = client.post(f"/api/spaces/{sid}/sources/paste", json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


LONG_TEXT = "# Audit notes\n\n" + " ".join(
    f"Finding {i} concerns supplier {i} and the contract review." for i in range(80)
)


# --- paste, snapshot, chunks, hooks ----------------------------------------------------------


def test_paste_source_is_indexed_end_to_end(client: TestClient, space: dict[str, Any]) -> None:
    services = services_of(client)
    ready: list[tuple[str, str]] = []
    services.on_source_ready.append(lambda sid, src: ready.append((sid, src)))
    sid = space["id"]

    created = _paste(client, sid, LONG_TEXT)
    assert created["type"] == "paste"
    assert created["title"] == "Audit notes"
    assert created["status"] == "queued"
    assert created["group"] == "documents"
    assert created["job_id"]
    assert created["snapshot_at"]
    assert set(created) == {
        "id", "type", "title", "origin", "group", "status", "progress", "failure_reason",
        "failure_next_step", "quality_warning", "enabled", "snapshot_at", "commit_hash",
        "stats", "created_at", "job_id", "ahead",
    }  # fmt: skip
    assert created["ahead"] is None  # only folder sources whose HEAD moved

    assert run_jobs(services) == 1
    source = _one(client, sid, created["id"])
    assert source["status"] == "ready", source
    assert source["stats"]["chunks"] >= 2
    assert source["stats"]["chars"] == len(LONG_TEXT)
    assert source["quality_warning"] is None
    assert ready == [(sid, created["id"])]
    assert client.get(f"/api/spaces/{sid}").json()["source_count"] == 1

    snap = client.get(f"/api/spaces/{sid}/sources/{created['id']}/snapshot").json()
    assert snap["markdown"] == LONG_TEXT
    assert snap["map"]["kind"] == "sections"

    record = services.spaces.get(sid)
    with Session(get_engine(record.db_path)) as session:
        chunks = session.exec(select(Chunk).order_by(Chunk.ordinal)).all()
    assert len(chunks) == source["stats"]["chunks"]
    # Its first line names the section (D-105); the body is the snapshot's text.
    body = LONG_TEXT[chunks[0].start_offset : chunks[0].end_offset]
    assert chunks[0].text == f"Section: Audit notes\n{body}"
    assert json.loads(chunks[0].locator) == {"heading": "Audit notes", "offset": 0}

    chunk = client.get(f"/api/spaces/{sid}/chunks/{chunks[1].id}").json()
    assert chunk["source_title"] == "Audit notes"
    assert chunk["locator"]["heading"] == "Audit notes"
    assert client.get(f"/api/spaces/{sid}/chunks/nope").status_code == 404

    index = services.indexes.for_space(sid)
    assert index.count() == len(chunks)
    hits = index.text_search("supplier contract", limit=3, source_ids=[created["id"]])
    assert hits and hits[0].source_id == created["id"]
    assert (record.folder / "vectors").is_dir()


def test_paste_validation_and_short_text_warning(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    assert (
        client.post(f"/api/spaces/{sid}/sources/paste", json={"text": "  \n "}).status_code == 422
    )
    assert client.post("/api/spaces/missing/sources/paste", json={"text": "x"}).status_code == 404
    created = _paste(client, sid, "A short note.", title="Note")
    run_jobs(services_of(client))
    source = _one(client, sid, created["id"])
    assert source["status"] == "ready"
    assert source["title"] == "Note"
    assert source["quality_warning"] == errors.WARN_SHORT


# --- uploads ---------------------------------------------------------------------------------


def test_uploads_of_several_types(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    files = [
        ("files", ("notes.md", b"# Notes\n\nPlain markdown text.", "text/markdown")),
        ("files", ("report.pdf", text_pdf(["Quarterly revenue grew"]), "application/pdf")),
        ("files", ("page.html", ARTICLE.encode(), "text/html")),
        ("files", ("tool.exe", b"MZ\x00\x00", "application/octet-stream")),
    ]
    response = client.post(f"/api/spaces/{sid}/sources/upload", files=files)
    assert response.status_code == 201, response.text
    created = response.json()
    assert [s["title"] for s in created] == ["notes.md", "report.pdf", "page.html", "tool.exe"]
    assert [s["status"] for s in created] == ["queued", "queued", "queued", "failed"]
    assert created[3]["failure_reason"] == errors.UNSUPPORTED_FILE[0]
    assert created[3]["failure_next_step"] == errors.UNSUPPORTED_FILE[1]

    run_jobs(services_of(client))
    by_title = {s["title"]: s for s in _sources(client, sid)}
    assert by_title["notes.md"]["status"] == "ready"
    pdf = by_title["report.pdf"]
    assert pdf["status"] == "ready"
    assert pdf["stats"]["pages"] == 1
    assert errors.WARN_BASIC_PDF in pdf["quality_warning"]
    assert by_title["Pump manual"]["status"] == "ready"  # html: title from the page

    original = client.get(f"/api/spaces/{sid}/sources/{pdf['id']}/original")
    assert original.status_code == 200
    assert original.headers["content-type"] == "application/pdf"
    assert original.headers["x-content-type-options"] == "nosniff"
    assert "inline" in original.headers["content-disposition"]
    html_id = by_title["Pump manual"]["id"]
    html = client.get(f"/api/spaces/{sid}/sources/{html_id}/original")
    assert html.headers["content-type"] == "application/octet-stream"
    assert "attachment" in html.headers["content-disposition"]
    paste = _paste(client, sid, "Text")
    assert client.get(f"/api/spaces/{sid}/sources/{paste['id']}/original").status_code == 404

    snap = client.get(f"/api/spaces/{sid}/sources/{pdf['id']}/snapshot").json()
    assert snap["map"]["kind"] == "pages"
    assert snap["map"]["segments"][0]["page"] == 1


def test_upload_too_large(
    client: TestClient, space: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.sources as sources_api

    monkeypatch.setattr(sources_api, "MAX_UPLOAD_BYTES", 10)
    response = client.post(
        f"/api/spaces/{space['id']}/sources/upload",
        files=[("files", ("big.txt", b"x" * 100, "text/plain"))],
    )
    source = response.json()[0]
    assert source["status"] == "failed"
    assert "larger than" in source["failure_reason"]
    folder = services_of(client).spaces.get(space["id"]).folder / "sources" / source["id"]
    assert not list(folder.glob("original.*"))


def test_audio_upload_needs_stt(client: TestClient, space: dict[str, Any], tmp_path: Path) -> None:
    sid = space["id"]
    wav = speech_wav(tmp_path / "talk.wav").read_bytes()
    created = client.post(
        f"/api/spaces/{sid}/sources/upload", files=[("files", ("talk.wav", wav, "audio/wav"))]
    ).json()[0]
    assert created["group"] == "transcripts"
    run_jobs(services_of(client))
    failed = _one(client, sid, created["id"])
    assert (failed["status"], failed["failure_reason"]) == ("failed", errors.NO_STT[0])

    services_of(client).ingest.tools.stt = lambda: FakeStt("We agreed on the budget.")
    second = client.post(
        f"/api/spaces/{sid}/sources/upload", files=[("files", ("talk.wav", wav, "audio/wav"))]
    ).json()[0]
    run_jobs(services_of(client))
    ready = _one(client, sid, second["id"])
    assert ready["status"] == "ready"
    assert ready["stats"]["duration_s"] == 4.0
    assert FakeStt.warning in ready["quality_warning"]
    chunk_id = _chunk_ids(client, sid, second["id"])[0]
    chunk = client.get(f"/api/spaces/{sid}/chunks/{chunk_id}").json()
    assert chunk["locator"]["start_s"] < 0.1
    assert chunk["locator"]["end_s"] > 2.5


def test_bundled_whistle_is_used_for_stt(
    client: TestClient, space: dict[str, Any], tmp_path: Path, paths: Paths
) -> None:
    from app.ingest.stt import resolve_stt
    from tests.ingest_helpers import TextTranscriber

    services = services_of(client)
    services.providers.override(transcriber=TextTranscriber("Hello world."))
    stt = resolve_stt(services.capabilities, services.providers, services.http)
    assert stt is not None
    assert stt.warning == errors.WARN_WHISTLE
    assert stt.transcribe_wav(speech_wav(tmp_path / "a.wav")) == "Hello world."


def _chunk_ids(client: TestClient, sid: str, source_id: str) -> list[str]:
    record = services_of(client).spaces.get(sid)
    with Session(get_engine(record.db_path)) as session:
        rows = session.exec(
            select(Chunk).where(Chunk.source_id == source_id).order_by(Chunk.ordinal)
        ).all()
    return [r.id for r in rows]


# --- URLs ------------------------------------------------------------------------------------


def test_url_source_fetches_page(client: TestClient, space: dict[str, Any], router: Router) -> None:
    sid = space["id"]
    router.add("GET", "https://example.com/pump", lambda _r: httpx.Response(200, html=ARTICLE))
    router.add("GET", "https://example.com/private", lambda _r: httpx.Response(403))
    created = client.post(f"/api/spaces/{sid}/sources/url", json={"url": "example.com/pump"}).json()
    assert (created["type"], created["group"]) == ("url", "web")
    assert created["origin"] == "https://example.com/pump"
    assert created["title"] == "https://example.com/pump"
    blocked = client.post(
        f"/api/spaces/{sid}/sources/url", json={"url": "https://example.com/private"}
    ).json()
    run_jobs(services_of(client))

    page = _one(client, sid, created["id"])
    assert page["status"] == "ready", page
    assert page["title"] == "Pump manual"
    assert page["snapshot_at"]
    chunk = client.get(
        f"/api/spaces/{sid}/chunks/{_chunk_ids(client, sid, created['id'])[0]}"
    ).json()
    assert "heading" in chunk["locator"] and "offset" in chunk["locator"]

    refused = _one(client, sid, blocked["id"])
    assert refused["status"] == "failed"
    assert "HTTP 403" in refused["failure_reason"]
    assert refused["failure_next_step"] == errors.SAVE_AS_PDF

    bad = client.post(f"/api/spaces/{sid}/sources/url", json={"url": "ftp://example.com/x"})
    assert bad.status_code == 422


def test_url_on_local_network_is_refused(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    created = client.post(
        f"/api/spaces/{sid}/sources/url", json={"url": "http://localhost:8484/api/settings"}
    ).json()
    services_of(client).ingest.tools.web.resolver = lambda _h: ["127.0.0.1"]
    run_jobs(services_of(client))
    source = _one(client, sid, created["id"])
    assert source["failure_reason"] == (
        "This address is on your local network; Trustbook only fetches public web pages."
    )


def test_youtube_captions_and_fallbacks(
    client: TestClient, space: dict[str, Any], router: Router
) -> None:
    sid = space["id"]
    captions = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "Welcome to the show."}]},
            {"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "Today: pumps."}]},
        ]
    }
    router.json("GET", "https://www.youtube.com/api/timedtext", captions)
    info = {
        "title": "Pump talk",
        "duration": 4,
        "subtitles": {"en": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?v=1"}]},
    }
    services_of(client).ingest.tools.youtube = FakeYoutube(info)
    created = client.post(
        f"/api/spaces/{sid}/sources/url", json={"url": "https://youtu.be/dQw4w9WgXcQ"}
    ).json()
    assert (created["type"], created["group"], created["origin"]) == (
        "youtube",
        "transcripts",
        VIDEO,
    )
    run_jobs(services_of(client))
    video = _one(client, sid, created["id"])
    assert video["status"] == "ready", video
    assert video["title"] == "Pump talk"
    assert video["quality_warning"] == errors.WARN_SHORT  # manual captions: no STT warning
    snap = client.get(f"/api/spaces/{sid}/sources/{created['id']}/snapshot").json()
    assert snap["map"]["kind"] == "timestamps"
    assert snap["map"]["segments"][1] == {"start": 21, "end": 34, "start_s": 2.0, "end_s": 4.0}

    services_of(client).ingest.tools.youtube = FakeYoutube({"title": "Silent"})
    no_captions = client.post(f"/api/spaces/{sid}/sources/url", json={"url": VIDEO}).json()
    run_jobs(services_of(client))
    failed = _one(client, sid, no_captions["id"])
    assert failed["failure_reason"] == errors.NO_CAPTIONS_NO_STT[0]

    playlist = client.post(
        f"/api/spaces/{sid}/sources/url", json={"url": "https://www.youtube.com/playlist?list=1"}
    )
    assert playlist.status_code == 422


def test_youtube_without_captions_uses_stt(
    client: TestClient, space: dict[str, Any], tmp_path: Path
) -> None:
    sid = space["id"]
    fake = FakeYoutube({"title": "Talk", "duration": 4}, audio=speech_wav(tmp_path / "a.wav"))
    tools = services_of(client).ingest.tools
    tools.youtube = fake
    tools.stt = lambda: FakeStt("Speech recognised here.")
    created = client.post(f"/api/spaces/{sid}/sources/url", json={"url": VIDEO}).json()
    run_jobs(services_of(client))
    source = _one(client, sid, created["id"])
    assert source["status"] == "ready", source
    assert fake.downloads == 1
    assert FakeStt.warning in source["quality_warning"]


# --- web search ------------------------------------------------------------------------------


def test_web_search_and_add_results(
    client: TestClient, space: dict[str, Any], router: Router
) -> None:
    sid = space["id"]
    results = [
        {"title": f"Result {i}", "url": f"https://example.com/r{i}", "content": f"<b>s{i}</b>"}
        for i in range(25)
    ]
    router.json("GET", "http://searxng:8080/search", {"results": results})
    response = client.post(f"/api/spaces/{sid}/web-search", json={"query": "pumps", "limit": 50})
    assert response.status_code == 200
    found = response.json()["results"]
    assert len(found) == 20
    assert found[0] == {"title": "Result 0", "url": "https://example.com/r0", "snippet": "s0"}
    log = client.get("/api/request-log").json()
    assert log[0]["capability"] == "web_search"
    assert log[0]["space_id"] == sid
    assert (
        len(client.post(f"/api/spaces/{sid}/web-search", json={"query": "x"}).json()["results"])
        == 10
    )
    assert client.post(f"/api/spaces/{sid}/web-search", json={"query": " "}).status_code == 422

    router.add("GET", "https://example.com/r1", lambda _r: httpx.Response(200, html=ARTICLE))
    added = client.post(
        f"/api/spaces/{sid}/sources/from-search",
        json={"items": [{"url": "https://example.com/r1", "title": "Result 1"}]},
    )
    assert added.status_code == 201
    source = added.json()[0]
    assert (source["type"], source["group"], source["title"]) == ("search", "web", "Result 1")
    run_jobs(services_of(client))
    ready = _one(client, sid, source["id"])
    assert ready["status"] == "ready"
    assert ready["title"] == "Result 1"  # the chosen title is kept

    refreshed = client.post(f"/api/spaces/{sid}/sources/{source['id']}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "queued"


def test_web_search_needs_key_for_paid_providers(client: TestClient, space: dict[str, Any]) -> None:
    assert client.put("/api/capabilities/web_search", json={"provider": "brave"}).status_code == 200
    response = client.post(f"/api/spaces/{space['id']}/web-search", json={"query": "x"})
    assert response.status_code == 409
    assert "API key" in response.json()["detail"]


# --- folders ---------------------------------------------------------------------------------


def _project(paths: Paths) -> Path:
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "api.py").write_text(
        "def get(url):\n    return request('get', url)\n\n\ndef post(url):\n    return 1\n"
    )
    (root / "src" / "config.py").write_text('TOKEN = "ghp_fake"\n')
    (root / "src" / "models.py").write_text("class Response:\n    status = 200\n")
    (root / "README.md").write_text("# Proj\n\nA tiny HTTP library.")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("ignored")
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("d" * 40 + "\n")
    return root


def test_folder_source_end_to_end(client: TestClient, space: dict[str, Any], paths: Paths) -> None:
    sid = space["id"]
    _project(paths)
    services_of(client).ingest.tools.scanner = fake_scanner(
        [Finding("src/config.py", 1, "github-pat")]
    )
    created = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"}).json()
    assert (created["type"], created["title"], created["origin"]) == ("folder", "proj", "work/proj")
    run_jobs(services_of(client))
    source = _one(client, sid, created["id"])
    assert source["status"] == "ready", source
    assert source["group"] == "code"
    assert source["commit_hash"] == "d" * 40
    assert source["stats"]["files"] == 3
    assert source["stats"]["skipped_secrets"] == 1
    assert "1 file skipped: possible secrets." in source["quality_warning"]
    snap = client.get(f"/api/spaces/{sid}/sources/{created['id']}/snapshot").json()
    assert snap["map"]["kind"] == "files"
    assert "ghp_fake" not in snap["markdown"]
    report = services_of(client).spaces.get(sid).folder / "sources" / created["id"] / "secrets.json"
    assert "ghp_fake" not in report.read_text()

    code_chunks = [
        c
        for c in (
            client.get(f"/api/spaces/{sid}/chunks/{cid}").json()
            for cid in _chunk_ids(client, sid, created["id"])
        )
        if c["locator"].get("file") == "src/api.py"
    ]
    # tree-sitter (Phase 3): one chunk per function, with its symbol and line range
    assert [c["locator"] for c in code_chunks] == [
        {"file": "src/api.py", "start_line": 1, "end_line": 2, "symbol": "get", "kind": "function"},
        {
            "file": "src/api.py",
            "start_line": 5,
            "end_line": 6,
            "symbol": "post",
            "kind": "function",
        },
    ]
    assert code_chunks[0]["text"].startswith("File: src/api.py · get (lines 1-2)\n")


def test_folder_validation(client: TestClient, space: dict[str, Any], paths: Paths) -> None:
    sid = space["id"]
    url = f"/api/spaces/{sid}/sources/folder"
    assert client.post(url, json={"path": "x"}).status_code == 404  # browse root missing
    _project(paths)
    for path in ("", ".", "./"):
        response = client.post(url, json={"path": path})
        assert response.status_code == 400
        assert response.json()["detail"] == "Choose a folder inside your home folder."
    assert client.post(url, json={"path": "../etc"}).status_code == 400
    assert client.post(url, json={"path": "/etc"}).status_code == 400
    assert client.post(url, json={"path": "work/missing"}).status_code == 404
    assert client.post(url, json={"path": "work/proj/README.md"}).status_code == 400


def test_folder_without_gitleaks_fails_safely(
    client: TestClient, space: dict[str, Any], paths: Paths
) -> None:
    sid = space["id"]
    _project(paths)

    def missing(_folder: Path) -> list[Finding]:
        raise ScannerMissingError("gitleaks")

    services_of(client).ingest.tools.scanner = missing
    created = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"}).json()
    run_jobs(services_of(client))
    source = _one(client, sid, created["id"])
    assert source["status"] == "failed"
    assert source["failure_reason"] == errors.SCANNER_MISSING[0]
    assert _chunk_ids(client, sid, created["id"]) == []


# --- changes, refresh, delete, cancel --------------------------------------------------------


def test_patch_enable_refresh_and_delete(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    a = _paste(client, sid, LONG_TEXT)
    b = _paste(client, sid, "Second text.", title="B")
    run_jobs(services_of(client))

    patched = client.patch(
        f"/api/spaces/{sid}/sources/{a['id']}", json={"title": "Renamed", "enabled": False}
    ).json()
    assert (patched["title"], patched["enabled"]) == ("Renamed", False)
    assert (
        client.patch(f"/api/spaces/{sid}/sources/nope", json={"enabled": True}).status_code == 404
    )
    assert (
        client.patch(f"/api/spaces/{sid}/sources/{a['id']}", json={"title": ""}).status_code == 422
    )

    both = client.put(
        f"/api/spaces/{sid}/sources/enabled",
        json={"source_ids": [a["id"], b["id"]], "enabled": False},
    ).json()
    assert [s["enabled"] for s in both] == [False, False]
    missing = client.put(
        f"/api/spaces/{sid}/sources/enabled", json={"source_ids": ["nope"], "enabled": True}
    )
    assert missing.status_code == 404

    refused = client.post(f"/api/spaces/{sid}/sources/{a['id']}/refresh")
    assert refused.status_code == 409
    assert "can't be refreshed" in refused.json()["detail"]
    assert client.post(f"/api/spaces/{sid}/sources/nope/refresh").status_code == 404

    services = services_of(client)
    index = services.indexes.for_space(sid)
    before = index.count()
    folder = services.spaces.get(sid).folder / "sources" / a["id"]
    assert folder.is_dir()
    assert client.delete(f"/api/spaces/{sid}/sources/{a['id']}").status_code == 204
    assert not folder.exists()
    assert _chunk_ids(client, sid, a["id"]) == []
    assert index.count() < before
    assert client.delete(f"/api/spaces/{sid}/sources/{a['id']}").status_code == 404
    assert [s["id"] for s in _sources(client, sid)] == [b["id"]]


def test_refresh_replaces_queued_job(
    client: TestClient, space: dict[str, Any], router: Router
) -> None:
    sid = space["id"]
    router.add("GET", "https://example.com/pump", lambda _r: httpx.Response(200, html=ARTICLE))
    created = client.post(
        f"/api/spaces/{sid}/sources/url", json={"url": "https://example.com/pump"}
    ).json()
    refreshed = client.post(f"/api/spaces/{sid}/sources/{created['id']}/refresh").json()
    assert refreshed["job_id"] != created["job_id"]
    services = services_of(client)
    assert services.jobs.get(created["job_id"]).status == "cancelled"
    assert run_jobs(services) == 1
    source = _one(client, sid, created["id"])
    assert source["status"] == "ready"
    first_chunks = _chunk_ids(client, sid, created["id"])

    client.post(f"/api/spaces/{sid}/sources/{created['id']}/refresh")
    run_jobs(services)
    second_chunks = _chunk_ids(client, sid, created["id"])
    assert len(second_chunks) == len(first_chunks)
    assert set(second_chunks).isdisjoint(first_chunks)
    assert services.indexes.for_space(sid).count() == len(second_chunks)


def test_cancelled_jobs_show_as_failed(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    queued = _paste(client, sid, LONG_TEXT)
    assert client.post(f"/api/jobs/{queued['job_id']}/cancel").status_code == 200
    source = _one(client, sid, queued["id"])
    assert source["status"] == "failed"
    assert source["failure_reason"] == errors.CANCELLED[0]

    running = _paste(client, sid, LONG_TEXT)
    services = services_of(client)
    ctx = JobContext(
        job_id=running["job_id"],
        kind="ingest",
        space_id=sid,
        payload={"source_id": running["id"]},
        _progress=lambda _v: None,
        _cancelled=lambda: True,
    )
    with pytest.raises(JobCancelledError):
        services.ingest.handler(ctx)
    stored = services.ingest.repo(sid).get(running["id"])
    assert (stored.status, stored.failure_reason) == ("failed", errors.CANCELLED[0])


def test_missing_embedder_fails_with_reason(make_client: AppFactory, paths: Paths) -> None:
    client = make_client(providers=ProviderRegistry(paths.models_dir, factories={}))
    sid = client.post("/api/spaces", json={"name": "S"}).json()["id"]
    created = _paste(client, sid, LONG_TEXT)
    run_jobs(services_of(client))
    source = _one(client, sid, created["id"])
    assert source["failure_reason"] == errors.NO_EMBEDDER[0]
    job = client.get("/api/jobs").json()["jobs"][0]
    assert (job["status"], job["error"]) == ("failed", errors.NO_EMBEDDER[0])


def test_progress_is_reported_while_running(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    created = _paste(client, sid, LONG_TEXT)
    services = services_of(client)
    job = services.jobs.claim_next(None)
    assert job is not None
    services.jobs.set_progress(job.id, 0.5)
    services.ingest.repo(sid).update(created["id"], status="indexing")
    source = _one(client, sid, created["id"])
    assert (source["status"], source["progress"]) == ("indexing", 0.5)


def test_deleting_space_drops_index(client: TestClient, space: dict[str, Any]) -> None:
    sid = space["id"]
    _paste(client, sid, LONG_TEXT)
    services = services_of(client)
    run_jobs(services)
    services.indexes.for_space(sid)
    assert client.delete(f"/api/spaces/{sid}").status_code == 204
    with pytest.raises(SpaceNotFoundError):  # not served from the cache any more
        services.indexes.for_space(sid)
