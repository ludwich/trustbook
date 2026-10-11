"""Heading-aware chunker for documents and web pages (SPEC §7.2).

Target 500 tokens, maximum 800, 80 tokens of overlap between neighbouring chunks of the same
section. Chunks end at sentence (or line) boundaries; a sentence is only split when it alone is
longer than the maximum. A heading starts a new chunk unless the current one is still small.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.chunking.tokens import TokenCounter
from app.chunking.units import Unit, split_units

TARGET_TOKENS = 500
MAX_TOKENS = 800
OVERLAP_TOKENS = 80
# A heading closes the current chunk only once it has this many tokens (tiny sections merge).
MIN_SECTION_TOKENS = 100

Locate = Callable[[int, int], dict[str, Any]]


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before it gets an id: ``text`` is what is embedded and shown to the model."""

    text: str
    start: int
    end: int
    locator: dict[str, Any]
    token_count: int


def _overlap(previous: list[Unit], limit: int, *, align_lines: bool = False) -> list[Unit]:
    tail: list[Unit] = []
    total = 0
    for unit in reversed(previous):
        if unit.heading is not None or total + unit.tokens > limit:
            break
        tail.insert(0, unit)
        total += unit.tokens
    if align_lines:  # the next chunk starts at a line start too
        while tail and not tail[0].line_start:
            tail.pop(0)
    # Never repeat the whole previous chunk.
    return tail if len(tail) < len(previous) else []


def _line_break(current: list[Unit], minimum: int) -> int | None:
    """Where to end ``current`` on a line end: the index of the unit to start the next chunk with.

    The latest unit that starts a line with at least ``minimum`` tokens before it; one that also
    starts a sentence or paragraph ("clean") is preferred.
    """
    clean: int | None = None
    any_start: int | None = None
    head = 0
    for k in range(1, len(current)):
        head += current[k - 1].tokens
        if current[k].line_start and head >= minimum:
            any_start = k
            if current[k].clean_start:
                clean = k
    return clean if clean is not None else any_start


def pack_units(
    units: list[Unit],
    *,
    target: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap: int = OVERLAP_TOKENS,
    min_section: int = MIN_SECTION_TOKENS,
    align_lines: bool = False,
) -> list[list[Unit]]:
    """Group units into chunks (each a list of consecutive units, possibly overlapping).

    ``align_lines`` (units from ``split_units(lines=True)``): a chunk only ends where the next
    unit starts a line, so it holds each of its lines in full. When the target is reached mid-line
    the chunk ends at an earlier line end (preferably a sentence end) and the rest of the line
    starts the next chunk. Only a single line longer than the maximum is cut inside.
    """
    groups: list[list[Unit]] = []
    current: list[Unit] = []
    tokens = 0
    for unit in units:
        if current and unit.heading is not None and tokens >= min_section:
            groups.append(current)
            current, tokens = [], 0
        elif current and tokens + unit.tokens > target:
            small = tokens < target // 2 and tokens + unit.tokens <= max_tokens
            if not small:
                head, carry = current, []
                if align_lines and not unit.line_start:
                    cut = _line_break(current, target // 2)
                    if cut is None and tokens + unit.tokens <= max_tokens:
                        current.append(unit)  # wait for the next line start
                        tokens += unit.tokens
                        continue
                    if cut is None:  # a line end anywhere, if the rest then fits a chunk
                        cut = _line_break(current, 0)
                        rest = sum(u.tokens for u in current[cut:]) if cut is not None else 0
                        if rest + unit.tokens > max_tokens:
                            cut = None  # one line over the maximum: cut inside it, here
                    if cut is not None:
                        head, carry = current[:cut], current[cut:]
                groups.append(head)
                current = _overlap(head, overlap, align_lines=align_lines) + carry
                tokens = sum(u.tokens for u in current)
                if tokens + unit.tokens > max_tokens:
                    current, tokens = carry, sum(u.tokens for u in carry)
                    if carry and tokens + unit.tokens > max_tokens:
                        groups.append(carry)
                        current, tokens = [], 0
        current.append(unit)
        tokens += unit.tokens
    if current:
        groups.append(current)
    return groups


def chunk_document(
    text: str,
    start: int,
    end: int,
    counter: TokenCounter,
    locate: Locate,
    *,
    target: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap: int = OVERLAP_TOKENS,
    prefix: str = "",
    align_lines: bool = False,
) -> list[ChunkDraft]:
    """Chunk ``text[start:end]``; ``locate(start, end)`` builds each chunk's locator.

    ``align_lines``: chunks hold whole lines (for files, whose locators name lines).
    """
    units = split_units(text, start, end, counter, max_tokens=max_tokens, lines=align_lines)
    drafts: list[ChunkDraft] = []
    groups = pack_units(
        units, target=target, max_tokens=max_tokens, overlap=overlap, align_lines=align_lines
    )
    for group in groups:
        c_start, c_end = group[0].start, group[-1].end
        body = text[c_start:c_end]
        drafts.append(
            ChunkDraft(
                text=prefix + body,
                start=c_start,
                end=c_end,
                locator=locate(c_start, c_end),
                token_count=counter.count(prefix + body),
            )
        )
    return drafts
