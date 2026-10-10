"""Grounding layer 5: deterministic code checks (SPEC §8).

- Every backticked identifier in a sentence must occur verbatim in at least one cited chunk.
  The only normalisation is a trailing ``()`` (prose writes ``get()`` for a function ``get``).
- Every cited chunk whose locator names a ``file`` must point at a file, and line range, that
  exist in the source's indexed snapshot (the ``files`` segments of ``map.json``).

No model, no network: plain string and file checks.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger("trustbook.grounding")

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

LineRange = tuple[int, int]


def backticked(text: str) -> list[str]:
    """The backticked identifiers of ``text``, unique, in order (``get()`` → ``get``)."""
    found: dict[str, None] = {}
    for match in _BACKTICKED.finditer(text):
        token = match.group(1).strip()
        if token.endswith("()") and len(token) > 2:
            token = token[:-2]
        if token:
            found.setdefault(token, None)
    return list(found)


_NAME_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?")


def _occurs(token: str, texts: list[str]) -> bool:
    """``token`` as a whole word in any of ``texts`` (exact, case-sensitive)."""
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])")
    return any(pattern.search(t) for t in texts)


def missing_identifiers(text: str, chunk_texts: Iterable[str]) -> list[str]:
    """Backticked names of ``text`` not found in ``chunk_texts`` (exact, deterministic).

    A backticked span passes if it occurs verbatim in a cited chunk, or if every identifier and
    number inside it occurs there as a whole word: code defines ``class Response`` and
    ``def ok``, so ``Response.ok`` never appears verbatim (D-042). A span without identifiers
    (e.g. a quoted literal) must occur verbatim.
    """
    texts = list(chunk_texts)
    missing: list[str] = []
    for token in backticked(text):
        if any(token in t for t in texts):
            continue
        parts = _NAME_PART.findall(token)
        if not parts or not all(_occurs(part, texts) for part in parts):
            missing.append(token)
    return missing


def _line(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def files_from_map(data: Mapping[str, Any]) -> dict[str, list[LineRange | None]]:
    """``file → line ranges`` of a ``map.json`` (None for a segment without line numbers)."""
    files: dict[str, list[LineRange | None]] = {}
    segments = data.get("segments")
    if not isinstance(segments, list):
        return files
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        file = segment.get("file")
        if not isinstance(file, str) or not file:
            continue
        start, end = _line(segment.get("start_line")), _line(segment.get("end_line"))
        files.setdefault(file, []).append((start, end) if start and end else None)
    return files


class SnapshotFiles:
    """Files and line ranges of each source's indexed snapshot, read once per instance.

    ``sources_dir`` is a space's ``sources/`` folder. A source without a readable ``map.json``
    has no files, so every file citation of it fails (grounding first).
    """

    def __init__(self, sources_dir: Path | None) -> None:
        self._dir = sources_dir
        self._cache: dict[str, dict[str, list[LineRange | None]]] = {}
        self._lock = threading.Lock()

    def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
        with self._lock:
            cached = self._cache.get(source_id)
            if cached is None:
                cached = self._read(source_id)
                self._cache[source_id] = cached
            return cached

    def _read(self, source_id: str) -> dict[str, list[LineRange | None]]:
        if self._dir is None or not _SAFE_ID.match(source_id):
            return {}
        path = self._dir / source_id / "map.json"
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            logger.info("No readable map.json for source %s; its file citations fail", source_id)
            return {}
        return files_from_map(data) if isinstance(data, dict) else {}


def locator_problem(files: SnapshotFiles, source_id: str, locator: Mapping[str, Any]) -> str | None:
    """Why a cited chunk's file/line range does not exist in the snapshot (None when it does).

    Locators without a ``file`` (pages, sections, timestamps) are not code and pass.
    """
    file = locator.get("file")
    if not isinstance(file, str) or not file:
        return None
    ranges = files.files(source_id).get(file)
    if ranges is None:
        return f"{file} is not in the indexed snapshot"
    raw_start, raw_end = locator.get("start_line"), locator.get("end_line")
    if raw_start is None and raw_end is None:
        return None
    start, end = _line(raw_start), _line(raw_end)
    if start is None or end is None or start < 1 or end < start:
        return f"{file} has an invalid line range"
    if any(r is not None and r[0] <= start and end <= r[1] for r in ranges):
        return None
    return f"lines {start}-{end} are not in {file}"
