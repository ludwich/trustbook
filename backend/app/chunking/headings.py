"""Section headings of a text document file and the heading path at any offset (D-068).

A chunk of a long changelog or manual says little on its own: ``officially added support for
CPython 3.12`` belongs to whatever release heading is above it, often in the previous chunk
(ref-near-03). Document files of folder sources therefore carry their heading path in the
chunk header (:func:`app.chunking.header.document_header`).

The heading syntax follows the file's format (:func:`heading_syntax`, D-071):

- **Markdown** (``.md``, ``.markdown`` and documents parsed from PDF, Word, …): ATX
  (``# Title`` … ``###### Title`` at column 0) and setext titles (a title line after a blank line,
  underlined with ``=`` for level 1 or ``-`` for level 2, as CommonMark defines); YAML front
  matter at the top is skipped; ``` and ``~~~`` fence code blocks.
- **reStructuredText and plain text** (``.rst``, ``.txt``): ATX lines, underlined titles (a title
  line at column 0 after a blank line, then a line of one repeated adornment character, at least
  3 long) and over- and underlined titles (the title may be indented). Every reStructuredText
  adornment character counts; levels follow the order in which each style first appears
  (docutils' rule). An adornment other than ``=`` / ``-`` must be at least as long as its title,
  so a ``***`` rule after a line of text is not a heading. Only ``` fences code:
  reStructuredText has no fences, and a ``~~~~`` line is a transition or an adornment.
- **AsciiDoc** (``.adoc``, ``.asciidoc``): ``= Title`` … ``====== Title`` (and Markdown ``#``)
  at column 0, the level is the number of markers. Delimited blocks (``----``, ``....``,
  ``====``, ``****``, ``____``, ``++++``, ``////`` and ```) hold no headings; block attributes
  (``[source,yaml]``, ``[NOTE]``) are never titles. Two-line (underlined) titles, deprecated in
  AsciiDoc, are not recognised: a missed heading only leaves out context, a wrong one would give
  lines a section they are not in.
- **Org** (``.org``): ``* Heading``, ``** Sub`` … at column 0 (trailing ``:tags:`` dropped);
  ``#+BEGIN_…`` / ``#+END_…`` blocks hold no headings, and ``# …`` is a comment.

A title line that is a list item, table row, quote, directive or code is never an underlined
title.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

# reStructuredText adornment characters (any printable 7-bit ASCII punctuation).
_RST_ADORNMENT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_MARKDOWN_ADORNMENT = "=-"
_ATX = re.compile(r"(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*")
_ASCIIDOC_TITLE = re.compile(r"(={1,6}|#{1,6})[ \t]+(\S.*?)(?:[ \t]+\1)?[ \t]*")
_ORG_HEADLINE = re.compile(r"(\*+)[ \t]+(.*?)(?:[ \t]+:[\w@#%:]+:)?[ \t]*")
FENCE = re.compile(r" {0,3}(```|~~~)")
Syntax = Literal["markdown", "rst", "asciidoc", "org"]
# Lines that open or close a block holding no headings, per syntax.
FENCES: dict[Syntax, re.Pattern[str]] = {
    "markdown": FENCE,
    "rst": re.compile(r" {0,3}```"),
    "asciidoc": re.compile(r"(?:-{4,}|\.{4,}|={4,}|\*{4,}|_{4,}|\+{4,}|/{4,})[ \t]*$|```"),
    "org": re.compile(r"[ \t]*#\+(?:begin|end)_", re.IGNORECASE),
}
_ORG_BLOCK_START = re.compile(r"[ \t]*#\+begin_", re.IGNORECASE)
_ORG_BLOCK_END = re.compile(r"[ \t]*#\+end_", re.IGNORECASE)
_SYNTAX_BY_EXTENSION: dict[str, Syntax] = {
    "md": "markdown",
    "markdown": "markdown",
    "rst": "rst",
    "txt": "rst",
    "adoc": "asciidoc",
    "asciidoc": "asciidoc",
    "org": "org",
}
# A line that is something else than a title: bullet item, table row, quote, directive, field.
_NOT_TITLE = re.compile(r"[-*+][ \t]|[|>]|\.\.[ \t]|:[A-Za-z]|```|~~~")
MIN_ADORNMENT = 3
SEPARATOR = " › "
MAX_PATH_CHARS = 200


def heading_syntax(path: str) -> Syntax:
    """The heading syntax of a document file, by its extension. A document of another format
    (PDF, Word, …) is parsed into Markdown."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _SYNTAX_BY_EXTENSION.get(extension, "markdown")


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


def _closes(syntax: Syntax, opened: str, current: str) -> bool:
    """Whether ``current`` closes the block that the line ``opened`` opened."""
    if syntax == "asciidoc":  # the same delimiter closes it (``` with or without a language)
        return (
            current.strip().startswith("```")
            if opened.startswith("```")
            else (current.strip() == opened)
        )
    if syntax == "org":
        return _ORG_BLOCK_END.match(current) is not None
    return FENCES[syntax].match(current) is not None


def find_headings(text: str, start: int, end: int, *, syntax: Syntax) -> list[Heading]:
    """The headings of the document at ``text[start:end]`` in order (see the module docstring)."""
    lines = _lines(text, start, end)
    markdown = syntax == "markdown"
    underlined = syntax in ("markdown", "rst")
    chars = _MARKDOWN_ADORNMENT if markdown else _RST_ADORNMENT
    styles: dict[tuple[str, bool], int] = {}  # underline styles of a reStructuredText file → level
    found: list[Heading] = []
    opened: str | None = None  # the line that opened the code block we are in

    def line(i: int) -> str:
        return text[lines[i][0] : lines[i][1]]

    first = _front_matter_end(text, lines) if markdown else 0

    def blank_before(i: int) -> bool:
        return i == first or not line(i - 1).strip()

    def level(char: str, overlined: bool) -> int:
        if markdown:
            return 1 if char == "=" else 2
        return styles.setdefault((char, overlined), len(styles) + 1)

    def one_line(i: int) -> Heading | None:
        current = line(i)
        if syntax == "org":
            match = _ORG_HEADLINE.fullmatch(current)
            return (
                Heading(lines[i][0], lines[i][1], match.group(2).strip(), len(match.group(1)))
                if match and match.group(2).strip()
                else None
            )
        match = (_ASCIIDOC_TITLE if syntax == "asciidoc" else _ATX).fullmatch(current)
        if match is None:
            return None
        return Heading(lines[i][0], lines[i][1], match.group(2).strip(), len(match.group(1)))

    def heading_at(i: int) -> tuple[Heading, int] | None:
        """The heading starting at line ``i`` and the number of lines it takes."""
        single = one_line(i)
        if single is not None:
            return single, 1
        if not underlined or not blank_before(i):
            return None
        current = line(i)
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

    fence = _ORG_BLOCK_START if syntax == "org" else FENCES[syntax]
    i = first
    while i < len(lines):
        if opened is not None:  # only the line closing the code block matters inside it
            if _closes(syntax, opened, line(i)):
                opened = None
            i += 1
            continue
        found_here = heading_at(i)
        if found_here is not None:
            found.append(found_here[0])
            i += found_here[1]
            continue
        if fence.match(line(i)):  # an adornment line is tried as a heading first
            opened = line(i).strip()
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
