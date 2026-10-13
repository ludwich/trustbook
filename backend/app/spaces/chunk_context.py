"""The source viewer's view of a cited chunk (Gate 1 G6).

The browser used to slice the whole snapshot with the chunk's offsets. Those offsets count
Unicode code points (Python), while JavaScript strings index UTF-16 units, so an emoji earlier in
the snapshot shifted the highlight. Here the server does the slicing and returns strings and
line numbers only:

- code chunks (locator with ``file`` and line numbers): the file's lines (whole file up to
  :data:`WHOLE_FILE_LINES` lines, else :data:`WINDOW_LINES` lines around the chunk), the cited
  line range, the commit and a language name;
- everything else: up to :data:`CONTEXT_CHARS` characters before and after the chunk, cut on
  line or word boundaries, and a label from the locator ("Pages 3–4", "Section: Install",
  "01:02–01:45").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chunking.header import without_section_header
from app.parse.snapshot import Segment

WHOLE_FILE_LINES = 2_000
WINDOW_LINES = 200
CONTEXT_CHARS = 1_500
FILE_PREFIX = "File: "

LANGUAGES = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "jsx",
    "ts": "typescript",
    "tsx": "tsx",
    "cs": "csharp",
    "java": "java",
    "kt": "kotlin",
    "go": "go",
    "rs": "rust",
    "rb": "ruby",
    "php": "php",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cc": "cpp",
    "hpp": "cpp",
    "swift": "swift",
    "scala": "scala",
    "sql": "sql",
    "sh": "bash",
    "bash": "bash",
    "ps1": "powershell",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "toml": "toml",
    "ini": "ini",
    "cfg": "ini",
    "xml": "xml",
    "html": "html",
    "htm": "html",
    "css": "css",
    "scss": "scss",
    "md": "markdown",
    "markdown": "markdown",
    "rst": "rst",
    "txt": "text",
    "al": "al",
    "xpp": "xpp",
}


@dataclass(frozen=True)
class CodeView:
    file: str
    commit: str | None
    language: str | None
    first_line: int
    lines: list[str]
    highlight: tuple[int, int]


@dataclass(frozen=True)
class TextView:
    label: str | None
    before: str
    highlight: str
    after: str


def language_for(path: str) -> str | None:
    """A language name for syntax highlighting, from the file extension (``None`` if unknown)."""
    name = path.rsplit("/", 1)[-1]
    if name in ("Dockerfile", "Makefile"):
        return name.lower()
    if "." not in name:
        return None
    return LANGUAGES.get(name.rsplit(".", 1)[-1].lower())


def code_location(locator: dict[str, Any]) -> tuple[str, int, int] | None:
    """(file, start_line, end_line) of a code chunk's locator, else ``None``."""
    file, start, end = locator.get("file"), locator.get("start_line"), locator.get("end_line")
    if not isinstance(file, str) or not file or not isinstance(start, int):
        return None
    if not isinstance(end, int) or end < start:
        end = start
    return file, max(1, start), max(1, end)


def _file_segment(segments: list[Segment], file: str, offset: int) -> Segment | None:
    """The snapshot segment holding ``file`` (the one containing ``offset`` if several)."""
    candidates = [s for s in segments if s.file == file and s.start_line is not None]
    for segment in candidates:
        if segment.start <= offset < segment.end:
            return segment
    return candidates[0] if candidates else None


def _code_body(chunk_text: str) -> str:
    if chunk_text.startswith(FILE_PREFIX):
        return chunk_text.partition("\n")[2]
    return chunk_text


def _offsets_match(markdown: str, chunk_text: str, start: int, end: int) -> bool:
    """The snapshot still holds this chunk (a refresh may have rewritten it since)."""
    if not 0 <= start < end <= len(markdown):
        return False
    return chunk_text.endswith(markdown[start:end])  # the chunk text may carry a "File:" line


