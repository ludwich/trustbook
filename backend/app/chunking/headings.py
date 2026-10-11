"""Section headings of a text document file and the heading path at any offset (D-068).

A chunk of a long changelog or manual says little on its own: ``officially added support for
CPython 3.12`` belongs to whatever release heading is above it, often in the previous chunk
(ref-near-03). Document files of folder sources therefore carry their heading path in the
chunk header (:func:`app.chunking.header.document_header`).

Three heading forms are recognised, outside fenced code blocks (`````` ``` `````` / ``~~~``):

- **ATX**: ``# Title`` … ``###### Title`` at column 0; the level is the number of ``#``.
- **Underlined (setext / reStructuredText)**: a title line at column 0, after a blank line (or at
  the start of the file), followed by a line of one repeated adornment character, at least 3 long.
- **Over- and underlined (reStructuredText)**: the same adornment line above and below the title
  (the title may be indented), after a blank line or at the start.

Markdown files (and documents converted to Markdown) only underline with ``=`` (level 1) and
``-`` (level 2), as CommonMark defines; YAML front matter at the top is skipped. Other text files
(``.rst``, ``.txt``, …) accept every reStructuredText adornment character, and their levels follow
the order in which each style first appears in the file (docutils' rule). An adornment other than
``=`` / ``-`` must be at least as long as its title, as reStructuredText requires, so a ``***``
rule after a line of text is not a heading. A title line that is a list item, table row, quote,
directive or code is never a heading.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

# reStructuredText adornment characters (any printable 7-bit ASCII punctuation).
_RST_ADORNMENT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_MARKDOWN_ADORNMENT = "=-"
_ATX = re.compile(r"(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*")
FENCE = re.compile(r" {0,3}(```|~~~)")
# A line that is something else than a title: bullet item, table row, quote, directive, field.
_NOT_TITLE = re.compile(r"[-*+][ \t]|[|>]|\.\.[ \t]|:[A-Za-z]|```|~~~")
MIN_ADORNMENT = 3
SEPARATOR = " › "
MAX_PATH_CHARS = 200


@dataclass(frozen=True)
class Heading:
    """A heading block: ``text[start:end]`` holds its line(s), adornments included."""

    start: int
    end: int
    title: str
    level: int


def _adornment(line: str, chars: str) -> str | None:
    """The adornment character of ``line`` (one character repeated, at least 3), else None."""
    stripped = line.rstrip()
    if len(stripped) < MIN_ADORNMENT or stripped[0] not in chars:
        return None
    return stripped[0] if stripped == stripped[0] * len(stripped) else None


def _title_line(line: str, *, indented: bool = False) -> bool:
    if not line.strip() or (line[0] in " \t" and not indented):
        return False
    if _adornment(line.strip(), _RST_ADORNMENT) is not None:
        return False
    return _NOT_TITLE.match(line.strip()) is None


def _lines(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        newline = text.find("\n", cursor, end)
        stop = end if newline == -1 else newline
        spans.append((cursor, stop))
        cursor = stop + 1
    return spans


def _front_matter_end(text: str, lines: list[tuple[int, int]]) -> int:
    """Index of the first line after YAML front matter (0 when there is none)."""
    if not lines or text[lines[0][0] : lines[0][1]].rstrip() != "---":
        return 0
    for i in range(1, len(lines)):
        if text[lines[i][0] : lines[i][1]].rstrip() in ("---", "..."):
            return i + 1
    return 0


def find_headings(text: str, start: int, end: int, *, markdown: bool) -> list[Heading]:
    """The headings of the document at ``text[start:end]`` in order (see the module docstring)."""
    lines = _lines(text, start, end)
    chars = _MARKDOWN_ADORNMENT if markdown else _RST_ADORNMENT
    styles: dict[tuple[str, bool], int] = {}  # underline styles of a non-Markdown file → level
    found: list[Heading] = []
    in_fence = False

    def line(i: int) -> str:
        return text[lines[i][0] : lines[i][1]]

    first = _front_matter_end(text, lines) if markdown else 0

    def blank_before(i: int) -> bool:
        return i == first or not line(i - 1).strip()

    def level(char: str, overlined: bool) -> int:
        if markdown:
            return 1 if char == "=" else 2
        return styles.setdefault((char, overlined), len(styles) + 1)

    def heading_at(i: int) -> tuple[Heading, int] | None:
        """The heading starting at line ``i`` and the number of lines it takes."""
        current = line(i)
        atx = _ATX.fullmatch(current)
        if atx:
            return Heading(lines[i][0], lines[i][1], atx.group(2).strip(), len(atx.group(1))), 1
        if not blank_before(i):
            return None
        over = None if markdown else _adornment(current, chars)
        if over is not None and i + 2 < len(lines):
            title = line(i + 1).strip()
            if (
                _adornment(line(i + 2), chars) == over
                and _title_line(line(i + 1), indented=True)
                and len(line(i + 2).rstrip()) >= len(title)
                and len(current.rstrip()) >= len(title)
            ):
                return Heading(lines[i][0], lines[i + 2][1], title, level(over, True)), 3
        if i + 1 < len(lines) and _title_line(current):
            under = _adornment(line(i + 1), chars)
            title = current.strip()
            if under is not None and (under in "=-" or len(line(i + 1).rstrip()) >= len(title)):
                return Heading(lines[i][0], lines[i + 1][1], title, level(under, False)), 2
        return None

    i = first
    while i < len(lines):
        if in_fence:  # only the closing fence matters inside a code block
            in_fence = FENCE.match(line(i)) is None
            i += 1
            continue
        found_here = heading_at(i)
        if found_here is not None:
            found.append(found_here[0])
            i += found_here[1]
            continue
        in_fence = FENCE.match(line(i)) is not None  # an adornment of ``~`` is tried first
        i += 1
    return found


class HeadingPaths:
    """The heading path (outermost first) in effect at any offset of one document."""

    def __init__(self, headings: list[Heading]) -> None:
        self.headings = headings
        self._starts = [h.start for h in headings]
        self._paths: list[tuple[Heading, ...]] = []
        stack: list[Heading] = []
        for heading in headings:
            while stack and stack[-1].level >= heading.level:
                stack.pop()
            stack.append(heading)
            self._paths.append(tuple(stack))

    def at(self, offset: int) -> tuple[Heading, ...]:
        """The headings above ``offset`` (a heading's own lines are under it)."""
        index = bisect_right(self._starts, offset) - 1
        return self._paths[index] if index >= 0 else ()

    def sections(self, start: int, end: int) -> list[tuple[int, Heading | None]]:
        """``(characters of text[start:end] in it, heading)`` per section the span overlaps."""
        bounds = [*self._starts, end]
        spans: list[tuple[int, Heading | None]] = []
        first = self._starts[0] if self._starts else end
        if start < first:
            spans.append((min(end, first) - start, None))
        for k, heading in enumerate(self.headings):
            size = min(end, bounds[k + 1]) - max(start, heading.start)
            if size > 0:
                spans.append((size, heading))
        return spans


def path_text(path: tuple[Heading, ...], max_chars: int = MAX_PATH_CHARS) -> str:
    """``Outer › Inner`` with whitespace collapsed; outer headings give way to the inner ones
    when the path is longer than ``max_chars``."""
    titles = [" ".join(h.title.split()) for h in path]
    while len(titles) > 1 and len(SEPARATOR.join(titles)) > max_chars:
        titles.pop(0)
    return SEPARATOR.join(titles)
