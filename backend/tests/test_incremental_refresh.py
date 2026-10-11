"""Incremental refresh of linked folders (SPEC §7.5, D-048): only changed files are re-indexed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Paths
from app.db.models import Chunk
from app.ingest import incremental
from app.ingest.folder import read_files
from app.parse.documents import DocumentParser
from app.services import Services
from tests.conftest import AppFactory
from tests.ingest_helpers import fake_scanner, run_jobs, services_of

A_PY = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
B_PY = (
    'TIMEOUT = 30\n\n\nclass Client:\n    """Talks to the server."""\n\n'
    "    def send(self, request):\n        return request\n\n"
    "    def close(self):\n        return None\n"
)


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    return client


def _project(paths: Paths) -> Path:
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text(A_PY)
    (root / "src" / "b.py").write_text(B_PY)
    (root / "src" / "c.py").write_text("def gamma():\n    return 3\n")
    (root / "README.md").write_text("# Proj\n\nA tiny client library for the demo server.")
    return root


def _setup(client: TestClient, paths: Paths) -> tuple[Path, str, str]:
    root = _project(paths)
    sid: str = client.post("/api/spaces", json={"name": "Code"}).json()["id"]
    created = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"})
    assert created.status_code == 201
    run_jobs(services_of(client))
    return root, sid, created.json()["id"]


def _chunks(services: Services, sid: str, source_id: str) -> list[Chunk]:
    return services.ingest.repo(sid).chunks(source_id)


def _by_symbol(chunks: list[Chunk], file: str, symbol: str | None) -> Chunk:
    for chunk in chunks:
        locator = json.loads(chunk.locator)
        if locator["file"] == file and locator.get("symbol") == symbol:
            return chunk
    raise AssertionError(f"no chunk {file} {symbol}")


def _refresh(client: TestClient, sid: str, source_id: str) -> dict[str, Any]:
    assert client.post(f"/api/spaces/{sid}/sources/{source_id}/refresh").status_code == 200
    run_jobs(services_of(client))
    source: dict[str, Any] = client.get(f"/api/spaces/{sid}/sources/{source_id}").json()
    return source


def test_refresh_keeps_unchanged_chunks_and_their_highlight(
    client: TestClient, paths: Paths
) -> None:
    services = services_of(client)
    root, sid, source_id = _setup(client, paths)
    first = _chunks(services, sid, source_id)
    index = services.indexes.for_space(sid)
    assert index.count() == len(first)
    send = _by_symbol(first, "src/b.py", "Client.send")
    before = client.get(f"/api/spaces/{sid}/chunks/{send.id}/context").json()
    assert before["code"]["highlight"] == [7, 8]

    # a.py grows (everything after it in the snapshot moves), c.py is removed
    (root / "src" / "a.py").write_text("import os\n\n\n" + A_PY + "\n\ndef delta():\n    pass\n")
    (root / "src" / "c.py").unlink()
    source = _refresh(client, sid, source_id)
    assert source["status"] == "ready"
    assert source["stats"]["changed_files"] == 2  # a.py changed, c.py removed
    assert source["stats"]["unchanged_files"] == 2  # b.py, README.md

    second = _chunks(services, sid, source_id)
    assert index.count() == len(second)  # no stale or duplicate vectors
    kept = {c.id for c in first if json.loads(c.locator)["file"] in ("src/b.py", "README.md")}
    assert kept <= {c.id for c in second}  # unchanged files keep their chunk ids
    old_a = {c.id for c in first if json.loads(c.locator)["file"] == "src/a.py"}
    assert old_a.isdisjoint(c.id for c in second)
    assert not any(json.loads(c.locator)["file"] == "src/c.py" for c in second)
    assert [c.ordinal for c in second] == list(range(len(second)))
    assert _by_symbol(second, "src/a.py", "delta")

    moved = _by_symbol(second, "src/b.py", "Client.send")
    assert moved.id == send.id and moved.start_offset > send.start_offset
    markdown = client.get(f"/api/spaces/{sid}/sources/{source_id}/snapshot").json()["markdown"]
    assert moved.text.endswith(markdown[moved.start_offset : moved.end_offset])
    after = client.get(f"/api/spaces/{sid}/chunks/{send.id}/context").json()
    assert after["code"]["highlight"] == [7, 8]
    assert after["code"]["lines"] == B_PY.rstrip("\n").split("\n")  # the whole file, from map
    lines = after["code"]["lines"][6:8]
    assert lines == ["    def send(self, request):", "        return request"]
    # a removed file's chunk no longer resolves
    gone = _by_symbol(first, "src/c.py", "gamma")
    assert client.get(f"/api/spaces/{sid}/chunks/{gone.id}").status_code == 404


def test_refresh_without_changes_embeds_nothing(client: TestClient, paths: Paths) -> None:
    services = services_of(client)
    _root, sid, source_id = _setup(client, paths)
    first = _chunks(services, sid, source_id)
    embedder: Any = services.providers.ingest_embedder(0)
    calls = embedder.calls
    source = _refresh(client, sid, source_id)
    assert source["stats"]["changed_files"] == 0
    assert source["stats"]["unchanged_files"] == 4
    assert embedder.calls == calls
    second = _chunks(services, sid, source_id)
    assert [(c.id, c.start_offset, c.end_offset) for c in second] == [
        (c.id, c.start_offset, c.end_offset) for c in first
    ]


def test_refresh_keeps_each_kept_chunks_parent(client: TestClient, paths: Paths) -> None:
    """D-077: parent keys and structure are file-relative, so a reused file's chunks keep them
    while their offsets move; the chunk API never shows them (they are not citable)."""
    services = services_of(client)
    root, sid, source_id = _setup(client, paths)
    first = _chunks(services, sid, source_id)
    send = _by_symbol(first, "src/b.py", "Client.send")
    assert send.parent_key == "src/b.py::Client"
    assert send.structure is not None and '"kind":"class"' in send.structure
    readme = next(c for c in first if json.loads(c.locator)["file"] == "README.md")
    assert readme.parent_key == "README.md#L1"
    (root / "src" / "a.py").write_text("import os\n\n\n" + A_PY)  # b.py moves in the snapshot
    _refresh(client, sid, source_id)
    moved = _by_symbol(_chunks(services, sid, source_id), "src/b.py", "Client.send")
    assert moved.id == send.id and moved.start_offset > send.start_offset
    assert (moved.parent_key, moved.structure) == (send.parent_key, send.structure)
    shown = client.get(f"/api/spaces/{sid}/chunks/{send.id}").json()
    assert "parent_key" not in shown and "structure" not in shown
    assert "parent" not in json.dumps(shown["locator"])


def test_refresh_resolves_a_kept_files_imports_against_the_new_files(
    client: TestClient, paths: Paths
) -> None:
    """D-077: import targets depend on the snapshot's other files. A reused file's imports are
    resolved again on refresh, so they match a full re-index when an imported file is added or
    removed (and never name a file the source no longer has)."""
    services = services_of(client)
    root, sid, source_id = _setup(client, paths)
    (root / "src" / "e.py").write_text("from .d import LIMIT\n\n\ndef use():\n    return LIMIT\n")
    _refresh(client, sid, source_id)

    def e_import() -> tuple[str, list[dict[str, Any]]]:
        chunk = _by_symbol(_chunks(services, sid, source_id), "src/e.py", None)
        imports: list[dict[str, Any]] = json.loads(chunk.structure or "{}")["imports"]
        return chunk.id, imports

    first_id, imports = e_import()
    assert [(i["text"], i["target"]) for i in imports] == [("from .d import LIMIT", None)]
    (root / "src" / "d.py").write_text("LIMIT = 3\n")
    source = _refresh(client, sid, source_id)
    assert source["stats"]["changed_files"] == 1  # only d.py is new: e.py is reused
    kept_id, imports = e_import()
    assert kept_id == first_id
    assert [(i["target"], i.get("module", False)) for i in imports] == [("src/d.py", False)]
    (root / "src" / "d.py").unlink()
    _refresh(client, sid, source_id)
    kept_id, imports = e_import()
    assert kept_id == first_id
    assert [i["target"] for i in imports] == [None]


def test_failed_refresh_forgets_file_records(
    client: TestClient, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = services_of(client)
    root, sid, source_id = _setup(client, paths)
    repo = services.ingest.repo(sid)
    assert len(repo.files(source_id)) == 4
    first = {c.id for c in _chunks(services, sid, source_id)}

    def broken(*_args: Any) -> None:
        raise RuntimeError("embedding failed")

    (root / "src" / "a.py").write_text("def changed():\n    return 0\n")
    monkeypatch.setattr(services.ingest, "_embed_into", broken)
    source = _refresh(client, sid, source_id)
    assert source["status"] == "failed"
    assert repo.files(source_id) == [] and _chunks(services, sid, source_id) == []
    assert services.indexes.for_space(sid).count() == 0

    monkeypatch.undo()
    source = _refresh(client, sid, source_id)
    assert source["status"] == "ready" and source["enabled"] is True
    second = {c.id for c in _chunks(services, sid, source_id)}
    assert first.isdisjoint(second)  # a full re-index: nothing could be trusted
    assert services.indexes.for_space(sid).count() == len(second)


def test_other_chunker_version_reindexes_everything(
    client: TestClient, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = services_of(client)
    _root, sid, source_id = _setup(client, paths)
    first = {c.id for c in _chunks(services, sid, source_id)}
    monkeypatch.setattr(incremental, "CHUNKER_VERSION", "next-chunker")
    source = _refresh(client, sid, source_id)
    assert source["stats"]["unchanged_files"] == 0
    assert first.isdisjoint(c.id for c in _chunks(services, sid, source_id))
    assert {f.chunker for f in services.ingest.repo(sid).files(source_id)} == {"next-chunker"}


def test_records_that_no_longer_match_the_snapshot_are_not_trusted(
    client: TestClient, paths: Paths
) -> None:
    services = services_of(client)
    _root, sid, source_id = _setup(client, paths)
    repo = services.ingest.repo(sid)
    records = repo.files(source_id)
    b_record = next(r for r in records if r.path == "src/b.py")
    b_ids = set(json.loads(b_record.chunk_ids))
    # Simulate a snapshot rewritten by an interrupted run: b.py's text is no longer where recorded
    folder = repo.folder(source_id)
    markdown = (folder / "snapshot.md").read_text()
    (folder / "snapshot.md").write_text("x" * 7 + markdown)
    previous = incremental.load_previous(repo, source_id, folder)
    assert previous is not None and "src/b.py" not in previous.snapshot.files
    source = _refresh(client, sid, source_id)
    assert source["status"] == "ready"
    assert b_ids.isdisjoint(c.id for c in _chunks(services, sid, source_id))


def test_read_files_skips_decoding_unchanged_files(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n")
    (root / "b.py").write_text("B = 2\n")
    parser = DocumentParser(tmp_path, lambda: 1, available=False)
    first = read_files(root, ["a.py", "b.py"], parser, lambda _f: None, lambda: None)
    assert all(r.sha256 and r.text for r in first)
    hashes = {"a.py": first[0].sha256 or "", "b.py": "0" * 64}
    second = read_files(
        root, ["a.py", "b.py"], parser, lambda _f: None, lambda: None, previous_hashes=hashes
    )
    assert second[0].reused and second[0].text is None
    assert not second[1].reused and second[1].text == "B = 2"


def test_deleting_a_source_removes_its_file_records(client: TestClient, paths: Paths) -> None:
    services = services_of(client)
    _root, sid, source_id = _setup(client, paths)
    repo = services.ingest.repo(sid)
    assert repo.files(source_id)
    assert client.delete(f"/api/spaces/{sid}/sources/{source_id}").status_code == 204
    assert repo.files(source_id) == []
