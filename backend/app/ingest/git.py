"""Read a folder's HEAD commit straight from ``.git`` (no git binary, works on read-only mounts)."""

from __future__ import annotations

import re
from pathlib import Path

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
