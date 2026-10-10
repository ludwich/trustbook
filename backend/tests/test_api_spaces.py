from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Paths


def test_space_crud(client: TestClient, paths: Paths) -> None:
    assert client.get("/api/spaces").json() == []
    response = client.post("/api/spaces", json={"name": "  ACME Audit ", "description": "Q3"})
    assert response.status_code == 201
    space = response.json()
    assert set(space) == {
        "id",
        "slug",
        "name",
        "description",
        "created_at",
        "schema_version",
        "embedding",
        "folder",
        "source_count",
    }
    assert (space["name"], space["slug"], space["source_count"]) == ("ACME Audit", "acme-audit", 0)
    assert space["folder"] == str(paths.spaces_dir / "acme-audit")
    assert space["embedding"]["model"] == "google/embeddinggemma-2"
    assert (paths.spaces_dir / "acme-audit" / "space.json").is_file()

    second = client.post("/api/spaces", json={"name": "Beta"}).json()
    assert [s["id"] for s in client.get("/api/spaces").json()] == [second["id"], space["id"]]
    assert client.get(f"/api/spaces/{space['id']}").json() == space

    patched = client.patch(f"/api/spaces/{space['id']}", json={"name": "Renamed"}).json()
    assert (patched["name"], patched["slug"], patched["description"]) == (
        "Renamed",
        "acme-audit",
        "Q3",
    )

    assert client.delete(f"/api/spaces/{space['id']}").status_code == 204
    assert not (paths.spaces_dir / "acme-audit").exists()
    assert client.get(f"/api/spaces/{space['id']}").status_code == 404
    assert client.delete(f"/api/spaces/{space['id']}").status_code == 404


def test_space_validation(client: TestClient) -> None:
    assert client.post("/api/spaces", json={"name": "   "}).status_code == 422
    assert client.post("/api/spaces", json={"name": "x" * 81}).status_code == 422
    assert (
        client.post("/api/spaces", json={"name": "ok", "description": "d" * 501}).status_code == 422
    )
    assert client.post("/api/spaces", json={}).status_code == 422
    space = client.post("/api/spaces", json={"name": "ok"}).json()
    assert client.patch(f"/api/spaces/{space['id']}", json={"name": ""}).status_code == 422
    assert client.patch("/api/spaces/nope", json={"name": "x"}).status_code == 404


def test_active_space(client: TestClient) -> None:
    space = client.post("/api/spaces", json={"name": "A"}).json()
    assert client.put("/api/active-space", json={"space_id": space["id"]}).status_code == 204
    assert client.put("/api/active-space", json={"space_id": None}).status_code == 204
    assert client.put("/api/active-space", json={"space_id": "nope"}).status_code == 404


def test_delete_cancels_space_jobs(client: TestClient) -> None:
    space = client.post("/api/spaces", json={"name": "A"}).json()
    services = client.app.state.services  # type: ignore[attr-defined]
    job = services.jobs.enqueue("index", "Index", space_id=space["id"])
    client.put("/api/active-space", json={"space_id": space["id"]})
    client.delete(f"/api/spaces/{space['id']}")
    assert services.jobs.get(job.id).status == "cancelled"
    assert services.pool.active_space_id is None
