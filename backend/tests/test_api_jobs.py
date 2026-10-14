from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.jobs.handlers import HandlerRegistry
from tests.conftest import AppFactory, wait_for


def _services(client: TestClient) -> Any:
    return client.app.state.services  # type: ignore[attr-defined]


def test_jobs_list_and_actions(client: TestClient) -> None:
    space = client.post("/api/spaces", json={"name": "ACME"}).json()
    jobs = _services(client).jobs
    a = jobs.enqueue("index", "Index A", space_id=space["id"])
    b = jobs.enqueue("index", "Index B")
    data = client.get("/api/jobs").json()
    assert data["summary"] == {
        "queued": 2,
        "running": 0,
        "paused": 0,
        "eta_seconds": None,
        "workers": 2,
    }
    first = data["jobs"][0]
    assert set(first) == {
        "id",
        "kind",
        "title",
        "space_id",
        "space_name",
        "status",
        "progress",
        "position",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "eta_seconds",
        "stage",
    }
    assert (first["id"], first["space_name"]) == (a.id, "ACME")

    assert client.post(f"/api/jobs/{a.id}/pause").json()["status"] == "paused"
    paused_again = client.post(f"/api/jobs/{a.id}/pause")
    assert paused_again.status_code == 409
    assert paused_again.json() == {"detail": "Only waiting jobs can be paused."}
    assert client.post(f"/api/jobs/{a.id}/resume").json()["status"] == "queued"

    assert client.post("/api/jobs/reorder", json={"job_ids": [b.id, a.id]}).status_code == 204
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [b.id, a.id]
    assert client.post("/api/jobs/reorder", json={"job_ids": ["nope"]}).status_code == 404

    cancelled = client.post(f"/api/jobs/{b.id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert client.post(f"/api/jobs/{b.id}/cancel").status_code == 409
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_worker_runs_registered_handler(make_client: AppFactory) -> None:
    handlers = HandlerRegistry()
    handlers.register("hello", lambda ctx: ctx.progress(0.5))
    client = make_client(handlers=handlers, start_workers=True)
    job = _services(client).jobs.enqueue("hello", "Hello")
    _services(client).pool.notify()
    wait_for(lambda: client.get("/api/jobs").json()["jobs"][0]["status"] == "done")
    done = client.get("/api/jobs").json()["jobs"][0]
    assert (done["id"], done["progress"]) == (job.id, 1.0)


def test_interrupted_jobs_requeued_on_start(make_client: AppFactory) -> None:
    first = make_client()
    jobs = _services(first).jobs
    job = jobs.enqueue("x", "X")
    jobs.claim_next(None)
    second = make_client()
    assert _services(second).jobs.get(job.id).status == "queued"
