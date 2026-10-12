"""A linked folder's commits, read straight from ``.git`` (no git binary, read-only mounts).

HEAD is resolved by hand (loose refs, packed refs, detached HEAD, linked worktrees). Counting
commits between two commits walks the commit graph with dulwich (Apache-2.0), so the image needs
no GPL ``git`` binary (D-049).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("trustbook.git")

_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def _read(path: Path) -> str | None:
    try:
        return path.read_text("utf-8", errors="replace").strip()
    except OSError:
        return None


def _git_dir(folder: Path, boundary: Path) -> Path | None:
    dot_git = folder / ".git"
    if dot_git.is_symlink():
        return None
    if dot_git.is_dir():
        return dot_git
    content = _read(dot_git) if dot_git.is_file() else None
    if content and content.startswith("gitdir:"):
        target = (folder / content[len("gitdir:") :].strip()).resolve()
        if target.is_relative_to(boundary.resolve()) and target.is_dir():
            return target
    return None


def _resolve_ref(git_dir: Path, common: Path, ref: str) -> str | None:
    if not ref.startswith("refs/") or ".." in ref:
        return None
    for base in (git_dir, common):
        value = _read(base / ref)
        if value and _SHA.match(value):
            return value
    packed = _read(common / "packed-refs") or ""
    for line in packed.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref and _SHA.match(parts[0]):
            return parts[0]
    return None


def head_commit(folder: Path, boundary: Path | None = None) -> str | None:
    """The commit HEAD points at, or ``None`` if ``folder`` is not a readable git checkout.

    ``boundary``: a linked worktree's git dir is only followed when it lies inside this folder.
    """
    git_dir = _git_dir(folder, boundary or folder)
    if git_dir is None:
        return None
    common_text = _read(git_dir / "commondir")
    common = (git_dir / common_text).resolve() if common_text else git_dir
    head = _read(git_dir / "HEAD")
    if not head:
        return None
    if head.startswith("ref:"):
        return _resolve_ref(git_dir, common, head[4:].strip())
    return head if _SHA.match(head) else None


# --- commits ahead (SPEC §7.5, D-049) -------------------------------------------------------

MAX_COUNT = 10_000  # walking further is not "cheap"; the count is reported as unknown


def commits_between(
    folder: Path, old: str, new: str, boundary: Path | None = None, *, limit: int = MAX_COUNT
) -> int | None:
    """``git rev-list --count old..new`` from the commit graph (dulwich, no git binary).

    ``None`` when it can't be told: not a checkout, ``old`` or ``new`` missing from the object
    store (a shallow clone that no longer holds the indexed commit), a broken history, or more
    than ``limit`` commits. Shallow boundaries (``.git/shallow``) are respected.
    """
    if not (_SHA.match(old) and _SHA.match(new)):
        return None
    if _git_dir(folder, boundary or folder) is None:
        return None
    try:
        from dulwich.objects import ObjectID
        from dulwich.repo import Repo

        with Repo(str(folder)) as repo:
            store = repo.object_store
            old_id, new_id = ObjectID(old.encode("ascii")), ObjectID(new.encode("ascii"))
            if old_id not in store or new_id not in store:
                return None
            count = 0
            for _entry in repo.get_walker(include=[new_id], exclude=[old_id]):
                count += 1
                if count > limit:
                    return None
            return count
    except Exception:  # any unreadable repository state means "unknown", never an error
        logger.info("Could not count commits in a linked folder", exc_info=True)
        return None


@dataclass(frozen=True)
class Ahead:
    """The linked folder's HEAD differs from the indexed commit."""

    indexed_commit: str
    head_commit: str
    commits_ahead: int | None  # None: the count can't be told (e.g. a shallow clone)


class AheadTracker:
    """``Ahead`` per folder, cheap enough for every ``GET /sources``.

    HEAD is re-read at most every :data:`HEAD_TTL_S` seconds per folder (a few small file reads);
    counts are cached per (folder, indexed, head), since a commit pair's distance never changes.
    """

    HEAD_TTL_S = 5.0
    MAX_COUNTS = 256

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._heads: dict[Path, tuple[float, str | None]] = {}
        self._counts: OrderedDict[tuple[Path, str, str], int | None] = OrderedDict()
        self._lock = threading.Lock()

    def head(self, folder: Path, boundary: Path) -> str | None:
        now = self._clock()
        with self._lock:
            cached = self._heads.get(folder)
            if cached is not None and now - cached[0] < self.HEAD_TTL_S:
                return cached[1]
        value = head_commit(folder, boundary)
        with self._lock:
            self._heads[folder] = (now, value)
        return value

    def ahead(self, folder: Path, boundary: Path, indexed: str) -> Ahead | None:
        """``None`` when HEAD is the indexed commit or can't be read."""
        head = self.head(folder, boundary)
        if head is None or head == indexed:
            return None
        key = (folder, indexed, head)
        with self._lock:
            if key in self._counts:
                self._counts.move_to_end(key)
                return Ahead(indexed, head, self._counts[key])
        count = commits_between(folder, indexed, head, boundary)
        with self._lock:
            self._counts[key] = count
            while len(self._counts) > self.MAX_COUNTS:
                self._counts.popitem(last=False)
        return Ahead(indexed, head, count)
