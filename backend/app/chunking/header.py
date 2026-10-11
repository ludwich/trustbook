"""The ``File: …`` first line of a code chunk, and the parts of it that are not code.

``File: <path> · <symbol> (lines a-b)``; a group of a split data literal also names its part,
``File: <path> · _codes (part 3) (lines a-b)`` (D-056). The part number says where the chunk is,
not what the code holds: full-text search drops it with the line numbers (D-051), and the
deterministic code checks (grounding layer 5) do not accept it as a number the code states.
"""

from __future__ import annotations

import re

# ``(part N)`` of a split data literal's header, which is always the chunk's first line.
_PART_IN_HEADER = re.compile(r"\A(File: [^\n]*?) \(part (\d+)\)(?= \(lines \d+-\d+\))")


def header_line(
    path: str, symbol: str | None, first_line: int, last_line: int, part: int | None = None
) -> str:
    """``File: path · symbol (lines a-b)``: the first line of every code chunk.

    Each group of a split data literal says which part it is:
    ``File: path · _codes (part 3) (lines a-b)``.
    """
    where = f"{path} · {symbol}" if symbol else path
    if symbol and part is not None:
        where += f" (part {part})"
    return f"File: {where} (lines {first_line}-{last_line})"


def header_part(chunk_text: str) -> int | None:
    """The part number of a split data literal's chunk (``None`` for every other chunk)."""
    match = _PART_IN_HEADER.match(chunk_text)
    return int(match.group(2)) if match else None


def without_part(chunk_text: str) -> str:
    """``chunk_text`` without the ``(part N)`` of its header; every other chunk unchanged."""
    return _PART_IN_HEADER.sub(r"\1", chunk_text, count=1)
