"""Paths relative to the read-only browse root (``/host``): no ``..``, no absolute paths and no
symlinks that lead out of the root."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class BrowsePathError(ValueError):
    """The path is not allowed. ``str(exc)`` is plain English."""


class BrowseRootMissingError(LookupError):
    """The browse root is not mounted."""


OUTSIDE_MESSAGE = "Only folders inside your home folder can be used."


def normalise_relative(path: str) -> str:
    """Clean a user-given relative path ("" is the root). Refuses ``..`` and absolute paths."""
    raw = path.strip()
    if raw.startswith(("/", "\\")):
        raise BrowsePathError(OUTSIDE_MESSAGE)
    parts = [p for p in PurePosixPath(raw).parts if p not in ("", ".")]
    if any(p == ".." for p in parts) or "\x00" in raw:
        raise BrowsePathError(OUTSIDE_MESSAGE)
    return "/".join(parts)


def resolve_in_root(root: Path, path: str) -> tuple[Path, str]:
    """(absolute path, normalised relative path) for ``path`` inside ``root``.

    :raises BrowseRootMissingError: the root is not available.
    :raises BrowsePathError: the path leaves the root (``..``, absolute, or via a symlink).
    :raises FileNotFoundError: the path does not exist.
    """
    if not root.is_dir():
        raise BrowseRootMissingError(str(root))
    relative = normalise_relative(path)
    real_root = root.resolve()
    target = (root / relative) if relative else root
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(relative)
    resolved = target.resolve()
    if not resolved.is_relative_to(real_root):
        raise BrowsePathError(OUTSIDE_MESSAGE)
    return resolved, relative
