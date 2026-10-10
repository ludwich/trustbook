"""Pull in the definitions of module-level constants that code chunks refer to (Gate 1, G3).

A chunk such as ``def __init__(self, pool_connections=DEFAULT_POOLSIZE, ...)`` names a value
that is defined elsewhere in the file (``DEFAULT_POOLSIZE = 10``). Without that line the model can
only answer with the constant's name. After the context is chosen, each code chunk's UPPER_CASE
names that it does not define itself are looked up: a chunk of the same source with a line
``NAME = …`` at column 0, in the same file first, then anywhere in the source. At most
:data:`MAX_EXTRA` such chunks are added, within the token budget. They are ordinary numbered
chunks: the model may cite them and every grounding layer treats them like any other chunk.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine

from app.retrieval.store import ChunkRow, chunks_assigning

MAX_EXTRA = 3
# Upper bound on names looked up per answer (each costs about 1 ms per 3,600 chunks).
MAX_NAMES = 24
_NAME = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
# ``NAME = …`` or ``NAME: Final[int] = …`` at column 0 (not ``NAME == …``).
_DEFINITION = re.compile(r"^([A-Z][A-Z0-9_]{2,})[ \t]*(?::[^=\n]*)?=(?!=)", re.MULTILINE)


class ContextChunk(Protocol):
    """What this module reads from a context chunk (``RetrievedChunk`` and ``ChunkRow`` fit)."""

    @property
    def chunk_id(self) -> str: ...
    @property
    def source_id(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def locator(self) -> dict[str, object]: ...
    @property
    def token_count(self) -> int: ...


@dataclass(frozen=True)
class Wanted:
    """A constant referenced by the context chunk ``referrer`` (in ``file`` of a source)."""

    name: str
    source_id: str
    file: str
    referrer: str


@dataclass(frozen=True)
class Extra:
    """A chunk that defines ``name``, needed by the context chunk ``referrer``."""

    row: ChunkRow
    name: str
    referrer: str


def code_file(locator: dict[str, object]) -> str | None:
    """The file of a code chunk (locator with ``file`` and line numbers), else ``None``."""
    file = locator.get("file")
    if isinstance(file, str) and file and "start_line" in locator:
        return file
    return None


def code_body(text: str) -> str:
    """Chunk text without the ``File: …`` line the code chunker puts in front."""
    if text.startswith("File: "):
        return text.partition("\n")[2]
    return text


def defined_names(code: str) -> set[str]:
    """UPPER_CASE names assigned at column 0 in ``code``."""
    return set(_DEFINITION.findall(code))


def referenced_names(code: str) -> list[str]:
    """UPPER_CASE names used in ``code`` but not defined there, unique, in order."""
    own = defined_names(code)
    seen: dict[str, None] = {}
    for name in _NAME.findall(code):
        if name not in own:
            seen.setdefault(name, None)
    return list(seen)


def wanted_names(chunks: Sequence[ContextChunk]) -> list[Wanted]:
    """Constants the code chunks refer to that no chunk in ``chunks`` defines, best chunk first."""
    already: set[str] = set()
    for chunk in chunks:
        if code_file(chunk.locator):
            already |= defined_names(code_body(chunk.text))
    wanted: list[Wanted] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        file = code_file(chunk.locator)
        if file is None:
            continue
        for name in referenced_names(code_body(chunk.text)):
            key = (chunk.source_id, name)
            if name in already or key in seen:
                continue
            seen.add(key)
            wanted.append(Wanted(name, chunk.source_id, file, chunk.chunk_id))
    return wanted[:MAX_NAMES]


def find_definitions(engine: Engine, wanted: Sequence[Wanted]) -> dict[Wanted, ChunkRow]:
    """The code chunk defining each wanted name: same file first, then the same source."""
    by_source: dict[str, list[Wanted]] = {}
    for item in wanted:
        by_source.setdefault(item.source_id, []).append(item)
    found: dict[Wanted, ChunkRow] = {}
    for source_id, items in by_source.items():
        rows = chunks_assigning(engine, source_id, sorted({w.name for w in items}))
        defines = [
            (row, defined_names(code_body(row.text))) for row in rows if code_file(row.locator)
        ]
        for item in items:
            matches = [row for row, names in defines if item.name in names]
            same_file = [row for row in matches if code_file(row.locator) == item.file]
            if matches:
                found[item] = (same_file or matches)[0]
    return found


def definition_extras(
    engine: Engine, chunks: Sequence[ContextChunk], *, max_extra: int = MAX_EXTRA
) -> list[Extra]:
    """Up to ``max_extra`` chunks (not already in ``chunks``) defining constants they use."""
    wanted = wanted_names(chunks)
    if not wanted:
        return []
    definitions = find_definitions(engine, wanted)
    taken = {c.chunk_id for c in chunks}
    extras: list[Extra] = []
    for item in wanted:
        row = definitions.get(item)
        if row is None or row.chunk_id in taken:
            continue
        taken.add(row.chunk_id)
        extras.append(Extra(row, item.name, item.referrer))
        if len(extras) >= max_extra:
            break
    return extras


def fit_extras[C: ContextChunk](
    selected: Sequence[C], extras: Sequence[tuple[C, str]], *, max_tokens: int
) -> list[C]:
    """Append ``(chunk, referrer id)`` extras to ``selected`` within ``max_tokens``.

    When the budget is full an extra may displace the lowest-ranked selected chunks, but never
    the top chunk, a chunk an added extra was found for, or another extra. An extra that cannot
    fit that way is left out.
    """
    result = list(selected)
    if not result:
        return result
    used = sum(c.token_count for c in result)
    keep = {result[0].chunk_id}
    for extra, referrer in extras:
        over = used + extra.token_count - max_tokens
        if over > 0:
            protected = keep | {referrer}
            drop: set[str] = set()
            freed = 0
            for chunk in reversed(result):
                if freed >= over:
                    break
                if chunk.chunk_id not in protected:
                    drop.add(chunk.chunk_id)
                    freed += chunk.token_count
            if freed < over:
                continue
            result = [c for c in result if c.chunk_id not in drop]
            used -= freed
        result.append(extra)
        used += extra.token_count
        keep |= {extra.chunk_id, referrer}
    return result
