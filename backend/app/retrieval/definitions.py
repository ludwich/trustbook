"""Pull in the definitions of module-level constants that code chunks refer to (Gate 1, G3).

A chunk such as ``def __init__(self, pool_connections=DEFAULT_POOLSIZE, ...)`` names a value
that is defined elsewhere in the file (``DEFAULT_POOLSIZE = 10``). Without that line the model can
only answer with the constant's name. After the context is chosen, each code chunk's UPPER_CASE
names that it does not define itself are looked up: a chunk of the same source with a line
``NAME = …`` at column 0, in the same file first, then anywhere in the source. At most
:data:`MAX_EXTRA` such chunks are added, within the token budget. They are ordinary numbered
chunks: the model may cite them and every grounding layer treats them like any other chunk.

A big data literal is split into parts (D-056) and only part 1 has the ``NAME = …`` line, while
the entry a referrer looks up (``ERROR_MESSAGES[code]``) is usually in another part. Such a
definition is the literal's parts, counted as one extra: all of them up to
:data:`LITERAL_MAX_TOKENS` (what one chunk held before the split), preferring the parts the
reranker scored highest for the question, then the parts in order (D-058).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine

from app.chunking.header import header_part
from app.chunking.syntax import MODULE_MAX_TOKENS
from app.retrieval.store import ChunkRow, chunks_assigning, chunks_of_symbol

MAX_EXTRA = 3
# The parts of a split data literal added for one name: the largest module-level chunk the code
# chunker makes, so a literal up to this size comes in whole, as it did before it was split.
LITERAL_MAX_TOKENS = MODULE_MAX_TOKENS
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
        # Part 1 of a split literal defines the name but holds only the first entries.
        if code_file(chunk.locator) and header_part(chunk.text) is None:
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


def literal_parts(
    engine: Engine, definer: ChunkRow, scores: Mapping[str, float] | None = None
) -> list[ChunkRow]:
    """The chunks that make up ``definer``'s definition, in line order.

    ``[definer]`` unless it is part 1 of a split data literal; then its parts up to
    :data:`LITERAL_MAX_TOKENS`, the ones with the highest ``scores`` (rerank scores by chunk id)
    first, then the rest in order.
    """
    file, symbol = code_file(definer.locator), definer.locator.get("symbol")
    if header_part(definer.text) != 1 or file is None or not isinstance(symbol, str):
        return [definer]
    rows = chunks_of_symbol(engine, definer.source_id, file, symbol)
    ids = [row.chunk_id for row in rows]
    if definer.chunk_id not in ids:
        return [definer]
    parts: list[ChunkRow] = []
    for row in rows[ids.index(definer.chunk_id) :]:
        if header_part(row.text) != len(parts) + 1:
            break  # the next chunk is not the next part of this literal
        parts.append(row)
    scored = scores or {}
    order = sorted(
        range(len(parts)),
        key=lambda i: (parts[i].chunk_id not in scored, -scored.get(parts[i].chunk_id, 0.0), i),
    )
    chosen: set[int] = set()
    used = 0
    for i in order:
        if chosen and used + parts[i].token_count > LITERAL_MAX_TOKENS:
            continue
        chosen.add(i)
        used += parts[i].token_count
    return [parts[i] for i in sorted(chosen)]


def definition_extras(
    engine: Engine,
    chunks: Sequence[ContextChunk],
    *,
    max_extra: int = MAX_EXTRA,
    scores: Mapping[str, float] | None = None,
) -> list[Extra]:
    """Chunks (not already in ``chunks``) defining constants they use, for up to ``max_extra``
    names. A split data literal brings several of its parts (:func:`literal_parts`)."""
    wanted = wanted_names(chunks)
    if not wanted:
        return []
    definitions = find_definitions(engine, wanted)
    taken = {c.chunk_id for c in chunks}
    extras: list[Extra] = []
    names = 0
    for item in wanted:
        row = definitions.get(item)
        if row is None:
            continue
        rows = [r for r in literal_parts(engine, row, scores) if r.chunk_id not in taken]
        if not rows:
            continue
        taken |= {r.chunk_id for r in rows}
        extras.extend(Extra(r, item.name, item.referrer) for r in rows)
        names += 1
        if names >= max_extra:
            break
    return extras


def fit_extras[C: ContextChunk](
    selected: Sequence[C],
    extras: Sequence[tuple[C, str]],
    *,
    max_tokens: int,
    protect: Collection[str] = (),
) -> list[C]:
    """Append ``(chunk, referrer id)`` extras to ``selected`` within ``max_tokens``.

    When the budget is full an extra may displace the lowest-ranked selected chunks, but never
    the top chunk, a chunk an added extra was found for, another extra, or a chunk in ``protect``
    (extras added by an earlier step). An extra that cannot fit that way is left out.
    """
    result = list(selected)
    if not result:
        return result
    used = sum(c.token_count for c in result)
    keep = {result[0].chunk_id, *protect}
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
