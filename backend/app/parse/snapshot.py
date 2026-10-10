"""A source's extracted text (``snapshot.md``) and its offset map (``map.json``).

See docs/ARCHITECTURE.md, "Snapshot files". Offsets are character offsets into the markdown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from app.fsutil import atomic_write_json, atomic_write_text

MapKind = Literal["pages", "timestamps", "sections", "files"]
MAP_VERSION = 1

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$", re.MULTILINE)


@dataclass(frozen=True)
class Segment:
    """Where ``markdown[start:end]`` came from (only the keys that apply are set)."""

    start: int
    end: int
    page: int | None = None
    start_s: float | None = None
    end_s: float | None = None
    speaker: str | None = None
    heading: str | None = None
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            f.name: getattr(self, f.name) for f in fields(self) if getattr(self, f.name) is not None
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Segment:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class FileEntry:
    """One file of a linked folder inside the snapshot (``kind`` picks the chunker)."""

    path: str
    start: int
    end: int
    kind: Literal["code", "document"]


@dataclass
class Snapshot:
    """Extracted text, its map and what the reader learned on the way."""

    markdown: str
    kind: MapKind
    segments: list[Segment]
    title: str | None = None
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    files: list[FileEntry] = field(default_factory=list)
    group: str | None = None
    commit_hash: str | None = None

    def map_json(self) -> dict[str, Any]:
        return {
            "version": MAP_VERSION,
            "kind": self.kind,
            "segments": [s.to_json() for s in self.segments],
        }


class SnapshotBuilder:
    """Appends text pieces and records a segment for each piece that has a location."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self.segments: list[Segment] = []

    @property
    def length(self) -> int:
        return self._length

    def text(self, text: str) -> None:
        """Append text that has no location of its own (separators, file headers)."""
        self._parts.append(text)
        self._length += len(text)

    def add(self, text: str, **where: Any) -> tuple[int, int]:
        """Append ``text`` and map it to ``where``; return its (start, end) offsets."""
        start = self._length
        self.text(text)
        if text:
            self.segments.append(Segment(start, self._length, **where))
        return start, self._length

    def separate(self) -> None:
        """Ensure the next piece starts a new paragraph."""
        if self._length:
            self.text("\n\n")

    def build(self) -> str:
        return "".join(self._parts)


def sections_from_markdown(markdown: str) -> list[Segment]:
    """Split markdown into sections at heading lines (the preamble has no heading)."""
    segments: list[Segment] = []
    matches = list(_HEADING.finditer(markdown))
    if not matches or matches[0].start() > 0:
        first = matches[0].start() if matches else len(markdown)
        if markdown[:first].strip():
            segments.append(Segment(0, first))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        segments.append(Segment(match.start(), end, heading=match.group(2).strip()))
    return segments


def normalise_text(text: str) -> str:
    """Unix newlines, no NUL bytes, no trailing whitespace at the end."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").rstrip()


def write_snapshot(folder: Path, snapshot: Snapshot) -> None:
    """Write ``snapshot.md`` and ``map.json`` into a source folder."""
    folder.mkdir(parents=True, exist_ok=True)
    atomic_write_text(folder / "snapshot.md", snapshot.markdown)
    atomic_write_json(folder / "map.json", snapshot.map_json())


def read_snapshot_files(folder: Path) -> tuple[str, dict[str, Any]] | None:
    """Return (markdown, map) or ``None`` if the source has no snapshot yet."""
    md_path = folder / "snapshot.md"
    if not md_path.is_file():
        return None
    markdown = md_path.read_text("utf-8")
    try:
        data = json.loads((folder / "map.json").read_text("utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {"version": MAP_VERSION, "kind": "sections", "segments": []}
    return markdown, data
