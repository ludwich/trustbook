"""Estimate route, job estimates and queue ETA, queue actions on ingest jobs (SPEC §11)."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.capabilities.bundled import ProviderRegistry
from app.config import Paths
from app.db.engine import get_engine
from app.db.models import Chunk
from app.index.memory import InMemoryIndexRegistry
from app.ingest import errors, estimate, service
from app.ingest.folder import walk_folder
from app.jobs.eta import ESTIMATE_KEY, FIXED_ESTIMATES
from tests.conftest import AppFactory, wait_for
from tests.fakes import FakeGrounding, FakeReranker, FakeTranscriber
from tests.ingest_helpers import run_jobs, services_of

CALIBRATION = {
    "version": 5,
    "measured_at": "2026-10-10T00:00:00Z",
    "cpu_count": 8,
    "embedding_chunks_per_s": 10.0,
    "embedding_chunks_per_s_code": 5.0,
    "rerank_pairs_per_s": 20.0,
    "parse_pages_per_s": 3.0,
    "stt_realtime_factor": 0.5,
    "notes": [],
}
LONG_TEXT = "# Report\n\n" + "\n\n".join(
    f"Paragraph {p}. " + " ".join(f"Finding {p}-{i} concerns supplier {i}." for i in range(60))
    for p in range(96)
)


@pytest.fixture
def sid(client: TestClient) -> str:
    space_id: str = client.post("/api/spaces", json={"name": "Acme"}).json()["id"]
    return space_id


def _calibrate(paths: Paths) -> None:
    paths.calibration_file.parent.mkdir(parents=True, exist_ok=True)
    paths.calibration_file.write_text(json.dumps(CALIBRATION))


def _project(paths: Paths, files: int = 3) -> Path:
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    for i in range(files):
        (root / "src" / f"m{i}.py").write_text(f"def f{i}():\n    return {i}\n" * 20)
    (root / "README.md").write_text("# Proj\n\nHello.\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.js").write_text("x\n")
    return root


def _estimate(client: TestClient, sid: str, *items: dict[str, Any]) -> Any:
    return client.post(f"/api/spaces/{sid}/sources/estimate", json={"items": list(items)})


# --- the route ---------------------------------------------------------------------------------


def test_folder_estimate_uses_the_ingestion_walk(
    client: TestClient, sid: str, paths: Paths
) -> None:
    root = _project(paths)
    response = _estimate(client, sid, {"kind": "folder", "path": "work/proj"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"seconds", "files", "summary", "confirm"}
    assert data["files"] == len(walk_folder(root).files) == 4
    assert data["confirm"] is False
    assert data["summary"].startswith("About 4 files, ")
    assert data["summary"].endswith("rough estimate (speed test not run yet)")
    _calibrate(paths)
    measured = _estimate(client, sid, {"kind": "folder", "path": "work/proj"}).json()
    assert measured["summary"] == "About 4 files, under a minute on this PC"
    assert measured["seconds"] <= data["seconds"]  # defaults assume a slow computer


def test_upload_and_item_estimates(client: TestClient, sid: str, paths: Paths) -> None:
    _calibrate(paths)
    files = [{"name": "a.pdf", "bytes": 250_000}, {"name": "C:\\docs\\b.mp3", "bytes": 1_600_000}]
    data = _estimate(client, sid, {"kind": "upload", "files": files}).json()
    work = estimate.upload_workload("a.pdf", 250_000)
    work += estimate.upload_workload("b.mp3", 1_600_000)  # the name only, not the path
    calibrated = estimate.rates_from(estimate.Calibration.model_validate(CALIBRATION))
    assert data["files"] == 2
    assert data["seconds"] == -(-work.seconds(calibrated) // 1)
    assert data["summary"] == "About 2 files, roughly 1 min on this PC"
    web = _estimate(client, sid, {"kind": "url", "count": 2}, {"kind": "paste"}).json()
    assert web["files"] is None
    assert web["summary"] == "Under a minute on this PC"
    youtube = _estimate(client, sid, {"kind": "youtube", "count": 1}).json()
    assert youtube["seconds"] > 0 and youtube["confirm"] is False


def test_long_adds_ask_for_confirmation(client: TestClient, sid: str, paths: Paths) -> None:
    _calibrate(paths)
    files = [{"name": f"talk{i}.mp3", "bytes": 16_000 * 600} for i in range(3)]  # 30 min audio
    data = _estimate(client, sid, {"kind": "upload", "files": files}).json()
    assert data["confirm"] is True
    assert data["seconds"] > 900  # half real time for the speech alone
    assert data["summary"] == f"About 3 files, roughly {round(data['seconds'] / 60)} min on this PC"


def test_estimate_errors(
    client: TestClient, sid: str, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = {"kind": "folder", "path": "work/proj"}
    paths.browse_root.mkdir(parents=True)
    assert _estimate(client, sid, folder).status_code == 404  # no such folder
    root = _project(paths)
    a_file = {"kind": "folder", "path": "work/proj/README.md"}
    assert _estimate(client, sid, a_file).status_code == 400
    assert _estimate(client, sid, {"kind": "folder", "path": ""}).status_code == 400
    assert _estimate(client, sid, {"kind": "folder", "path": "../etc"}).status_code == 400
    assert _estimate(client, "missing", folder).status_code == 404
    assert client.post(f"/api/spaces/{sid}/sources/estimate", json={"items": []}).status_code == 422
    assert _estimate(client, sid, {"kind": "ftp", "count": 1}).status_code == 422
    assert _estimate(client, sid, {"kind": "url", "count": 0}).status_code == 422
    bad_size = {"kind": "upload", "files": [{"name": "a.pdf", "bytes": -1}]}
    assert _estimate(client, sid, bad_size).status_code == 422
    monkeypatch.setattr(estimate, "walk_folder", lambda r: walk_folder(r, max_files=2))
    too_big = _estimate(client, sid, folder)
    assert too_big.status_code == 422
    assert too_big.json()["detail"].startswith(errors.FOLDER_TOO_BIG[0])
    assert root.is_dir()


# --- job estimates and the queue ETA ----------------------------------------------------------


def _payload(client: TestClient, job_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(services_of(client).jobs.get(job_id).payload)
    return data


def test_ingest_jobs_store_their_estimate_and_feed_the_eta(
    client: TestClient, sid: str, paths: Paths
) -> None:
    _calibrate(paths)
    _project(paths)
    folder = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"}).json()
    folder_s = _payload(client, folder["job_id"])[ESTIMATE_KEY]
    expected = _estimate(client, sid, {"kind": "folder", "path": "work/proj"}).json()["seconds"]
    assert folder_s == expected
    summary = client.get("/api/jobs").json()["summary"]
    assert (summary["queued"], summary["eta_seconds"]) == (1, folder_s)

    paste = client.post(f"/api/spaces/{sid}/sources/paste", json={"text": "Short note."}).json()
    paste_s = _payload(client, paste["job_id"])[ESTIMATE_KEY]
    calibrated = estimate.rates_from(estimate.Calibration.model_validate(CALIBRATION))
    assert paste_s == -(-estimate.text_workload(len("Short note.")).seconds(calibrated) // 1)
    eta = client.get("/api/jobs").json()["summary"]["eta_seconds"]
    assert eta == max(-(-(folder_s + paste_s) // 2), folder_s)

    # A running job counts what is left of it; a paused one doesn't count.
    jobs = services_of(client).jobs
    running = jobs.claim_next(None)
    assert running is not None and running.id == folder["job_id"]
    jobs.set_progress(running.id, 0.5)
    jobs.pause(paste["job_id"])
    assert client.get("/api/jobs").json()["summary"]["eta_seconds"] == -(-folder_s // 2)
    client.post(f"/api/jobs/{paste['job_id']}/resume")
    jobs.cancel(running.id)
    assert client.get("/api/jobs").json()["summary"]["eta_seconds"] == paste_s

    overview = jobs.enqueue("overview", "Overview", space_id=sid)
    eta = client.get("/api/jobs").json()["summary"]["eta_seconds"]
    assert eta == max(-(-(paste_s + FIXED_ESTIMATES["overview"]) // 2), FIXED_ESTIMATES["overview"])
    jobs.cancel(overview.id)
    jobs.cancel(paste["job_id"])
    assert client.get("/api/jobs").json()["summary"]["eta_seconds"] is None


def test_refresh_gets_a_fresh_estimate(client: TestClient, sid: str, paths: Paths) -> None:
    root = _project(paths, files=1)
    source = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"}).json()
    run_jobs(services_of(client))
    before = _payload(client, source["job_id"])[ESTIMATE_KEY]
    for i in range(30):
        (root / "src" / f"extra{i}.py").write_text("x = 1\n" * 2_000)
    refreshed = client.post(f"/api/spaces/{sid}/sources/{source['id']}/refresh").json()
    assert refreshed["job_id"] != source["job_id"]
    assert _payload(client, refreshed["job_id"])[ESTIMATE_KEY] > before


# --- queue actions on ingest jobs --------------------------------------------------------------


def test_active_space_ingest_jobs_go_first(client: TestClient) -> None:
    a = client.post("/api/spaces", json={"name": "A"}).json()["id"]
    b = client.post("/api/spaces", json={"name": "B"}).json()["id"]
    first = client.post(f"/api/spaces/{a}/sources/paste", json={"text": "Alpha."}).json()
    second = client.post(f"/api/spaces/{b}/sources/paste", json={"text": "Beta."}).json()
    assert client.put("/api/active-space", json={"space_id": b}).status_code == 204
    services = services_of(client)
    claimed = services.jobs.claim_next(services.pool.active_space_id)
    assert claimed is not None and claimed.id == second["job_id"]
    services.pool._run(claimed)
    # Reorder and pause still apply to the other space's waiting job.
    assert client.post(f"/api/jobs/{first['job_id']}/pause").json()["status"] == "paused"
    assert services.jobs.claim_next(services.pool.active_space_id) is None
    client.post(f"/api/jobs/{first['job_id']}/resume")
    run_jobs(services)
    sources = client.get(f"/api/spaces/{a}/sources").json()
    assert sources[0]["status"] == "ready"


class GateEmbedder:
    """Embeds like the fake, but holds its third call until the test lets it go."""

    dim = 4

    def __init__(self) -> None:
        self.calls = 0
        self.holding = threading.Event()
        self.release = threading.Event()

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.5, 0.25, 1.0] for _ in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls == 3:
            self.holding.set()
            assert self.release.wait(5)
        return [[float(len(t) % 7), 0.5, 0.25, 1.0] for t in texts]


def test_cancel_mid_embedding_removes_partial_rows(
    make_client: AppFactory, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "WRITE_BATCH", 8)  # some vectors are written before the cancel
    embedder = GateEmbedder()
    registry = ProviderRegistry(paths.models_dir, factories={})
    registry.override(
        embedder=embedder,
        reranker=FakeReranker(),
        grounding=FakeGrounding(),
        transcriber=FakeTranscriber(),
    )
    indexes = InMemoryIndexRegistry()
    client = make_client(providers=registry, indexes=indexes)
    sid = client.post("/api/spaces", json={"name": "S"}).json()["id"]
    created = client.post(f"/api/spaces/{sid}/sources/paste", json={"text": LONG_TEXT}).json()
    services = services_of(client)
    job = services.jobs.claim_next(None)
    assert job is not None
    worker = threading.Thread(target=services.pool._run, args=(job,))
    worker.start()
    try:
        assert embedder.holding.wait(5)
        record = services.spaces.get(sid)
        with Session(get_engine(record.db_path)) as session:
            assert session.exec(select(Chunk)).first() is not None  # rows exist mid-embedding
        wait_for(lambda: indexes.for_space(sid).count() > 0)  # and some vectors
        response = client.post(f"/api/jobs/{job.id}/cancel")
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
    finally:
        embedder.release.set()
        worker.join(10)
    assert not worker.is_alive()
    listed = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert listed[job.id]["status"] == "cancelled"
    source = client.get(f"/api/spaces/{sid}/sources").json()[0]
    assert source["id"] == created["id"]
    assert (source["status"], source["failure_reason"]) == ("failed", errors.CANCELLED[0])
    assert indexes.for_space(sid).count() == 0
    with Session(get_engine(record.db_path)) as session:
        assert session.exec(select(Chunk)).all() == []
    assert listed[job.id]["finished_at"] is not None
