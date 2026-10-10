from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Paths


def test_browse_lists_dirs_first_and_hides_symlinks(client: TestClient, paths: Paths) -> None:
    root = paths.browse_root
    (root / "Projects" / "api").mkdir(parents=True)
    (root / "documents").mkdir()
    (root / ".config").mkdir()
    (root / "b.txt").write_text("hello")
    (root / "A.md").write_text("x")
    (root / "out").symlink_to(paths.data)

    data = client.get("/api/browse").json()
    assert data["path"] == "" and data["parent"] is None
    assert [e["name"] for e in data["entries"]] == [
        ".config",
        "documents",
        "Projects",
        "A.md",
        "b.txt",
    ]
    files = {e["name"]: e for e in data["entries"]}
    assert files["documents"] == {
        "name": "documents",
        "path": "documents",
        "is_dir": True,
        "size": None,
    }
    assert files["b.txt"]["size"] == 5

    sub = client.get("/api/browse", params={"path": "Projects"}).json()
    assert sub["parent"] == ""
    assert sub["entries"] == [{"name": "api", "path": "Projects/api", "is_dir": True, "size": None}]
    deeper = client.get("/api/browse", params={"path": "Projects/api"}).json()
    assert deeper["parent"] == "Projects"


def test_browse_refuses_escapes(client: TestClient, paths: Paths) -> None:
    assert client.get("/api/browse").status_code == 404  # root not mounted
    root = paths.browse_root
    root.mkdir(parents=True)
    (root / "f.txt").write_text("x")
    (root / "out").symlink_to(paths.data)
    for bad in ("..", "../data", "/etc", "a/../../x"):
        response = client.get("/api/browse", params={"path": bad})
        assert response.status_code == 400, bad
    assert client.get("/api/browse", params={"path": "out"}).status_code == 400
    assert client.get("/api/browse", params={"path": "missing"}).status_code == 404
    assert client.get("/api/browse", params={"path": "f.txt"}).status_code == 400
