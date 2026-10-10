"""Fallback code chunker (SPEC §7.3, DECISIONS D-023).

Splits a file into blocks at blank lines, at top-level ``procedure``/``trigger`` keywords and after
a top-level closing brace, then packs consecutive blocks up to a target of 400 tokens (maximum
800). Blocks longer than the maximum are split between lines. Locator = file + line range.
"""

from __future__ import annotations

import re

from app.chunking.document import ChunkDraft
from app.chunking.tokens import TokenCounter

TARGET_TOKENS = 400
MAX_TOKENS = 800
_KEYWORD_START = re.compile(
    r"^(?:(?:local|internal|protected|public|private|global|static|event)\s+)*"
    r"(?:procedure|trigger)\b",
    re.IGNORECASE,
)


def _line_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """(start, end) offsets of each line in ``text[start:end]`` (end excludes the newline)."""
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor <= end:
        newline = text.find("\n", cursor, end)
        stop = end if newline == -1 else newline
        spans.append((cursor, stop))
        if newline == -1:
            break
        cursor = newline + 1
    return spans


def _blocks(text: str, lines: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Line-index ranges [first, last] of non-blank blocks."""
    blocks: list[tuple[int, int]] = []
    first: int | None = None
    for i, (s, e) in enumerate(lines):
        line = text[s:e]
        blank = not line.strip()
        if blank:
            if first is not None:
                blocks.append((first, i - 1))
                first = None
            continue
        if first is not None and _KEYWORD_START.match(line):
            blocks.append((first, i - 1))
            first = None
        if first is None:
            first = i
        if line.rstrip() in ("}", "};", "end;", "END;"):
            blocks.append((first, i))
            first = None
    if first is not None:
        blocks.append((first, len(lines) - 1))
    return blocks


def chunk_code(
    text: str,
    start: int,
    end: int,
    path: str,
    counter: TokenCounter,
    *,
    target: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
) -> list[ChunkDraft]:
    """Chunk the file at ``text[start:end]``; line numbers are 1-based within the file."""
    target = min(target, max_tokens)
    lines = _line_spans(text, start, end)
    line_tokens = [counter.count(text[s:e]) + 1 for s, e in lines]

    pieces: list[tuple[int, int, int]] = []  # (first line, last line, tokens)
    for first, last in _blocks(text, lines):
        tokens = sum(line_tokens[first : last + 1])
        if tokens <= max_tokens:
            pieces.append((first, last, tokens))
            continue
        cursor, running = first, 0
        for i in range(first, last + 1):
            if running and running + line_tokens[i] > max_tokens:
                pieces.append((cursor, i - 1, running))
                cursor, running = i, 0
            running += line_tokens[i]
        pieces.append((cursor, last, running))

    groups: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    tokens = 0
    for first, last, piece_tokens in pieces:
        if current is not None:
            fits_target = tokens + piece_tokens <= target
            small = tokens < target // 2 and tokens + piece_tokens <= max_tokens
            if fits_target or small:
                current = (current[0], last)
                tokens += piece_tokens
                continue
            groups.append(current)
        current, tokens = (first, last), piece_tokens
    if current is not None:
        groups.append(current)

    drafts: list[ChunkDraft] = []
    for first, last in groups:
        c_start, c_end = lines[first][0], lines[last][1]
        prefix = f"File: {path} (lines {first + 1}-{last + 1})\n"
        body = prefix + text[c_start:c_end]
        locator = {"file": path, "start_line": first + 1, "end_line": last + 1, "symbol": None}
        drafts.append(ChunkDraft(body, c_start, c_end, locator, counter.count(body)))
    return drafts
