"""The ``File: …`` first line of a folder file's chunk, and the parts of it that are not content.

Code: ``File: <path> · <symbol> (lines a-b)``; a group of a split data literal also names its part,
``File: <path> · _codes (part 3) (lines a-b)`` (D-056). The part number says where the chunk is,
not what the code holds: full-text search drops it with the line numbers (D-051), and the
deterministic code checks (grounding layer 5) do not accept it as a number the code states.

Document files (``.md``, ``.rst``, ``.txt``, …): ``File: <path> · <heading path> (lines a-b)``,
``File: <path> (lines a-b)`` before the first heading, and without ``(lines a-b)`` for a paged
document (D-068). The heading path (``Release History › 2.32.0 (2024-05-20)``) is the section the
chunk's first line is under: context the model, the grounding checks and the judge may read, while
line numbers stay on the body lines. Chunks are split at headings (D-104), so the heading path is
the section of the chunk's whole text; a chunk that starts with headings without text of their own
is named by the last of them.

Web pages, pasted and uploaded text under a heading: ``Section: <heading path>`` (D-105), the same
heading path without a file. Text before the first heading has no first line.
"""

from __future__ import annotations

import re

from app.chunking.headings import SEPARATOR, Heading, find_headings, heading_syntax

# ``(part N)`` of a split data literal's header, which is always the chunk's first line.
_PART_IN_HEADER = re.compile(r"\A(File: [^\n]*?) \(part (\d+)\)(?= \(lines \d+-\d+\))")
_LINES_SUFFIX = re.compile(r" \(lines \d+-\d+\)\Z")
_LOCATION_SUFFIX = re.compile(r"(?: \(part \d+\))? \(lines \d+-\d+\)\Z")
# A heading's numbering: "4 ", "4.2 ", "IV. " is not matched; "2.32.0 (2024-05-20)" is not one
# (no word follows the number).
_NUMBERING = re.compile(r"\d+(?:\.\d+)*\.?[ \t]+(?=[^\W\d_])")
# A number as written in full: a version (2.32.0, v2.32.0) or a date (2024-05-20) is one number.
_COMPLETE_NUMBER = re.compile(r"(?:(?<=\b[vV])|(?<![\w.\-]))\d+(?:[.\-]\d+)*(?!\w|[.\-]\d)")
FILE_PREFIX = "File: "
SECTION_PREFIX = "Section: "
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


def section_header(heading_path: str) -> str:
    """``Section: Outer › Inner``: the first line of a web page's, pasted or uploaded text's chunk
    under a heading (D-105); ``""`` (no first line) without a heading."""
    heading_path = " ".join(heading_path.split())
    return f"{SECTION_PREFIX}{heading_path}" if heading_path else ""


def section_path(chunk_text: str) -> str:
    """The heading path of a chunk's ``Section:`` first line, ``""`` for any other chunk."""
    first = chunk_text.split("\n", 1)[0]
    if not first.startswith(SECTION_PREFIX) or "\n" not in chunk_text:
        return ""
    return first[len(SECTION_PREFIX) :].strip()


def without_section_header(chunk_text: str) -> str:
    """A chunk's text without its ``Section:`` first line (the body only); others unchanged."""
    return chunk_text.partition("\n")[2] if section_path(chunk_text) else chunk_text


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


def section_of(chunk_text: str, path: str, first_line: int, start: int, end: int) -> str:
    """The section a document file's chunk puts lines ``start``-``end`` in (``first_line`` is its
    first body line): the header's heading path, or, below a heading inside the body, that
    heading's title. ``""`` when a heading inside the body starts after ``start`` and at or
    before ``end``: the lines are then in more than one section and none of them speaks for all.

    Strict on purpose: a line after a later heading in the chunk is never given the header's
    section, and only the innermost title of a heading inside the body is returned.

    A chunk split at headings (D-104) may start with headings that have no text of their own and
    is then named by the last of them, a heading inside the body. Lines above that heading are
    not in its section: they get ``""``. The heading itself and the lines below it get the header's
    path.
    """
    context = header_context(chunk_text, path)
    body = chunk_text.partition("\n")[2]
    headings = find_headings(body, 0, len(body), syntax=heading_syntax(path))
    starts = [first_line + body.count("\n", 0, h.start) for h in headings]
    if any(start < at <= end for at in starts):
        return ""
    named = _named_heading(context, headings, starts, first_line)
    if named is not None and start < named:
        return ""
    for heading, at in reversed(list(zip(headings, starts, strict=True))):
        if first_line < at <= start:
            return context if at == named else heading.title
    return context


