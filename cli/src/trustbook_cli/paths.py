"""Folder paths for ``sources add folder`` (D-088).

The API takes a path relative to the browse root, the home folder mounted read-only at ``/host``
(SPEC §4). The CLI also accepts Windows separators and an absolute path, which is mapped onto the
browse root given with ``--browse-root`` (``TRUSTBOOK_BROWSE_ROOT``): the host folder on a laptop
(``C:/Users/anna``) or ``/host`` inside the container.
"""

from __future__ import annotations

import re

from trustbook_cli.client import TrustbookError

_DRIVE = re.compile(r"^[A-Za-z]:(/|$)")


def _slashes(path: str) -> str:
    return re.sub(r"/+", "/", path.strip().replace("\\", "/"))


def is_absolute(path: str) -> bool:
    """POSIX (``/x``), UNC (``\\\\server\\x``) or drive (``C:\\x``, ``C:/x``) absolute path."""
    p = _slashes(path)
    return p.startswith("/") or bool(_DRIVE.match(p))


def _parts(path: str) -> list[str]:
    return [part for part in _slashes(path).split("/") if part not in ("", ".")]


def folder_api_path(path: str, browse_root: str | None) -> str:
    """The API path (relative, ``/``-separated) for a folder typed on the command line.

    A relative path is relative to the browse root, not to the current directory. An absolute
    path must lie inside ``browse_root``; drive paths compare case-insensitively (Windows).

    :raises TrustbookError: an absolute path without a browse root, outside it, or ``..``.
    """
    if not path.strip():
        raise TrustbookError("Give a folder path.")
    if is_absolute(path):
        if not browse_root:
            raise TrustbookError(
                f"{path} is an absolute path. Give it relative to the folder Trustbook may read "
                "(TRUSTBOOK_BROWSE_ROOT in .env), or pass --browse-root with that folder."
            )
        relative = _strip_root(path, browse_root)
    else:
        relative = _parts(path)
    if any(part == ".." for part in relative):
        raise TrustbookError(f"{path}: '..' is not allowed; give a folder inside the browse root.")
    if not relative:
        raise TrustbookError("Choose a folder inside the browse root, not the root itself.")
    return "/".join(relative)


def _strip_root(path: str, root: str) -> list[str]:
    target, base = _parts(path), _parts(root)
    drive = bool(_DRIVE.match(_slashes(path))) or bool(_DRIVE.match(_slashes(root)))

    def fold(parts: list[str]) -> list[str]:
        return [p.casefold() for p in parts] if drive else parts

    if not is_absolute(root) or fold(target[: len(base)]) != fold(base):
        raise TrustbookError(
            f"{path} is not inside the browse root {root}. Trustbook can only read folders there."
        )
    return target[len(base) :]
