"""Indexed commit vs. the folder's HEAD (SPEC §7.5, D-049): counted with dulwich, no git binary.

Repositories are built with dulwich itself, so the tests need no ``git`` on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from fastapi.testclient import TestClient

from app.config import Paths
from app.ingest.git import AheadTracker, commits_between, head_commit
from app.parse.documents import DocumentParser
from tests.conftest import AppFactory
from tests.ingest_helpers import fake_scanner, run_jobs, services_of

AUTHOR = b"Test <test@example.com>"


def _commit(root: Path, name: str, content: str, n: int) -> str:
    (root / name).write_text(content)
    porcelain.add(str(root), paths=[str(root / name)])
    sha = porcelain.commit(
        str(root),
        message=f"commit {n}".encode(),
        author=AUTHOR,
        committer=AUTHOR,
        author_timestamp=1_700_000_000 + n,
        commit_timestamp=1_700_000_000 + n,
        author_timezone=0,
        commit_timezone=0,
    )
    return sha.decode()


def _repo(root: Path, commits: int) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    porcelain.init(str(root))
    return [_commit(root, "a.py", f"def f():\n    return {i}\n", i) for i in range(commits)]


def _branch(root: Path) -> str:
    head = (root / ".git" / "HEAD").read_text().strip()
    assert head.startswith("ref: ")
    return head[5:]


def test_counts_like_rev_list(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shas = _repo(root, 5)
    assert head_commit(root) == shas[-1]
    assert commits_between(root, shas[0], shas[-1]) == 4
    assert commits_between(root, shas[3], shas[-1]) == 1
    assert commits_between(root, shas[-1], shas[-1]) == 0
    assert commits_between(root, shas[-1], shas[0]) == 0  # HEAD moved back: nothing ahead
    assert commits_between(root, shas[0], shas[-1], limit=2) is None  # too far to count cheaply


def test_packed_refs_and_packed_objects(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shas = _repo(root, 3)
    ref = _branch(root)
    loose = root / ".git" / ref
    loose.unlink()
    (root / ".git" / "packed-refs").write_text(f"# pack-refs with: peeled\n{shas[-1]} {ref}\n")
    with Repo(str(root)) as repo:
        repo.object_store.pack_loose_objects()
    assert not any((root / ".git" / "objects").glob("[0-9a-f][0-9a-f]/*"))
    assert head_commit(root) == shas[-1]
    assert commits_between(root, shas[0], shas[-1]) == 2


def test_detached_head(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shas = _repo(root, 4)
    (root / ".git" / "HEAD").write_text(shas[2] + "\n")
    assert head_commit(root) == shas[2]
    assert commits_between(root, shas[0], shas[2]) == 2


def test_shallow_clone_without_the_indexed_commit(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shas = _repo(root, 4)
    # A depth-2 shallow clone: the two oldest commits are gone, the boundary is recorded.
    for old in shas[:2]:
        (root / ".git" / "objects" / old[:2] / old[2:]).unlink()
    (root / ".git" / "shallow").write_text(shas[2] + "\n")
    assert commits_between(root, shas[0], shas[-1]) is None  # can't be counted
    assert commits_between(root, shas[2], shas[-1]) == 1
    tracker = AheadTracker()
    ahead = tracker.ahead(root, tmp_path, shas[0])
    assert ahead is not None
    assert (ahead.indexed_commit, ahead.head_commit, ahead.commits_ahead) == (
        shas[0],
        shas[-1],
        None,
    )


def test_not_a_checkout_or_bad_input(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert commits_between(plain, "a" * 40, "b" * 40) is None
    root = tmp_path / "proj"
    shas = _repo(root, 2)
    assert commits_between(root, "not-a-sha", shas[-1]) is None
    head = shas[-1]
    obj = root / ".git" / "objects" / head[:2] / head[2:]
    obj.chmod(0o644)  # loose objects are written read-only
    obj.write_bytes(b"not zlib data")
    assert commits_between(root, shas[0], head) is None  # unreadable: unknown, never an error


def test_linked_worktree_outside_the_boundary_is_not_followed(tmp_path: Path) -> None:
    real = tmp_path / "outside"
    shas = _repo(real, 2)
    inside = tmp_path / "host" / "wt"
    inside.mkdir(parents=True)
    (inside / ".git").write_text(f"gitdir: {real / '.git'}\n")
    assert commits_between(inside, shas[0], shas[1], tmp_path / "host") is None


def test_tracker_caches_head_briefly_and_counts_for_good(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shas = _repo(root, 2)
    now = [0.0]
    tracker = AheadTracker(clock=lambda: now[0])
    assert tracker.ahead(root, tmp_path, shas[-1]) is None  # HEAD is the indexed commit
    new = _commit(root, "b.py", "X = 1\n", 9)
    assert tracker.ahead(root, tmp_path, shas[-1]) is None  # HEAD cached for a few seconds
    now[0] += AheadTracker.HEAD_TTL_S + 0.1
    ahead = tracker.ahead(root, tmp_path, shas[-1])
    assert ahead is not None and ahead.head_commit == new and ahead.commits_ahead == 1
    shutil.rmtree(root / ".git" / "objects")  # counts are cached per commit pair
    ahead = tracker.ahead(root, tmp_path, shas[-1])
    assert ahead is not None and ahead.commits_ahead == 1


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    return client


def test_source_shows_how_far_the_folder_moved(client: TestClient, paths: Paths) -> None:
    root = paths.browse_root / "work" / "proj"
    shas = _repo(root, 2)
    sid = client.post("/api/spaces", json={"name": "Code"}).json()["id"]
    created = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"}).json()
    assert created["ahead"] is None  # queued
    run_jobs(services_of(client))
    url = f"/api/spaces/{sid}/sources/{created['id']}"
    source: dict[str, Any] = client.get(url).json()
    assert source["status"] == "ready" and source["commit_hash"] == shas[-1]
    assert source["ahead"] is None

    new = [_commit(root, "a.py", f"def f():\n    return {i}\n", i) for i in (10, 11, 12)]
    services_of(client).ingest.ahead_tracker = AheadTracker()  # skip the HEAD cache
    expected = {"indexed_commit": shas[-1], "head_commit": new[-1], "commits_ahead": 3}
    assert client.get(url).json()["ahead"] == expected
    listed = client.get(f"/api/spaces/{sid}/sources").json()
    assert listed[0]["ahead"] == expected

    assert client.post(f"{url}/refresh").status_code == 200
    run_jobs(services_of(client))
    source = client.get(url).json()
    assert source["commit_hash"] == new[-1] and source["ahead"] is None


def test_single_source_404(client: TestClient) -> None:
    sid = client.post("/api/spaces", json={"name": "Code"}).json()["id"]
    response = client.get(f"/api/spaces/{sid}/sources/nope")
    assert response.status_code == 404
