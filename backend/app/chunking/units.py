"""Split text into small units (headings, sentences, lines) that chunkers pack into chunks.

A unit never crosses a paragraph. Prose is split into sentences; tables, lists and code blocks
into lines. A unit longer than the chunk maximum is split at whitespace as a last resort.
"""

from __future__ import annotations

import bisect
import math
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace

from app.chunking.headings import FENCE, Heading
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


def _markdown_heading(text: str, start: int, end: int) -> tuple[str, int] | None:
    """``(title, end)`` of a Markdown ``#`` heading on the first line of a paragraph."""
    first_end = text.find("\n", start, end)
    first_end = end if first_end == -1 else first_end
    heading = _HEADING_LINE.fullmatch(text, start, first_end)
    return (heading.group(1).strip(), first_end) if heading else None


def _fence_lines(
    text: str,
    start: int,
    end: int,
    headings: Sequence[Heading],
    starts: Sequence[int],
    fence: re.Pattern[str],
) -> int:
    """How many lines of ``text[start:end]`` open or close a fenced code block (``fence``: the
    file's syntax, :data:`app.chunking.headings.FENCES`), not counting a heading's adornment
    lines (``headings`` in order, ``starts`` their offsets)."""
    count = 0
    cursor = start
    while cursor < end:
        newline = text.find("\n", cursor, end)
        stop = end if newline == -1 else newline
        k = bisect.bisect_right(starts, cursor) - 1
        inside = k >= 0 and cursor < headings[k].end
        if not inside and fence.match(text, cursor, stop):
            count += 1
        cursor = stop + 1
    return count


Region = tuple[int, int, tuple[str, int] | None]


def _heading_regions(
    text: str,
    start: int,
    p_start: int,
    p_end: int,
    headings: Sequence[Heading],
    starts: Sequence[int],
) -> list[Region]:
    """A paragraph cut at the document's heading blocks: ``(start, end, heading)`` per region,
    where ``heading`` is ``(title, end of its block)`` when the region starts with one."""
    first_line = text.rfind("\n", start, p_start) + 1 or start
    index = bisect.bisect_left(starts, first_line)
    regions: list[Region] = []
    cursor = p_start
    pending: tuple[str, int] | None = None
    for heading in headings[index:]:
        if heading.start >= p_end:
            break
        h_start = max(heading.start, p_start)
        if h_start > cursor or pending is not None:
            regions.append((cursor, h_start, pending))
        cursor, pending = h_start, (heading.title, heading.end)
    regions.append((cursor, p_end, pending))
    return [(s, e, h) for s, e, h in regions if e > s]


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
    headings: Sequence[Heading] | None = None,
    fence: re.Pattern[str] = FENCE,
) -> list[Unit]:
    """Units covering the non-blank text of ``text[start:end]`` in order.

    ``lines``: prose is also split at line ends and every unit says whether it starts a line, so
    chunks of a file can end on whole lines (a line locator then names only lines the chunk holds
    in full, D-056).

    ``headings`` (a document file's, D-068): each heading block (an underlined title is two or
    three lines) is one heading unit, in place of the Markdown ``#`` lines found here otherwise;
    ``fence``: the lines that open or close a code block in the file's syntax.
    """
    units: list[Unit] = []
    rows: set[int] = set()
    in_fence = False
    ordered = sorted(headings, key=lambda h: h.start) if headings is not None else None
    starts = [h.start for h in ordered] if ordered is not None else []
    for p_start, p_end in _paragraphs(text, start, end):
        p_start, p_end = _trimmed(text, p_start, p_end)
        if p_end <= p_start:
            continue
        paragraph = text[p_start:p_end]
        if ordered is None:
            fence_marks = paragraph.count("```") + paragraph.count("~~~")
        else:  # fence lines, not a ``~~~~~`` heading adornment (D-068)
            fence_marks = _fence_lines(text, p_start, p_end, ordered, starts, fence)
        fenced = in_fence or fence_marks > 0
        if fence_marks % 2 == 1:
            in_fence = not in_fence
        # Without the document's headings: Markdown ``#`` lines, the paragraph's kind for all of it.
        whole = fenced or bool(_STRUCTURED.match(paragraph))
        if ordered is None:
            regions = [(p_start, p_end, None if whole else _markdown_heading(text, p_start, p_end))]
        else:
            regions = _heading_regions(text, start, p_start, p_end, ordered, starts)
        for r_start, r_end, heading in regions:
            if heading is not None:
                h_end = min(heading[1], r_end)
                tokens = counter.count(text[r_start:h_end])
                units.append(Unit(r_start, h_end, tokens, heading=heading[0]))
                r_start = _trimmed(text, h_end, r_end)[0]
            if r_end <= r_start:
                continue
            structured = (
                whole if ordered is None else fenced or bool(_STRUCTURED.match(text[r_start:r_end]))
            )
            pieces: Iterable[tuple[int, int]] = (
                _lines(text, r_start, r_end) if structured else _sentences(text, r_start, r_end)
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
