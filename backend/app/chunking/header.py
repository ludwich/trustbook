"""The ``File: …`` first line of a folder file's chunk, and the parts of it that are not content.

Code: ``File: <path> · <symbol> (lines a-b)``; a group of a split data literal also names its part,
``File: <path> · _codes (part 3) (lines a-b)`` (D-056). The part number says where the chunk is,
not what the code holds: full-text search drops it with the line numbers (D-051), and the
deterministic code checks (grounding layer 5) do not accept it as a number the code states.

Document files (``.md``, ``.rst``, ``.txt``, …): ``File: <path> · <heading path> (lines a-b)``,
``File: <path> (lines a-b)`` before the first heading, and without ``(lines a-b)`` for a paged
document (D-068). The heading path (``Release History › 2.32.0 (2024-05-20)``) is the section the
chunk's first line is under: context the model, the grounding checks and the judge may read, while
line numbers stay on the body lines.
"""

from __future__ import annotations

import re

# ``(part N)`` of a split data literal's header, which is always the chunk's first line.
_PART_IN_HEADER = re.compile(r"\A(File: [^\n]*?) \(part (\d+)\)(?= \(lines \d+-\d+\))")
_LINES_SUFFIX = re.compile(r" \(lines \d+-\d+\)\Z")
FILE_PREFIX = "File: "
CONTEXT_SEPARATOR = " · "


def header_line(
    path: str, symbol: str | None, first_line: int, last_line: int, part: int | None = None
) -> str:
    """``File: path · symbol (lines a-b)``: the first line of every code chunk.

    Each group of a split data literal says which part it is:
    ``File: path · _codes (part 3) (lines a-b)``.
    """
    where = f"{path}{CONTEXT_SEPARATOR}{symbol}" if symbol else path
    if symbol and part is not None:
        where += f" (part {part})"
    return f"File: {where} (lines {first_line}-{last_line})"


def document_header(path: str, heading_path: str, lines: tuple[int, int] | None) -> str:
    """``File: path · heading path (lines a-b)``: the first line of a document file's chunk.

    No heading (text before the first one) leaves out `` · …``; a paged document (located by
    page, not line) leaves out ``(lines a-b)``.
    """
    heading_path = " ".join(heading_path.split())
    where = f"{path}{CONTEXT_SEPARATOR}{heading_path}" if heading_path else path
    return f"{FILE_PREFIX}{where}" + (f" (lines {lines[0]}-{lines[1]})" if lines else "")


def header_context(chunk_text: str, path: str) -> str:
    """What a chunk's ``File: path · …`` header says after the path, without ``(lines a-b)``.

    The heading path of a document file's chunk, the symbol of a code chunk, ``""`` when the
    header names only the file (or there is no header).
    """
    first = chunk_text.split("\n", 1)[0]
    prefix = f"{FILE_PREFIX}{path}{CONTEXT_SEPARATOR}"
    if not first.startswith(prefix):
        return ""
    return _LINES_SUFFIX.sub("", first[len(prefix) :]).strip()


def header_part(chunk_text: str) -> int | None:
    """The part number of a split data literal's chunk (``None`` for every other chunk)."""
    match = _PART_IN_HEADER.match(chunk_text)
    return int(match.group(2)) if match else None


def without_part(chunk_text: str) -> str:
    """``chunk_text`` without the ``(part N)`` of its header; every other chunk unchanged."""
    return _PART_IN_HEADER.sub(r"\1", chunk_text, count=1)
