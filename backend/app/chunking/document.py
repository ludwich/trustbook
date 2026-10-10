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


def _overlap(previous: list[Unit], limit: int) -> list[Unit]:
    tail: list[Unit] = []
    total = 0
    for unit in reversed(previous):
        if unit.heading is not None or total + unit.tokens > limit:
            break
        tail.insert(0, unit)
        total += unit.tokens
    # Never repeat the whole previous chunk.
    return tail if len(tail) < len(previous) else []


def pack_units(
    units: list[Unit],
    *,
    target: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap: int = OVERLAP_TOKENS,
    min_section: int = MIN_SECTION_TOKENS,
) -> list[list[Unit]]:
    """Group units into chunks (each a list of consecutive units, possibly overlapping)."""
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
                groups.append(current)
                current = _overlap(current, overlap)
                tokens = sum(u.tokens for u in current)
                if tokens + unit.tokens > max_tokens:
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
) -> list[ChunkDraft]:
    """Chunk ``text[start:end]``; ``locate(start, end)`` builds each chunk's locator."""
    units = split_units(text, start, end, counter, max_tokens=max_tokens)
    drafts: list[ChunkDraft] = []
    for group in pack_units(units, target=target, max_tokens=max_tokens, overlap=overlap):
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
