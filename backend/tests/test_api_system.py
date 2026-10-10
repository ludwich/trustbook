from __future__ import annotations

import stat
from dataclasses import replace

from fastapi.testclient import TestClient

from app.config import Paths
from app.jobs.calibration import CALIBRATION_VERSION
from tests.conftest import AppFactory, wait_for


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok", "version": "test"}


def test_startup_creates_layout_and_private_settings(client: TestClient, paths: Paths) -> None:
    for folder in (paths.config_dir, paths.spaces_dir, paths.clones_dir):
        assert folder.is_dir()
    assert paths.global_db.is_file()
    assert stat.S_IMODE(paths.settings_file.stat().st_mode) == 0o600


def test_setup_flow(client: TestClient) -> None:
    assert client.get("/api/setup").json() == {"completed": False, "chat_ready": False}
    response = client.post("/api/setup/complete")
    assert response.status_code == 409
    assert response.json() == {"detail": "Set up the chat model first."}
    client.put("/api/capabilities/chat", json={"preset": "mock"})
    assert client.post("/api/setup/complete").json() == {"completed": True, "chat_ready": True}
    assert client.get("/api/setup").json() == {"completed": True, "chat_ready": True}


def test_settings_get_and_put(client: TestClient) -> None:
    assert client.get("/api/settings").json() == {
        "power": "normal",
        "setup_completed": False,
        "workers": 2,
        "thresholds": {"refuse": 0.15, "verify": 0.5},
        "hints_dismissed": [],
    }
    body = client.put("/api/settings", json={"power": "low"}).json()
    assert (body["power"], body["workers"]) == ("low", 1)
    assert client.get("/api/jobs").json()["summary"]["workers"] == 1
    assert client.put("/api/settings", json={"power": "turbo"}).status_code == 422
    assert client.put("/api/settings", json={"setup_completed": True}).status_code == 422
    assert client.put("/api/settings", json={}).json()["power"] == "low"


def test_settings_persist_across_restart(make_client: AppFactory) -> None:
    first = make_client()
    first.put("/api/settings", json={"power": "high"})
    second = make_client()
    assert second.get("/api/settings").json()["power"] == "high"


def test_unknown_api_path(client: TestClient) -> None:
    assert client.get("/api/nope").status_code == 404


def test_first_start_calibration_runs(make_client: AppFactory, paths: Paths) -> None:
    client = make_client(start_workers=True, calibrate_on_start=True)
    wait_for(lambda: client.get("/api/calibration").json()["status"] == "done")
    results = client.get("/api/calibration").json()["results"]
    assert results["version"] == CALIBRATION_VERSION
    assert results["embedding_chunks_per_s"] > 0
    assert paths.calibration_file.is_file()


def test_static_spa_fallback(make_client: AppFactory, paths: Paths) -> None:
    static = paths.data.parent / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "assets" / "app.js").write_text("console.log(1)")
    (paths.data.parent / "secret.txt").write_text("nope")
    client = make_client(paths=replace(paths, static_dir=static))
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/spaces/abc").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert "nope" not in client.get("/..%2Fsecret.txt").text
    assert client.get("/api/missing").status_code == 404
    assert client.get("/api/health").json()["status"] == "ok"


def test_no_static_dir_means_no_fallback(client: TestClient) -> None:
    assert client.get("/").status_code == 404