def _named_heading(
    context: str, headings: list[Heading], starts: list[int], first_line: int
) -> int | None:
    """The line of the heading inside the body that the header names: the last heading of the
    body, after the first line, whose title is the innermost title of the header's path (D-104).
    ``None`` when the header names the section the first line is in (every chunk made before
    D-104, and every chunk that does not start with headings without text)."""
    if not headings or starts[-1] <= first_line or not context:
        return None
    innermost = context.split(SEPARATOR)[-1]
    return starts[-1] if " ".join(headings[-1].title.split()) == innermost else None


def section_at(chunk_text: str, path: str, first_line: int, line: int) -> str:
    """The section of one line: :func:`section_of` for ``line``-``line``."""
    return section_of(chunk_text, path, first_line, line, line)


def without_numbering(heading_path: str) -> str:
    """``4 Limits › 4.2 Timeouts`` → ``Limits › Timeouts``: a heading's numbering says where a
    section is, not what it says."""
    titles = heading_path.split(SEPARATOR)
    return SEPARATOR.join(t[m.end() :] if (m := _NUMBERING.match(t)) else t for t in titles)


def split_header(chunk_text: str) -> tuple[str, str]:
    """``(header line, body)`` of a folder file's chunk or a ``Section:`` chunk (D-105);
    ``("", chunk_text)`` without a header."""
    if not chunk_text.startswith(FILE_PREFIX) and not section_path(chunk_text):
        return "", chunk_text
    header, _, body = chunk_text.partition("\n")
    return header, body


def header_content(header: str) -> str:
    """What a ``File:`` header line states: path and section or symbol, without ``(part N)``,
    ``(lines a-b)`` and the numbering of the headings (``""`` for a line that is no header).
    A ``Section:`` line states its heading path without the numbering."""
    if header.startswith(SECTION_PREFIX):
        return f"{SECTION_PREFIX}{without_numbering(header[len(SECTION_PREFIX) :].strip())}"
    if not header.startswith(FILE_PREFIX):
        return ""
    where = _LOCATION_SUFFIX.sub("", header[len(FILE_PREFIX) :])
    path, separator, context = where.partition(CONTEXT_SEPARATOR)
    return f"{FILE_PREFIX}{path}{separator}{without_numbering(context)}"


def complete_numbers(text: str) -> frozenset[str]:
    """The numbers of ``text`` as written in full (``2.32.0``, ``2024-05-20``, ``8080``)."""
    return frozenset(m.group(0) for m in _COMPLETE_NUMBER.finditer(text))


def without_numbers(text: str) -> str:
    """``text`` with its complete numbers blanked out (names stay)."""
    return _COMPLETE_NUMBER.sub(" ", text)


def stated_in_full(sentence: str, numbers: frozenset[str], parts: re.Pattern[str]) -> set[str]:
    """The ``parts`` (number literals as the checks find them) of ``sentence`` that only occur in
    numbers it writes exactly as one of ``numbers``: "2.32" of "2.32.0" when ``numbers`` holds
    ``2.32.0``; not when the sentence also writes "2.32" (or "2.32.1") elsewhere."""
    inside: set[str] = set()
    outside: set[str] = set()
    for match in _COMPLETE_NUMBER.finditer(sentence):
        found = set(parts.findall(match.group(0)))
        (inside if match.group(0) in numbers else outside).update(found)
    return inside - outside


def header_part(chunk_text: str) -> int | None:
    """The part number of a split data literal's chunk (``None`` for every other chunk)."""
    match = _PART_IN_HEADER.match(chunk_text)
    return int(match.group(2)) if match else None


def without_part(chunk_text: str) -> str:
    """``chunk_text`` without the ``(part N)`` of its header; every other chunk unchanged."""
    return _PART_IN_HEADER.sub(r"\1", chunk_text, count=1)
