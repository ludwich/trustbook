"""Heading-aware chunker for documents and web pages (SPEC §7.2).

Target 500 tokens, maximum 800, 80 tokens of overlap between neighbouring chunks of the same
section. Chunks end at sentence (or line) boundaries; a sentence is only split when it alone is
longer than the maximum. With the document's headings (document files, web pages, pasted and
uploaded text) every heading starts a new chunk (D-104); without them a heading starts a new chunk
unless the current one is still small.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.chunking.headings import FENCE, Heading
from app.chunking.structure import Structure
from app.chunking.tokens import TokenCounter
from app.chunking.units import Unit, split_units

TARGET_TOKENS = 500
MAX_TOKENS = 800
OVERLAP_TOKENS = 80
# Without the document's headings, a heading closes the current chunk only once it has this many
# tokens (tiny sections merge).
MIN_SECTION_TOKENS = 100

Locate = Callable[[int, int], dict[str, Any]]
# A chunk's first line for its body ``text[start:end]`` (a folder file's ``File:`` line, a web
# chunk's ``Section:`` line); ``""`` for none.
Header = Callable[[int, int], str]
# A chunk's parent for its body ``text[start:end]``: ``(parent key, structure)`` (D-077).
Parent = Callable[[int, int], tuple[str | None, Structure | None]]


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before it gets an id: ``text`` is what is embedded and shown to the model.

    ``parent_key`` / ``structure``: the chunk's parent (D-077), ``None`` when it has none. They
    never change ``text`` or ``locator``: parent data is not citable.
    """

    text: str
    start: int
    end: int
    locator: dict[str, Any]
    token_count: int
    parent_key: str | None = None
    structure: Structure | None = None


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
    split_headings: bool = False,
) -> list[list[Unit]]:
    """Group units into chunks (each a list of consecutive units, possibly overlapping).

    ``align_lines`` (units from ``split_units(lines=True)``): a chunk only ends where the next
    unit starts a line, so it holds each of its lines in full. When the target is reached mid-line
    the chunk ends at an earlier line end (preferably a sentence end) and the rest of the line
    starts the next chunk. Only a single line longer than the maximum is cut inside.

    Without ``split_headings`` a section smaller than ``min_section`` joins the next one.

    ``split_headings`` (document files, web pages, pasted and uploaded text, D-104): every heading
    starts a new chunk, so each chunk holds the text of one section only and its section label
    is true for all of it. The one exception is a heading with no text of its own (the next line
    is another heading, as in "Usage" right above "Install"): it stays with the next heading, and
    the chunk is labelled by the last heading, the one its text is under. Text before the first
    heading is a chunk of its own. A small section is never merged with another one.
    """
    groups: list[list[Unit]] = []
    current: list[Unit] = []
    tokens = 0

    def heading_closes() -> bool:
        """Whether a heading closes ``current`` before it."""
        if split_headings:  # unless everything so far is headings without text (D-104)
            return any(u.heading is None for u in current)
        return tokens >= min_section

    for unit in units:
        if current and unit.heading is not None and heading_closes():
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


def _whole_lines(text: str, start: int, end: int, lo: int, hi: int) -> tuple[int, int]:
    """``(start, end)`` widened to the start and end of their lines when only whitespace is
    between (the indentation of a first line, trailing spaces), never beyond ``lo``/``hi``."""
    line_start = text.rfind("\n", lo, start) + 1 or lo
    if not text[line_start:start].strip():
        start = line_start
    line_end = text.find("\n", end, hi)
    line_end = hi if line_end == -1 else line_end
    if not text[end:line_end].strip():
        end = line_end
    return start, end


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
    header: Header | None = None,
    parent: Parent | None = None,
    headings: Sequence[Heading] | None = None,
    fence: re.Pattern[str] = FENCE,
    align_lines: bool = False,
) -> list[ChunkDraft]:
    """Chunk ``text[start:end]``; ``locate(start, end)`` builds each chunk's locator.

    ``align_lines``: chunks hold whole lines (for files, whose locators name lines), indentation
    of the first line included. ``header(start, end)``: a first line naming where the body is
    (instead of the fixed ``prefix``; ``""``: no first line). ``parent(start, end)``: the
    chunk's parent key and structure (D-077; none without it). ``headings``: the document's
    headings (:func:`app.chunking.headings.find_headings`) instead of the Markdown ``#`` lines,
    with ``fence`` the lines that open or close a code block in their syntax; every heading then
    starts a chunk (D-104).
    """
    units = split_units(
        text,
        start,
        end,
        counter,
        max_tokens=max_tokens,
        lines=align_lines,
        headings=headings,
        fence=fence,
    )
    drafts: list[ChunkDraft] = []
    groups = pack_units(
        units,
        target=target,
        max_tokens=max_tokens,
        overlap=overlap,
        align_lines=align_lines,
        split_headings=headings is not None,
    )
    for group in groups:
        c_start, c_end = group[0].start, group[-1].end
        if align_lines:
            c_start, c_end = _whole_lines(text, c_start, c_end, start, end)
        body = text[c_start:c_end]
        line = header(c_start, c_end) if header is not None else ""
        first = f"{line}\n" if line else prefix
        parent_key, structure = parent(c_start, c_end) if parent is not None else (None, None)
        drafts.append(
            ChunkDraft(
                text=first + body,
                start=c_start,
                end=c_end,
                locator=locate(c_start, c_end),
                token_count=counter.count(first + body),
                parent_key=parent_key,
                structure=structure,
            )
        )
    return drafts
