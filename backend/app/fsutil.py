"""File-system helpers shared by settings, spaces and calibration."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    """Write ``text`` to ``path`` atomically (temp file in the same folder + ``os.replace``).

    The file gets permission bits ``mode`` before it becomes visible under its final name.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    # os.replace keeps the temp file's mode; re-apply in case the target pre-existed elsewhere.
    path.chmod(mode)


def atomic_write_json(path: Path, data: Any, *, mode: int = 0o644) -> None:
    """Serialise ``data`` as indented JSON and write it atomically."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode=mode)
