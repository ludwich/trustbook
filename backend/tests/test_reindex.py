"""Re-indexing a space whose chunks an older chunker made (D-106): status, API, the job."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.chunking.dispatch import CHUNKER_VERSION, CHUNKER_VERSIONS
from app.config import Paths
from app.db.models import Chunk, Source, SourceFile
from app.ingest.reindex import (
    BEFORE_RECORDS,
    REINDEX_KEY,
    StoredSnapshotError,
    chunk_kind,
    outdated_kinds,
    reindex_work,
    stored_snapshot,
)
from app.jobs.eta import ESTIMATE_KEY
from app.parse.documents import DocumentParser
from app.parse.snapshot import Segment
from app.services import Services
from tests.conftest import AppFactory
from tests.ingest_helpers import fake_scanner, run_jobs, services_of

GUIDE = (
    "# Guide\n\nIntro to the guide.\n\n## Install\n\n"
    + " ".join(f"Step {i} installs part {i} of the pump." for i in range(40))
    + "\n\n## Retries\n\nRetries are off by default.\n"
)


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    return client


def _space(client: TestClient) -> str:
    response = client.post("/api/spaces", json={"name": "Docs"})
    assert response.status_code == 201
    sid: str = response.json()["id"]
    return sid


def _paste(client: TestClient, sid: str, text: str = GUIDE) -> str:
    response = client.post(f"/api/spaces/{sid}/sources/paste", json={"text": text})
    assert response.status_code == 201, response.text
    run_jobs(services_of(client))
    source_id: str = response.json()["id"]
    return source_id


def _status(client: TestClient, sid: str) -> dict[str, Any]:
    response = client.get(f"/api/spaces/{sid}/reindex")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


def _chunks(services: Services, sid: str, source_id: str) -> list[Chunk]:
    return services.ingest.repo(sid).chunks(source_id)


def _make_old(services: Services, sid: str, source_id: str) -> list[Chunk]:
    """Turn a source into one indexed by the previous chunker: no record of its chunker
    version, and chunks as that chunker made them (no "Section:" line)."""
    repo = services.ingest.repo(sid)
    source = repo.get(source_id)
    stats = json.loads(source.stats)
    stats.pop("chunkers")
    repo.update(source_id, stats=stats)
    old: list[Chunk] = []
    for chunk in repo.chunks(source_id):
        body = chunk.text.partition("\n")[2] if chunk.text.startswith("Section: ") else chunk.text
        old.append(
            Chunk(
                id=chunk.id,
                source_id=chunk.source_id,
                ordinal=chunk.ordinal,
                text=body,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                locator=chunk.locator,
                token_count=chunk.token_count,
                parent_key=chunk.parent_key,
                structure=chunk.structure,
            )
        )
    repo.replace_chunks(source_id, old)
    chunks = repo.chunks(source_id)
    assert chunks and all(not c.text.startswith("Section: ") for c in chunks)
    return chunks


# --- what is old --------------------------------------------------------------------------------


def test_a_new_space_needs_no_reindex(client: TestClient) -> None:
    sid = _space(client)
    assert _status(client, sid) == {
        "needed": False,
        "sources": [],
        "reindexing": False,
        "estimate_seconds": None,
        "estimate_summary": None,
        "confirm": False,
    }
    _paste(client, sid)
    assert _status(client, sid)["needed"] is False


def test_unknown_space_is_404(client: TestClient) -> None:
    assert client.get("/api/spaces/nope/reindex").status_code == 404
    assert client.post("/api/spaces/nope/reindex").status_code == 404


def test_a_source_of_an_older_chunker_is_reported_with_an_estimate(client: TestClient) -> None:
    services = services_of(client)
    sid = _space(client)
    source_id = _paste(client, sid)
    _make_old(services, sid, source_id)
    status = _status(client, sid)
    assert status["needed"] is True
    assert status["reindexing"] is False
    assert status["sources"] == [{"id": source_id, "title": "Guide", "reindexing": False}]
    assert isinstance(status["estimate_seconds"], int) and status["estimate_seconds"] >= 1
    assert status["estimate_summary"].startswith("Under a minute on this PC")
    assert status["confirm"] is False


def test_outdated_kinds_by_record_files_and_chunks() -> None:
    def source(type_: str, stats: dict[str, Any] | None = None) -> Source:
        return Source(
            id="s",
            type=type_,
            title="t",
            group="documents",
            status="ready",
            stats=json.dumps(stats or {}),
            created_at="x",
            updated_at="x",
        )

    current = {"document": CHUNKER_VERSION}
    assert outdated_kinds(source("paste", {"chunkers": current}), [], None) == set()
    older = {"document": BEFORE_RECORDS, "code": CHUNKER_VERSIONS["code"]}
    assert outdated_kinds(source("folder", {"chunkers": older}), [], None) == {"document"}
    # Before the record: web, paste and upload chunks changed; transcripts did not.
    assert outdated_kinds(source("url"), [], {"heading": "A", "offset": 0}) == {"document"}
    assert outdated_kinds(source("youtube"), [], {"start_s": 0.0, "end_s": 4.0}) == set()
    assert outdated_kinds(source("paste"), [], None) == set()  # no chunks: nothing to redo

    def record(path: str, kind: str, chunker: str) -> SourceFile:
        return SourceFile(
            source_id="s",
            path=path,
            sha256="x",
            kind=kind,
            chunker=chunker,
            start_offset=0,
            end_offset=1,
        )

    code_only = [record("a.py", "code", BEFORE_RECORDS)]
    assert outdated_kinds(source("folder"), code_only, {"file": "a.py"}) == set()
    mixed = [*code_only, record("README.md", "document", BEFORE_RECORDS)]
    assert outdated_kinds(source("folder"), mixed, {"file": "a.py"}) == {"document"}
    # A folder indexed before file records existed.
    assert outdated_kinds(source("folder"), [], {"file": "a.py"}) == {"code", "document"}


def test_chunk_kinds_and_the_work_of_a_reindex() -> None:
    assert chunk_kind({"start_s": 1.0}) == "transcript"
    assert chunk_kind({"file": "a.py", "symbol": None}) == "code"
    assert chunk_kind({"file": "a.md", "heading": None}) == "document"
    assert chunk_kind({"heading": "A", "offset": 0}) == "document"
    assert chunk_kind({"pages": [1]}) == "document"
    locators = [{"file": "a.py"}, {"file": "a.md", "heading": "A"}, {"heading": None}]
    work = reindex_work(locators, {"document"})
    assert (work.code_chunks, work.text_chunks) == (0, 2)


def test_the_stored_snapshot_of_a_folder() -> None:
    markdown = "# a.py\n\nx = 1\n\n# README.md\n\nHello\n\n# r.pdf\n\nPage one\n\nPage two"
    a = markdown.index("x = 1")
    r = markdown.index("Hello")
    p1, p2 = markdown.index("Page one"), markdown.index("Page two")
    data = {
        "kind": "files",
        "segments": [
            Segment(a, a + 5, file="a.py", start_line=1, end_line=1).to_json(),
            Segment(r, r + 5, file="README.md", start_line=1, end_line=1).to_json(),
            Segment(p1, p1 + 8, file="r.pdf", page=1).to_json(),
            Segment(p2, p2 + 8, file="r.pdf", page=2).to_json(),
        ],
    }
    snapshot = stored_snapshot(markdown, data)
    assert [(e.path, e.start, e.end, e.kind) for e in snapshot.files] == [
        ("a.py", a, a + 5, "code"),
        ("README.md", r, r + 5, "document"),
        ("r.pdf", p1, p2 + 8, "document"),
    ]
    with pytest.raises(StoredSnapshotError):
        stored_snapshot(markdown, {"kind": "nonsense"})
    with pytest.raises(StoredSnapshotError):
        stored_snapshot(markdown, {"kind": "files", "segments": []})
    assert stored_snapshot("Text.", {"kind": "sections", "segments": []}).kind == "sections"


# --- the re-index job ---------------------------------------------------------------------------


def test_reindex_runs_through_the_queue_and_keeps_the_old_index_until_it_is_ready(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = services_of(client)
    sid = _space(client)
    source_id = _paste(client, sid)
    old = _make_old(services, sid, source_id)
    old_ids = {c.id for c in old}
    index = services.indexes.for_space(sid)
    assert index.count() == len(old)

    response = client.post(f"/api/spaces/{sid}/reindex")
    assert response.status_code == 202
    status = response.json()
    assert status["needed"] is True and status["reindexing"] is True
    assert status["sources"] == [{"id": source_id, "title": "Guide", "reindexing": True}]
    assert status["estimate_seconds"] is None  # nothing left to queue
    # One ingest job in the normal queue, with its estimate; the source stays ready.
    (job,) = [j for j in client.get("/api/jobs").json()["jobs"] if j["status"] == "queued"]
    assert (job["kind"], job["title"]) == ("ingest", "Re-index “Guide”")
    payload = json.loads(services.jobs.get(job["id"]).payload)
    assert payload["source_id"] == source_id and payload[REINDEX_KEY] is True
    assert isinstance(payload[ESTIMATE_KEY], int)
    source = client.get(f"/api/spaces/{sid}/sources/{source_id}").json()
    assert (source["status"], source["enabled"]) == ("ready", True)
    # Asking again queues nothing more.
    assert client.post(f"/api/spaces/{sid}/reindex").status_code == 202
    assert len([j for j in client.get("/api/jobs").json()["jobs"] if j["status"] == "queued"]) == 1

    seen: dict[str, Any] = {}
    embed = services.ingest._embed_into

    def watched(*args: Any) -> None:
        embed(*args)
        # The new vectors exist, but chat still reads the old chunks of a ready source.
        seen["ids"] = {c.id for c in _chunks(services, sid, source_id)}
        seen["status"] = services.ingest.repo(sid).get(source_id).status
        seen["vectors"] = index.count()

    monkeypatch.setattr(services.ingest, "_embed_into", watched)
    assert run_jobs(services) == 1
    assert seen["ids"] == old_ids and seen["status"] == "ready"
    assert seen["vectors"] > len(old)

    new = _chunks(services, sid, source_id)
    assert all(c.text.startswith("Section: ") for c in new)
    assert old_ids.isdisjoint(c.id for c in new)
    assert index.count() == len(new)  # the old vectors are gone
    after = client.get(f"/api/spaces/{sid}/sources/{source_id}").json()
    assert after["status"] == "ready"
    assert after["stats"]["chunkers"] == {"document": CHUNKER_VERSION}
    assert after["stats"]["chunks"] == len(new)
    assert services.jobs.get(job["id"]).status == "done"
    assert _status(client, sid)["needed"] is False
    hits = index.text_search("Retries default", limit=3, source_ids=[source_id])
    assert hits and hits[0].chunk_id in {c.id for c in new}


def test_unchanged_chunks_keep_their_id_and_vector(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = services_of(client)
    sid = _space(client)
    source_id = _paste(client, sid)
    before = _chunks(services, sid, source_id)
    monkeypatch.setitem(CHUNKER_VERSIONS, "document", "next-chunker")
    assert _status(client, sid)["sources"][0]["id"] == source_id
    embedded: list[int] = []
    embed = services.ingest._embed_into
    monkeypatch.setattr(
        services.ingest,
        "_embed_into",
        lambda ctx, index, chunks: (embedded.append(len(chunks)), embed(ctx, index, chunks)),
    )
    client.post(f"/api/spaces/{sid}/reindex")
    run_jobs(services)
    after = _chunks(services, sid, source_id)
    assert [c.id for c in after] == [c.id for c in before]  # old citations still open
    assert embedded == []  # nothing embedded again
    source = services.ingest.repo(sid).get(source_id)
    assert json.loads(source.stats)["chunkers"] == {"document": "next-chunker"}
    assert _status(client, sid)["needed"] is False


def test_a_failed_reindex_leaves_the_source_as_it_was(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = services_of(client)
    sid = _space(client)
    source_id = _paste(client, sid)
    old = _make_old(services, sid, source_id)
    index = services.indexes.for_space(sid)
    embed = services.ingest._embed_into

    def broken(*args: Any) -> None:
        embed(*args)
        raise RuntimeError("disk full")

    monkeypatch.setattr(services.ingest, "_embed_into", broken)
    client.post(f"/api/spaces/{sid}/reindex")
    run_jobs(services)
    jobs = [j for j in client.get("/api/jobs").json()["jobs"] if j["kind"] == "ingest"]
    assert jobs[0]["status"] == "failed"
    source = client.get(f"/api/spaces/{sid}/sources/{source_id}").json()
    assert (source["status"], source["enabled"]) == ("ready", True)
    assert [c.id for c in _chunks(services, sid, source_id)] == [c.id for c in old]
    assert index.count() == len(old)  # the new vectors were removed
    status = _status(client, sid)
    assert status["needed"] is True and status["reindexing"] is False


def test_a_reindex_without_its_stored_snapshot_fails_plainly(client: TestClient) -> None:
    services = services_of(client)
    sid = _space(client)
    source_id = _paste(client, sid)
    _make_old(services, sid, source_id)
    (services.ingest.repo(sid).folder(source_id) / "snapshot.md").unlink()
    client.post(f"/api/spaces/{sid}/reindex")
    run_jobs(services)
    (job,) = [j for j in client.get("/api/jobs").json()["jobs"] if j["kind"] == "ingest"][:1]
    assert job["status"] == "failed"
    assert job["error"].startswith("The saved copy of this source could not be read")
    assert client.get(f"/api/spaces/{sid}/sources/{source_id}").json()["status"] == "ready"


def test_a_folder_reindex_keeps_code_chunks_and_rechunks_documents(
    client: TestClient, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = services_of(client)
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("def alpha():\n    return 1\n")
    (root / "README.md").write_text("# Proj\n\nA tiny client.\n\n## Usage\n\nCall alpha.\n")
    sid = _space(client)
    created = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"})
    assert created.status_code == 201
    run_jobs(services)
    source_id = created.json()["id"]
    before = _chunks(services, sid, source_id)
    code = {c.id for c in before if json.loads(c.locator)["file"] == "src/a.py"}
    # The folder changes on disk; a re-index still reads only the stored snapshot.
    (root / "README.md").write_text("# Other\n\nSomething else entirely.\n")
    monkeypatch.setitem(CHUNKER_VERSIONS, "document", "next-chunker")
    status = _status(client, sid)
    assert [s["id"] for s in status["sources"]] == [source_id]
    client.post(f"/api/spaces/{sid}/reindex")
    run_jobs(services)
    after = _chunks(services, sid, source_id)
    assert code <= {c.id for c in after}
    assert any("Call alpha." in c.text for c in after)
    assert not any("Something else" in c.text for c in after)
    records = {f.path: f.chunker for f in services.ingest.repo(sid).files(source_id)}
    assert records == {"README.md": "next-chunker", "src/a.py": CHUNKER_VERSIONS["code"]}
    assert _status(client, sid)["needed"] is False