def code_view(
    *,
    markdown: str | None,
    segments: list[Segment],
    chunk_text: str,
    start_offset: int,
    end_offset: int,
    location: tuple[str, int, int],
    commit: str | None,
) -> CodeView:
    """The cited file's lines around the chunk, from the snapshot (or the chunk alone)."""
    file, start_line, end_line = location
    segment = None
    if markdown is not None and _offsets_match(markdown, chunk_text, start_offset, end_offset):
        segment = _file_segment(segments, file, start_offset)
    if segment is None or markdown is None or segment.end > len(markdown):
        body = _code_body(chunk_text).split("\n")
        return CodeView(
            file=file,
            commit=commit,
            language=language_for(file),
            first_line=start_line,
            lines=body,
            highlight=(start_line, start_line + len(body) - 1),
        )
    lines = markdown[segment.start : segment.end].split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines = lines[:-1]  # the newline that ends the file is not a line of its own
    first = 1
    if len(lines) > WHOLE_FILE_LINES:
        first = max(1, start_line - WINDOW_LINES)
        last = min(len(lines), end_line + WINDOW_LINES)
        lines = lines[first - 1 : last]
    last_shown = first + len(lines) - 1
    return CodeView(
        file=file,
        commit=commit,
        language=language_for(file),
        first_line=first,
        lines=lines,
        highlight=(min(start_line, last_shown), min(end_line, last_shown)),
    )


def _cut_before(text: str) -> str:
    """Keep the end of ``text``, starting on a line (else word) boundary."""
    if len(text) < CONTEXT_CHARS:
        return text
    newline = text.find("\n")
    if 0 <= newline < len(text) // 2:
        return text[newline + 1 :]
    space = text.find(" ")
    if 0 <= space < len(text) // 2:
        return text[space + 1 :]
    return text


def _cut_after(text: str) -> str:
    """Keep the start of ``text``, ending on a line (else word) boundary."""
    if len(text) < CONTEXT_CHARS:
        return text
    newline = text.rfind("\n")
    if newline >= len(text) // 2:
        return text[:newline]
    space = text.rfind(" ")
    if space >= len(text) // 2:
        return text[:space]
    return text


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def label_for(locator: dict[str, Any]) -> str | None:
    """A short place name: "Page 3", "Pages 3–4", "Section: Install", "01:02–01:45"."""
    label: str | None = None
    pages = [p for p in locator.get("pages") or [] if isinstance(p, int)]
    if pages:
        low, high = min(pages), max(pages)
        label = f"Page {low}" if low == high else f"Pages {low}–{high}"
    elif isinstance(locator.get("start_s"), int | float):
        start = float(locator["start_s"])
        end = locator.get("end_s")
        label = _clock(start)
        if isinstance(end, int | float) and end > start:
            label += f"–{_clock(float(end))}"
    elif isinstance(locator.get("heading"), str) and locator["heading"]:
        label = f"Section: {locator['heading']}"
    file = locator.get("file")
    if isinstance(file, str) and file:
        return f"{file} · {label}" if label else file
    return label


def text_view(
    *,
    markdown: str | None,
    segments: list[Segment],
    chunk_text: str,
    start_offset: int,
    end_offset: int,
    locator: dict[str, Any],
) -> TextView:
    """The chunk as a string with up to ~1,500 characters of context on each side."""
    label = label_for(locator)
    if markdown is None or not _offsets_match(markdown, chunk_text, start_offset, end_offset):
        # The stored text, without a web chunk's ``Section:`` line (D-105): the label names it.
        return TextView(
            label=label, before="", highlight=without_section_header(chunk_text), after=""
        )
    low, high = 0, len(markdown)
    file = locator.get("file")
    mine = [s for s in segments if isinstance(file, str) and file and s.file == file]
    if mine:  # a document inside a linked folder: stay within that file
        low = min(min(s.start for s in mine), start_offset)
        high = max(max(s.end for s in mine), end_offset)
    before = markdown[max(low, start_offset - CONTEXT_CHARS) : start_offset]
    after = markdown[end_offset : min(high, end_offset + CONTEXT_CHARS)]
    if start_offset - CONTEXT_CHARS > low:
        before = _cut_before(before)
    if end_offset + CONTEXT_CHARS < high:
        after = _cut_after(after)
    return TextView(
        label=label,
        before=before,
        highlight=markdown[start_offset:end_offset],
        after=after,
    )
