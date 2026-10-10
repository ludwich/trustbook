from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.capabilities.http import space_context
from app.config import Paths
from app.jobs.calibration import CALIBRATION_VERSION
from tests.conftest import AppFactory, wait_for
from tests.fakes import Router


def _services(client: TestClient) -> Any:
    return client.app.state.services  # type: ignore[attr-defined]


def test_request_log_routes(client: TestClient, router: Router) -> None:
    router.json("GET", "http://searxng:8080/search", {"results": []})
    space = client.post("/api/spaces", json={"name": "ACME"}).json()
    client.post("/api/capabilities/web_search/test")
    with space_context(space["id"], "ACME"):
        _services(client).health.test("web_search")
    entries = client.get("/api/request-log").json()
    assert len(entries) == 2
    newest, older = entries
    assert set(newest) == {
        "id",
        "timestamp",
        "capability",
        "endpoint_host",
        "space_id",
        "space_name",
        "bytes_sent",
        "tokens_sent",
        "excerpt",
    }
    assert (newest["space_id"], newest["space_name"]) == (space["id"], "ACME")
    assert (older["capability"], older["endpoint_host"]) == ("web_search", "searxng")
    assert older["excerpt"].startswith("GET /search?q=trustbook")
    paged = client.get(f"/api/request-log?limit=10&before_id={newest['id']}").json()
    assert [e["id"] for e in paged] == [older["id"]]
    assert client.get("/api/request-log?limit=0").status_code == 422
    assert client.delete("/api/request-log").status_code == 204
    assert client.get("/api/request-log").json() == []


def test_calibration_routes(make_client: AppFactory, paths: Paths) -> None:
    client = make_client()
    assert client.get("/api/calibration").json() == {"status": "pending", "results": None}
    response = client.post("/api/calibration/run")
    assert response.status_code == 202
    job = response.json()
    assert (job["kind"], job["status"]) == ("calibrate", "queued")
    assert client.post("/api/calibration/run").json()["id"] == job["id"]
    _services(client).pool.start()
    wait_for(lambda: client.get("/api/calibration").json()["status"] == "done")
    results = client.get("/api/calibration").json()["results"]
    assert set(results) == {
        "version",
        "measured_at",
        "cpu_count",
        "embedding_chunks_per_s",
        "rerank_pairs_per_s",
        "parse_pages_per_s",
        "stt_realtime_factor",
        "notes",
        "cpu",
        "runtime",
    }
    assert set(results["cpu"]) == {"model", "logical", "usable", "physical", "bf16"}
    assert results["runtime"]["torch_threads"] >= 1
    assert results["parse_pages_per_s"] is None
    assert paths.calibration_file.is_file()


def test_calibration_not_requeued_when_done(make_client: AppFactory, paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    paths.calibration_file.write_text(
        json.dumps(
            {
                "version": CALIBRATION_VERSION,
                "measured_at": "2026-10-10T10:00:00.000Z",
                "cpu_count": 4,
                "embedding_chunks_per_s": 3.0,
                "rerank_pairs_per_s": 9.0,
                "parse_pages_per_s": None,
                "stt_realtime_factor": 0.1,
                "notes": [],
            }
        )
    )
    client = make_client(calibrate_on_start=True)
    assert client.get("/api/jobs").json()["jobs"] == []
