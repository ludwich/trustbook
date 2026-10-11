"""Split text into small units (headings, sentences, lines) that chunkers pack into chunks.

A unit never crosses a paragraph. Prose is split into sentences; tables, lists and code blocks
into lines. A unit longer than the chunk maximum is split at whitespace as a last resort.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace

from app.chunking.tokens import TokenCounter

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_HEADING_LINE = re.compile(r"#{1,6}[ \t]+(.+?)[ \t#]*$")
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s)")
# A unit ending like this ends a sentence (or introduces a list): a clean place to cut after it.
_CLEAN_END = re.compile(r"[.!?…:][\"'”’)\]]*$")
_STRUCTURED = re.compile(r"^(\s*[|>]|```|~~~| {4}|\t|\s*[-*+] |\s*\d+[.)] )")
_ABBREVIATIONS = frozenset(
    "e.g i.e etc vs mr mrs ms dr prof sr jr st no fig eq approx inc ltd co cf al".split()
)


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    tokens: int
    heading: str | None = None  # set when the unit is a heading line
    # Only set by ``split_units(lines=True)``: the unit starts a line (a chunk may start here and
    # the chunk before it then ends at a line end), and "clean" if a sentence or paragraph also
    # ends right before it (the preferred place to cut).
    line_start: bool = False
    clean_start: bool = False


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _paragraphs(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    cursor = start
    for match in _PARAGRAPH_BREAK.finditer(text, start, end):
        yield cursor, match.start()
        cursor = match.end()
    yield cursor, end


def _lines(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    cursor = start
    while cursor < end:
        newline = text.find("\n", cursor, end)
        stop = end if newline == -1 else newline
        yield cursor, stop
        cursor = stop + 1


def _is_abbreviation(text: str, period: int) -> bool:
    word_start = period
    while word_start > 0 and (text[word_start - 1].isalpha() or text[word_start - 1] == "."):
        word_start -= 1
    word = text[word_start:period].lower()
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _sentences(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    cursor = start
    for match in _SENTENCE_END.finditer(text, start, end):
        if text[match.start()] == "." and _is_abbreviation(text, match.start()):
            continue
        yield cursor, match.end()
        cursor = match.end()
    yield cursor, end


def _hard_split(
    text: str, start: int, end: int, counter: TokenCounter, max_tokens: int
) -> list[Unit]:
    """Split an over-long unit at whitespace into pieces of at most ``max_tokens``."""
    tokens = counter.count(text[start:end])
    if tokens <= max_tokens or end - start < 2:
        return [Unit(start, end, tokens)]
    pieces = math.ceil(tokens / max_tokens)
    size = math.ceil((end - start) / pieces)
    units: list[Unit] = []
    cursor = start
    while cursor < end:
        cut = min(end, cursor + size)
        if cut < end:
            space = text.rfind(" ", cursor + size // 2, cut)
            newline = text.rfind("\n", cursor + size // 2, cut)
            best = max(space, newline)
            if best > cursor:
                cut = best
        piece_start, piece_end = _trimmed(text, cursor, cut)
        if piece_end > piece_start:
            units.extend(_hard_split(text, piece_start, piece_end, counter, max_tokens))
        cursor = cut
    return units


def _starts_line(text: str, start: int, offset: int) -> bool:
    line = text.rfind("\n", start, offset) + 1 or start
    return not text[line:offset].strip()


def _mark_lines(text: str, start: int, units: list[Unit], rows: set[int]) -> list[Unit]:
    """Set ``line_start`` / ``clean_start`` on ``units`` (already split at line ends).

    ``rows``: starts of table rows, list items and code lines, each a clean place to cut.
    """
    marked: list[Unit] = []
    previous: Unit | None = None
    for unit in units:
        line_start = _starts_line(text, start, unit.start)
        clean = line_start and (
            previous is None
            or unit.heading is not None
            or unit.start in rows
            or text.count("\n", previous.end, unit.start) > 1  # a new paragraph
            or bool(_CLEAN_END.search(text, previous.start, previous.end))
        )
        # The line break before a unit is a token too (as in the code chunkers), so chunks of
        # line-split text do not grow past their target.
        tokens = unit.tokens + (1 if line_start and previous is not None else 0)
        marked.append(replace(unit, tokens=tokens, line_start=line_start, clean_start=clean))
        previous = unit
    return marked


def split_units(
    text: str,
    start: int,
    end: int,
    counter: TokenCounter,
    *,
    max_tokens: int,
    lines: bool = False,
) -> list[Unit]:
    """Units covering the non-blank text of ``text[start:end]`` in order.

    ``lines``: prose is also split at line ends and every unit says whether it starts a line, so
    chunks of a file can end on whole lines (a line locator then names only lines the chunk holds
    in full, D-056).
    """
    units: list[Unit] = []
    rows: set[int] = set()
    in_fence = False
    for p_start, p_end in _paragraphs(text, start, end):
        p_start, p_end = _trimmed(text, p_start, p_end)
        if p_end <= p_start:
            continue
        paragraph = text[p_start:p_end]
        fence_marks = paragraph.count("```") + paragraph.count("~~~")
        structured = in_fence or fence_marks > 0 or bool(_STRUCTURED.match(paragraph))
        if fence_marks % 2 == 1:
            in_fence = not in_fence
        body_start = p_start
        if not structured:
            first_end = text.find("\n", p_start, p_end)
            first_end = p_end if first_end == -1 else first_end
            heading = _HEADING_LINE.fullmatch(text, p_start, first_end)
            if heading:
                tokens = counter.count(text[p_start:first_end])
                units.append(Unit(p_start, first_end, tokens, heading=heading.group(1).strip()))
                body_start = first_end
        pieces: Iterable[tuple[int, int]] = (
            _lines(text, body_start, p_end) if structured else _sentences(text, body_start, p_end)
        )
        if lines and not structured:
            pieces = [line for s, e in pieces for line in _lines(text, s, e)]
        for s_start, s_end in pieces:
            s_start, s_end = _trimmed(text, s_start, s_end)
            if s_end > s_start:
                if structured:
                    rows.add(s_start)
                units.extend(_hard_split(text, s_start, s_end, counter, max_tokens))
    return _mark_lines(text, start, units, rows) if lines else units
