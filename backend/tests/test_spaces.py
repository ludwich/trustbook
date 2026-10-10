from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.config import Paths, ensure_layout
from app.db.migrate import latest_version
from app.spaces.service import (
    SpaceNotFoundError,
    SpaceService,
    UnsafeSpacePathError,
    slugify,
)
from tests.fakes import write_models_lock


@pytest.fixture
def service(paths: Paths) -> SpaceService:
    ensure_layout(paths)
    return SpaceService(paths)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("ACME Audit 2026", "acme-audit-2026"),
        ("  Café — Größe  ", "cafe-groe"),
        ("!!!", "space"),
        ("", "space"),
        ("CON", "con-space"),
        ("../../etc", "etc"),
        ("a" * 100, "a" * 48),
    ],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_create_builds_folder_layout(service: SpaceService, paths: Paths) -> None:
    record = service.create("ACME Audit", "Q3 engagement")
    folder = paths.spaces_dir / "acme-audit"
    assert record.folder == folder
    assert record.slug == "acme-audit"
    for sub in ("vectors", "sources", "ideas"):
        assert (folder / sub).is_dir()
    assert (folder / "space.db").is_file()
    manifest = json.loads((folder / "space.json").read_text())
    assert manifest["id"] == record.id
    assert len(record.id) == 32
    assert manifest["name"] == "ACME Audit"
    assert manifest["description"] == "Q3 engagement"
    assert manifest["schema_version"] == latest_version("space")
    assert manifest["embedding"] == {
        "model": "google/embeddinggemma-2",
        "revision": "914f7f89142e33e77833254d9c9b90c3cef7303b",
    }
    assert "created_at" in manifest
    assert service.source_count(record) == 0


def test_embedding_from_models_lock(service: SpaceService, paths: Paths) -> None:
    write_models_lock(paths.models_dir)
    record = service.create("X")
    assert record.embedding_revision == "rev-embeddings"


def test_slugs_are_unique(service: SpaceService) -> None:
    first = service.create("Same")
    second = service.create("Same")
    third = service.create("same!")
    assert [first.slug, second.slug, third.slug] == ["same", "same-2", "same-3"]
    assert len({first.id, second.id, third.id}) == 3


def test_list_newest_first_and_get(service: SpaceService) -> None:
    a = service.create("A")
    b = service.create("B")
    assert [r.id for r in service.list_all()] == [b.id, a.id]
    assert service.get(a.id).name == "A"
    assert service.names() == {a.id: "A", b.id: "B"}
    with pytest.raises(SpaceNotFoundError):
        service.get("nope")
    assert service.find("nope") is None


def test_update_keeps_slug(service: SpaceService) -> None:
    record = service.create("Old name")
    updated = service.update(record.id, name="New name", description="d")
    assert updated.name == "New name"
    assert updated.description == "d"
    assert updated.slug == "old-name"
    assert service.update(record.id, description="e").name == "New name"


def test_delete_cancels_jobs_and_removes_folder(paths: Paths) -> None:
    ensure_layout(paths)
    cancelled: list[str] = []
    service = SpaceService(paths, cancel_jobs=cancelled.append)
    record = service.create("Gone")
    service.delete(record.id)
    assert cancelled == [record.id]
    assert not record.folder.exists()
    assert service.list_all() == []


def test_delete_refuses_symlinked_space(
    service: SpaceService, paths: Paths, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious")
    (outside / "space.json").write_text(
        json.dumps({"id": "evil", "name": "Evil", "created_at": "2026-01-01T00:00:00Z"})
    )
    os.symlink(outside, paths.spaces_dir / "evil")
    # Symlinked folders are not listed, so they cannot be deleted through the service.
    with pytest.raises(SpaceNotFoundError):
        service.delete("evil")
    with pytest.raises(UnsafeSpacePathError):
        service._checked_folder(paths.spaces_dir / "evil")
    assert (outside / "keep.txt").exists()


def test_checked_folder_refuses_outside_paths(service: SpaceService, paths: Paths) -> None:
    with pytest.raises(UnsafeSpacePathError):
        service._checked_folder(paths.data)
    with pytest.raises(UnsafeSpacePathError):
        service._checked_folder(paths.spaces_dir)
    with pytest.raises(UnsafeSpacePathError):
        service._checked_folder(paths.spaces_dir / ".." / "config")


def test_unreadable_manifest_is_skipped(service: SpaceService, paths: Paths) -> None:
    broken = paths.spaces_dir / "broken"
    broken.mkdir()
    (broken / "space.json").write_text("{")
    (paths.spaces_dir / "no-manifest").mkdir()
    (paths.spaces_dir / "file.txt").write_text("x")
    service.create("Fine")
    assert [r.name for r in service.list_all()] == ["Fine"]


def test_failed_create_cleans_up(
    service: SpaceService, paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_a: object, **_k: object) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr("app.spaces.service.migrate", fail)
    with pytest.raises(RuntimeError):
        service.create("Broken")
    assert list(paths.spaces_dir.iterdir()) == []


def test_migrate_all_updates_schema_version(service: SpaceService) -> None:
    record = service.create("Old")
    manifest_path = record.folder / "space.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 0
    manifest_path.write_text(json.dumps(manifest))
    service.migrate_all()
    assert service.get(record.id).schema_version == latest_version("space")


def test_source_count_unreadable_db(service: SpaceService) -> None:
    record = service.create("X")
    record.db_path.unlink()
    assert service.source_count(record) == 0
    record.db_path.write_text("not a database")
    assert service.source_count(record) == 0
