"""Parent context for the top children of a retrieval (D-077, D-079).

Retrieval ranks and cites small child chunks. After the context is chosen (and the D-032 / D-060
extras are added), the first :data:`EXPAND_TOP` ranked context chunks with a parent get two kinds
of parent context:

- **Citable promotion**, at most one chunk per child. A document or web child that does not start
  its section gets the chunk before it (``section_previous``); a method or class-run child gets its
  class's head chunk (``class_head``), always when it is at most :data:`HEAD_UNGATED_TOKENS`,
  otherwise only when the reranker scored it. A promoted chunk is an ordinary numbered chunk: the
  model cites it by its own exact lines and every grounding layer checks it like any other chunk.
  Promotions use only the free part of the context budget; they never displace a chunk and never
  enter ``ranked`` (layer 2 reads only ranked children).
- **Code outline**, NOT citable: per code file of an expanded child, the imports that bind names
  the context's code uses, the module's top-level names and each expanded class's declaration with
  its member names. It holds no values, no line numbers and no chunk labels. It is an
  :class:`OutlineBlock`, never a chunk, so nothing in it can be cited, become a premise, a judge
  item or a layer 5 anchor: a fact only the outline shows cannot verify (it fails closed).
- **Transcript windows** (D-091), NOT citable: for each of the first :data:`EXPAND_TOP` ranked
  context chunks of a transcript, the neighbouring chunks of its source (by ordinal: -1, +1, -2,
  +2), with their times and speakers, as one :class:`TranscriptWindow`. The child itself and any
  neighbour that is already a numbered chunk are only named by their times, never repeated. A
  window is never a chunk: citations go to the child's own times, and a fact only a window shows
  cannot verify (it fails closed).

Everything is read from ``space.db`` rows (the parent keys and structure records stored at chunk
time); nothing is fetched. Any error gives no expansion (the caller logs it).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from sqlalchemy import Engine

from app.chunking.clock import clock
from app.chunking.header import split_header
from app.chunking.structure import Import, Structure, key_file, module_key
from app.grounding.code_checks import unescape_markdown
from app.retrieval.definitions import code_file
from app.retrieval.fusion import CONTEXT_TOKENS
from app.retrieval.store import ChunkRow, chunks_at_ordinals

EXPAND_TOP = 3  # the first ranked context chunks that are expanded
HEAD_UNGATED_TOKENS = 200  # a class head up to this size is promoted without being ranked
PROMOTE_MAX_TOKENS = 600  # no promoted chunk is larger (measured previous-section max: 560)
MAX_PROMOTED = 3  # promoted chunks per answer
EXPANSION_TOKENS = 900  # promoted tokens per answer
OUTLINE_BLOCK_TOKENS = 120  # outline tokens per file
OUTLINE_TOKENS = 300  # outline tokens per prompt
OUTLINE_NAMES = 24  # names on one outline line (module names, class members)
WINDOW_TOKENS = 2_000  # one transcript window's span (the child included), chunk tokens
WINDOW_ANSWER_TOKENS = 2_400  # prompt tokens all transcript windows of an answer add
WINDOW_REACH = 2  # neighbours on each side of a transcript child
WINDOW_PLACEHOLDER = "(numbered chunk above)"

ExpansionKind = Literal["section_previous", "class_head"]
SECTION_PREVIOUS: ExpansionKind = "section_previous"
CLASS_HEAD: ExpansionKind = "class_head"

_LINE_KEY = re.compile(r"#L(\d+)$")
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_PY_FROM = re.compile(r"^(from\s+\S+\s+import\s+)\(?\s*(.*?)\s*,?\s*\)?\s*;?$")
_JS_BRACES = re.compile(r"^(.*?\{)\s*(.*?)\s*,?\s*(\}.*)$")


class Scored(Protocol):
    """A reranked candidate: its id and rerank score."""

    @property
    def chunk_id(self) -> str: ...
    @property
    def score(self) -> float: ...


class Child(Protocol):
    """What expansion reads from a context chunk (``RetrievedChunk`` fits)."""

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
    @property
    def ordinal(self) -> int: ...
    @property
    def parent_key(self) -> str | None: ...
    @property
    def structure(self) -> Structure | None: ...
    @property
    def definition_of(self) -> str | None: ...


@dataclass(frozen=True)
class OutlineBlock:
    """The outline of one code file: ``lines`` are shown indented under the file path.

    Never a chunk: no label, no line numbers, no values. ``tokens``: :func:`outline_tokens` of
    :meth:`render`.
    """

    source_id: str
    file: str
    lines: tuple[str, ...]
    tokens: int

    def render(self) -> str:
        return "\n".join([self.file, *(f"  {line}" for line in self.lines)])


@dataclass(frozen=True)
class Promotion:
    """A chunk promoted for the context child ``referrer`` (its rerank score, else 0.0)."""

    row: ChunkRow
    score: float
    kind: ExpansionKind
    referrer: str


@dataclass(frozen=True)
class WindowPart:
    """One line of a transcript window: a chunk of the transcript, by its times and speaker.

    ``text`` None: the chunk is a numbered chunk of the prompt (the child or a numbered
    neighbour), named by its times only. ``tokens``: what the line adds to the prompt.
    """

    chunk_id: str
    ordinal: int
    start_s: object
    end_s: object
    speaker: str | None
    text: str | None
    tokens: int

    def label(self) -> str:
        """``[0:12–2:10] Anna:`` (without the speaker when it has none)."""
        span = f"[{clock(self.start_s)}–{clock(self.end_s)}]"
        return f"{span} {self.speaker}:" if self.speaker else span

    def render(self) -> str:
        body = WINDOW_PLACEHOLDER if self.text is None else self.text
        return f"{self.label()} {body}"


@dataclass(frozen=True)
class TranscriptWindow:
    """What was said around the numbered transcript chunks of one source, in time order.

    Never a chunk: no label, never cited, never a premise, judge item or anchor (D-091).
    ``tokens``: what the window adds to the prompt (:func:`outline_tokens` of its title and
    labels, plus each shown neighbour's stored token count).
    """

    source_id: str
    title: str
    parts: tuple[WindowPart, ...]
    tokens: int

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        """The neighbours whose text the window shows, in time order."""
        return tuple(p.chunk_id for p in self.parts if p.text is not None)

    @property
    def numbered_ids(self) -> tuple[str, ...]:
        """The numbered chunks the window names by their times only."""
        return tuple(p.chunk_id for p in self.parts if p.text is None)

    def render(self) -> str:
        return "\n".join([self.title, *(f"  {part.render()}" for part in self.parts)])


@dataclass(frozen=True)
class Expansion:
    """What :func:`expand` adds: promoted chunks (in child rank order), the outline and the
    transcript windows."""

    promoted: tuple[Promotion, ...] = ()
    outline: tuple[OutlineBlock, ...] = ()
    promoted_tokens: int = 0
    outline_tokens: int = 0
    windows: tuple[TranscriptWindow, ...] = ()
    window_tokens: int = 0


FileRowsLookup = Callable[[str, str], Sequence[ChunkRow]]


def outline_tokens(text: str) -> int:
    """Token estimate for outline text: UTF-8 bytes / 3, rounded up (CJK-safe; code measured
    0.348 tokens per byte)."""
    return math.ceil(len(text.encode("utf-8")) / 3)


def outline_text(blocks: Sequence[OutlineBlock]) -> str:
    """The outline as the prompt shows it (without its heading): blocks separated by blank lines."""
    return "\n\n".join(block.render() for block in blocks)


def outline_fingerprint(blocks: Sequence[OutlineBlock]) -> str | None:
    """A short hash of the outline text (None without an outline)."""
    if not blocks:
        return None
    return hashlib.sha256(outline_text(blocks).encode("utf-8")).hexdigest()[:16]


def fit_free(used: int, size: int, *, max_tokens: int) -> bool:
    """Whether a chunk of ``size`` tokens fits the free part of the budget (never displaces)."""
    return size >= 0 and used + size <= max_tokens


def children(chunks: Sequence[Child], *, top: int = EXPAND_TOP) -> list[Child]:
    """The context chunks that are expanded: among the first ``top`` ranked context chunks (not
    the D-032 / D-060 extras), those with a parent."""
    ranked = [c for c in chunks if c.definition_of is None][:top]
    return [c for c in ranked if c.parent_key is not None and c.structure is not None]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _squeezed(text: str) -> str:
    """``text`` without any whitespace: an import joined onto one line still matches its lines."""
    return "".join(text.split())


def _same_place(a: dict[str, object], b: dict[str, object]) -> bool:
    return a.get("file") == b.get("file")


def _reaches_section_start(row: ChunkRow, key: str) -> bool:
    """A line-located chunk whose lines include the first line of the section ``key``."""
    match = _LINE_KEY.search(key)
    start, end = row.locator.get("start_line"), row.locator.get("end_line")
    if match is None or not isinstance(start, int) or not isinstance(end, int):
        return False
    return start <= int(match.group(1)) <= end


def _previous_in_section(
    engine: Engine, child: Child, allowed_source_ids: Iterable[str]
) -> ChunkRow | None:
    if child.ordinal < 1 or child.parent_key is None:
        return None
    found = chunks_at_ordinals(engine, child.source_id, [child.ordinal - 1], allowed_source_ids)
    row = found.get(child.ordinal - 1)
    if row is None or not _same_place(row.locator, child.locator):
        return None
    if row.parent_key == child.parent_key or _reaches_section_start(row, child.parent_key):
        return row
    return None


def _class_head(child: Child, file_rows: FileRowsLookup, ranked_ids: set[str]) -> ChunkRow | None:
    file = key_file(child.parent_key or "")
    if file is None:
        return None
    heads = [
        row
        for row in file_rows(child.source_id, file)
        if row.parent_key == child.parent_key and row.structure is not None and row.structure.head
    ]
    if not heads:
        return None
    head = min(heads, key=lambda row: row.ordinal)
    if head.token_count <= HEAD_UNGATED_TOKENS or head.chunk_id in ranked_ids:
        return head
    return None


def promotions(
    engine: Engine,
    chunks: Sequence[Child],
    expanded: Sequence[Child],
    scores: dict[str, float],
    *,
    file_rows: FileRowsLookup,
    allowed_source_ids: Iterable[str],
    max_tokens: int = CONTEXT_TOKENS,
) -> list[Promotion]:
    """At most one citable chunk per expanded child, within every promotion cap and the free
    context budget (:func:`fit_free`); chunks already in the context are skipped."""
    allowed = list(allowed_source_ids)
    taken = {c.chunk_id for c in chunks}
    used = sum(max(0, c.token_count) for c in chunks)
    promoted: list[Promotion] = []
    spent = 0
    for child in expanded:
        if len(promoted) >= MAX_PROMOTED:
            break
        structure = child.structure
        if structure is None or structure.head:
            continue
        row: ChunkRow | None
        kind: ExpansionKind
        if structure.kind == "section":
            row, kind = _previous_in_section(engine, child, allowed), SECTION_PREVIOUS
        elif structure.kind == "class":
            row, kind = _class_head(child, file_rows, set(scores)), CLASS_HEAD
        else:
            continue  # module children: the outline covers them
        if row is None or row.chunk_id in taken:
            continue
        size = max(0, row.token_count)
        if size > PROMOTE_MAX_TOKENS or spent + size > EXPANSION_TOKENS:
            continue
        if not fit_free(used, size, max_tokens=max_tokens):
            continue
        promoted.append(Promotion(row, scores.get(row.chunk_id, 0.0), kind, child.chunk_id))
        taken.add(row.chunk_id)
        used += size
        spent += size
    return promoted


# --- outline -------------------------------------------------------------------------------


def _identifiers(texts: Iterable[str]) -> set[str]:
    return {name for text in texts for name in _IDENTIFIER.findall(text)}


def _bound(item: str) -> str:
    """The name an import list item binds: ``b`` for ``a as b``, ``a`` for ``a``."""
    words = item.replace("*", " ").split()
    return words[-1] if words else ""


def _cut_list(items: list[str], used: set[str]) -> str | None:
    """The list with unused items replaced by ``…`` (one per run); None when no item is used."""
    out: list[str] = []
    kept = False
    for item in items:
        if _bound(item) in used:
            out.append(item)
            kept = True
        elif not out or out[-1] != "…":
            out.append("…")
    return ", ".join(out) if kept else None


def clipped(text: str) -> bool:
    """Whether a stored import text was cut at the structure text cap (it ends with ``…``)."""
    return text.endswith("…")


def _alias_item(name: str, full: str) -> str | None:
    """The import list item that binds ``name`` in the statement ``full``: ``a as name`` or
    ``name``; None when ``full`` does not show it."""
    alias = re.search(rf"([\w$.]+)\s+as\s+{re.escape(name)}(?![\w$])", full)
    if alias is not None:
        return f"{alias.group(1)} as {name}"
    if re.search(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", full):
        return name
    return None


def _clipped_import(imp: Import, used: set[str], full: str | None) -> str | None:
    """A clipped import statement shortened from its bound names and its full source text.

    Only a ``from M import …`` or ``import { … } from …`` list is rebuilt: the head comes from the
    stored text, each used name's item from ``full`` (the statement's own source lines), the
    JavaScript ``from`` part from ``full``. Without ``full``, or when ``full`` does not show an
    item, None: a clipped statement is never shown as stored.
    """
    if full is None:
        return None
    flat = _flat(full)
    items: list[str] = []
    for name in imp.names:
        if name in used:
            item = _alias_item(name, flat)
            if item is None:
                return None
            items.append(item)
        else:
            items.append("…")  # cut by _cut_list below
    cut = _cut_list(items, used)
    if cut is None:
        return None
    py = re.match(r"^(from\s+\S+\s+import\s+)", imp.text)
    if py and flat.startswith(py.group(1).rstrip()):
        return f"{py.group(1)}{cut}"
    js = re.match(r"^(.*?\{)", imp.text)
    tail = re.search(r"(\}\s*from\s*(['\"])[^'\"]*\2\s*;?)\s*$", flat)
    if js and tail and flat.startswith(js.group(1)):
        return f"{js.group(1)} {cut} {tail.group(1).rstrip(';')}"
    return None


def shorten_import(imp: Import, used: set[str], full: str | None = None) -> str | None:
    """The import statement with the names the context does not use cut to ``…``; None when it
    binds no used name. The text is the statement shortened, never anything it does not say.

    A stored text clipped at the structure text cap (:func:`clipped`) is rebuilt from ``full``,
    the statement's own source lines (see :func:`_clipped_import`), or left out.
    """
    wanted = [name for name in imp.names if name in used]
    if not wanted:
        return None
    if clipped(imp.text):
        return _clipped_import(imp, used, full)
    text = _flat(imp.text)
    if len(wanted) == len(imp.names):
        return text
    py = _PY_FROM.match(text)
    if py:
        items = [i.strip() for i in py.group(2).split(",") if i.strip()]
        cut = _cut_list(items, used)
        if cut is not None:
            return f"{py.group(1)}{cut}"
    js = _JS_BRACES.match(text)
    if js:
        items = [i.strip() for i in js.group(2).split(",") if i.strip()]
        cut = _cut_list(items, used)
        if cut is not None:
            return f"{js.group(1)} {cut} {js.group(3)}"
    return text


def _source_lines(rows: Sequence[ChunkRow], imp: Import) -> str | None:
    """Lines ``imp.line``..``imp.end_line`` of the module chunk that holds the import (its body
    without the ``File:`` header); None when no row holds them."""
    for row in rows:
        s = row.structure
        start = row.locator.get("start_line")
        if s is None or imp not in s.imports or not isinstance(start, int):
            continue
        body = split_header(row.text)[1].split("\n")
        first, last = imp.line - start, imp.end_line - start
        if 0 <= first <= last < len(body):
            return "\n".join(body[first : last + 1])
    return None


@dataclass(frozen=True)
class _Seen:
    """What the context already shows of one file: chunk texts (whitespace removed), the line
    ranges of the file's context chunks and the context chunk ids."""

    texts: tuple[str, ...]
    spans: tuple[tuple[int, int], ...]
    ids: frozenset[str]

    def shows(self, text: str) -> bool:
        squeezed = _squeezed(text)
        return bool(squeezed) and any(squeezed in chunk for chunk in self.texts)

    def covers(self, first: int, last: int) -> bool:
        return any(a <= first and last <= b for a, b in self.spans)


def _seen(context: Sequence[Child | ChunkRow], source_id: str, file: str) -> _Seen:
    spans: list[tuple[int, int]] = []
    for c in context:
        start, end = c.locator.get("start_line"), c.locator.get("end_line")
        same = c.source_id == source_id and code_file(c.locator) == file
        if same and isinstance(start, int) and isinstance(end, int):
            spans.append((start, end))
    return _Seen(
        tuple(_squeezed(c.text) for c in context),
        tuple(spans),
        frozenset(c.chunk_id for c in context),
    )


def _no_value(text: str) -> bool:
    """Outline text never carries an assignment or default value."""
    return "=" not in text


def _pick_names(
    names: Sequence[tuple[int, str]], near: Sequence[int], limit: int
) -> tuple[list[str], int]:
    """Up to ``limit`` names (by ordinal, deduplicated), nearest to the ``near`` ordinals first,
    shown in line order; and how many were left out."""
    unique: dict[str, int] = {}
    for ordinal, name in names:
        unique.setdefault(name, ordinal)
    ordered = list(unique.items())
    if len(ordered) <= limit:
        return [name for name, _ in ordered], 0
    distance = {
        name: (min((abs(ordinal - n) for n in near), default=0), i)
        for i, (name, ordinal) in enumerate(ordered)
    }
    chosen = set(sorted(distance, key=lambda name: distance[name])[:limit])
    return [name for name, _ in ordered if name in chosen], len(ordered) - limit


def _with_gaps(all_names: Sequence[str], shown: set[str]) -> str:
    out: list[str] = []
    for name in all_names:
        if name in shown:
            out.append(name)
        elif not out or out[-1] != "…":
            out.append("…")
    return ", ".join(out)


def _module_line(rows: Sequence[ChunkRow], file: str, near: Sequence[int], limit: int) -> str:
    module = module_key(file)
    names: list[tuple[int, str]] = []
    for row in rows:
        s = row.structure
        if s is None:
            continue
        if row.parent_key == module:
            names.extend((row.ordinal, name) for name in s.defines)
        elif s.kind == "class" and s.head and s.up == module and s.title:
            names.append((row.ordinal, f"class {s.title}"))
    shown, more = _pick_names(names, near, limit)
    if not shown:
        return ""
    text = ", ".join(shown) + (f", … and {more} more" if more else "")
    return f"module: {text}"


def _member(row: ChunkRow, title: str) -> str | None:
    symbol = row.locator.get("symbol")
    if not isinstance(symbol, str) or not symbol or symbol == title:
        return None
    return symbol.rsplit(".", 1)[-1]


def _class_line(
    rows: Sequence[ChunkRow], key: str, near: Sequence[int], limit: int, seen: _Seen
) -> str:
    family = [row for row in rows if row.parent_key == key and row.structure is not None]
    if not family:
        return ""
    title = next((row.structure.title for row in family if row.structure is not None), "")
    heads = [row for row in family if row.structure is not None and row.structure.head]
    head = heads[0] if heads else None
    decl = head.structure.decl if head is not None and head.structure is not None else None
    if not decl or not _no_value(decl) or seen.shows(decl) or (head and head.chunk_id in seen.ids):
        decl = title  # the declaration is in a context chunk already (or unknown)
    members = [(row.ordinal, m) for row in family if (m := _member(row, title)) is not None]
    shown, _ = _pick_names(members, near, limit)
    ordered = list(dict.fromkeys(name for _, name in members))
    if not ordered:
        return "" if decl == title else decl
    return f"{decl}: {_with_gaps(ordered, set(shown))}"


@dataclass(frozen=True)
class _Parts:
    """The candidate lines of one file's outline block, kept apart so the fitting can drop the
    least needed first: import statements, the module line, the classes' lines."""

    imports: tuple[str, ...]
    module: str
    classes: tuple[str, ...]

    def lines(self) -> list[str]:
        out = ["imports: " + "; ".join(self.imports)] if self.imports else []
        if self.module:
            out.append(self.module)
        return [*out, *self.classes]

    def smaller(self) -> _Parts | None:
        """One part less: the last import statement, else the module line, else the last class
        line (the expanded classes go last); None when nothing is left."""
        if self.imports:
            return _Parts(self.imports[:-1], self.module, self.classes)
        if self.module:
            return _Parts((), "", self.classes)
        if self.classes:
            return _Parts((), "", self.classes[:-1])
        return None


def _block_lines(
    rows: Sequence[ChunkRow],
    file: str,
    classes: Sequence[str],
    near: Sequence[int],
    used: set[str],
    seen: _Seen,
    limit: int,
) -> _Parts:
    module = module_key(file)
    imports: dict[tuple[int, str], Import] = {}
    for row in rows:
        if row.parent_key == module and row.structure is not None:
            for imp in row.structure.imports:
                imports.setdefault((imp.line, imp.text), imp)
    statements: list[str] = []
    for _, imp in sorted(imports.items()):
        if seen.covers(imp.line, imp.end_line) or seen.shows(imp.text):
            continue  # the import is in a context chunk already
        full = _source_lines(rows, imp) if clipped(imp.text) else None
        short = shorten_import(imp, used, full)
        if short and _no_value(short):
            statements.append(short)
    module_line = _module_line(rows, file, near, limit)
    class_lines: list[str] = []
    for key in classes:
        line = _class_line(rows, key, near, limit, seen)
        if line and _no_value(line) and not seen.shows(line):
            class_lines.append(line)
    return _Parts(
        tuple(statements),
        module_line if module_line and _no_value(module_line) else "",
        tuple(class_lines),
    )


def _fitted_block(source_id: str, file: str, make: Callable[[int], _Parts]) -> OutlineBlock | None:
    """The block within :data:`OUTLINE_BLOCK_TOKENS`: fewer names per line first, then import
    statements dropped from the last, then the module line, then class lines from the last (the
    expanded children's classes are what the outline is for). When even the first class line
    alone is over the cap (long member names), it alone with only the names nearest the children;
    None when not even that fits."""
    parts: _Parts | None = None
    for limit in (OUTLINE_NAMES, OUTLINE_NAMES // 2, OUTLINE_NAMES // 4):
        parts = make(limit)
        if _block_tokens(source_id, file, parts.lines()) <= OUTLINE_BLOCK_TOKENS:
            break
    while (
        parts is not None and _block_tokens(source_id, file, parts.lines()) > OUTLINE_BLOCK_TOKENS
    ):
        parts = parts.smaller()
    lines = parts.lines() if parts is not None else []
    for limit in (2, 1):
        if lines:
            break
        first = _Parts((), "", make(limit).classes[:1]).lines()
        if first and _block_tokens(source_id, file, first) <= OUTLINE_BLOCK_TOKENS:
            lines = first
    if not lines:
        return None
    return OutlineBlock(source_id, file, tuple(lines), _block_tokens(source_id, file, lines))


def _block_tokens(source_id: str, file: str, lines: Sequence[str]) -> int:
    return outline_tokens(OutlineBlock(source_id, file, tuple(lines), 0).render())


def outline(
    expanded: Sequence[Child],
    context: Sequence[Child | ChunkRow],
    *,
    file_rows: FileRowsLookup,
) -> list[OutlineBlock]:
    """One block per code file of the expanded children, in the order of their best child, within
    :data:`OUTLINE_BLOCK_TOKENS` per file and :data:`OUTLINE_TOKENS` in all (lowest-ranked blocks
    dropped first)."""
    files: dict[tuple[str, str], list[Child]] = {}
    for child in expanded:
        file = key_file(child.parent_key or "")
        if file is None or child.structure is None or child.structure.kind == "section":
            continue
        files.setdefault((child.source_id, file), []).append(child)
    blocks: list[OutlineBlock] = []
    for (source_id, file), members in files.items():
        rows = file_rows(source_id, file)
        if not rows:
            continue
        used = _identifiers(
            c.text for c in context if c.source_id == source_id and code_file(c.locator) == file
        )
        classes = list(
            dict.fromkeys(
                c.parent_key
                for c in members
                if c.parent_key is not None
                and c.structure is not None
                and c.structure.kind == "class"
            )
        )
        near = [c.ordinal for c in members]
        seen = _seen(context, source_id, file)

        def make(
            limit: int,
            rows: Sequence[ChunkRow] = rows,
            file: str = file,
            classes: Sequence[str] = classes,
            near: Sequence[int] = near,
            used: set[str] = used,
            seen: _Seen = seen,
        ) -> _Parts:
            return _block_lines(rows, file, classes, near, used, seen, limit)

        block = _fitted_block(source_id, file, make)
        if block is not None:
            blocks.append(block)
    while blocks and sum(b.tokens for b in blocks) > OUTLINE_TOKENS:
        blocks.pop()
    return blocks


# --- transcript windows (D-091) ------------------------------------------------------------


def window_text(windows: Sequence[TranscriptWindow]) -> str:
    """The windows as the prompt shows them (without their heading), separated by blank lines."""
    return "\n\n".join(window.render() for window in windows)


def window_fingerprint(windows: Sequence[TranscriptWindow]) -> str | None:
    """A short hash of the window text (None without a window)."""
    if not windows:
        return None
    return hashlib.sha256(window_text(windows).encode("utf-8")).hexdigest()[:16]


def _is_transcript(locator: dict[str, object]) -> bool:
    return "start_s" in locator


def transcript_children(chunks: Sequence[Child], *, top: int = EXPAND_TOP) -> list[Child]:
    """The context chunks that get a transcript window: among the first ``top`` ranked context
    chunks (the same slice as :func:`children`, never an extra or a promotion), those of a
    transcript with a known position in their source."""
    ranked = [c for c in chunks if c.definition_of is None][:top]
    return [c for c in ranked if _is_transcript(c.locator) and c.ordinal >= 0]


def _speaker(locator: dict[str, object]) -> str | None:
    speaker = locator.get("speaker")
    return " ".join(speaker.split()) or None if isinstance(speaker, str) else None


def _part(
    chunk_id: str, ordinal: int, locator: dict[str, object], text: str | None, tokens: int
) -> WindowPart:
    """A window line; its ``tokens`` are its label (or placeholder) plus ``tokens`` of text."""
    shown = None if text is None else " ".join(unescape_markdown(text).split())
    # A numbered chunk's line names only its times: its header carries the speaker.
    speaker = None if shown is None else _speaker(locator)
    bare = WindowPart(
        chunk_id, ordinal, locator.get("start_s"), locator.get("end_s"), speaker, None, 0
    )
    if shown is None:
        return replace(bare, tokens=outline_tokens(f"  {bare.render()}\n"))
    cost = outline_tokens(f"  {bare.label()} \n") + max(0, tokens)
    return replace(bare, text=shown, tokens=cost)


def _title(title: str) -> str:
    return " ".join(title.split()) or "Untitled"


def _window_tokens(title: str, parts: Iterable[WindowPart]) -> int:
    return outline_tokens(f"{title}\n") + sum(p.tokens for p in parts)


def _merged(windows: Sequence[TranscriptWindow]) -> list[TranscriptWindow]:
    """Windows of one source whose ordinals overlap or touch, joined into one block in time
    order (each chunk once); blocks keep the order of their first window."""
    merged: list[TranscriptWindow] = []
    for window in windows:
        low = min(p.ordinal for p in window.parts)
        high = max(p.ordinal for p in window.parts)
        for i, block in enumerate(merged):
            b_low = min(p.ordinal for p in block.parts)
            b_high = max(p.ordinal for p in block.parts)
            if block.source_id == window.source_id and low <= b_high + 1 and b_low <= high + 1:
                parts = {p.chunk_id: p for p in (*block.parts, *window.parts)}
                ordered = tuple(sorted(parts.values(), key=lambda p: p.ordinal))
                merged[i] = TranscriptWindow(
                    block.source_id, block.title, ordered, _window_tokens(block.title, ordered)
                )
                break
        else:
            merged.append(window)
    # A join can make two blocks touch: join again until nothing changes.
    return merged if len(merged) == len(windows) else _merged(merged)


def transcript_windows(
    engine: Engine,
    context: Sequence[Child | ChunkRow],
    kids: Sequence[Child],
    *,
    allowed_source_ids: Iterable[str],
    max_tokens: int = CONTEXT_TOKENS,
) -> list[TranscriptWindow]:
    """One window per transcript child (``kids``, best first), merged per source.

    ``context``: every numbered chunk of the prompt (the context chunks and the promotions).
    Neighbours are added whole, in the order -1, +1, -2, +2; a direction stops at the source's
    end or at the first neighbour that does not fit, so a window never has a gap. A neighbour
    fits when the window's span stays within :data:`WINDOW_TOKENS` (stored token counts, the
    child included), all windows add at most :data:`WINDOW_ANSWER_TOKENS` to the prompt, and the
    prompt stays within ``max_tokens`` (:func:`fit_free`: a window never displaces a chunk). A
    neighbour that is a numbered chunk is named by its times only, and one an earlier window
    shows costs nothing. A child without a neighbour to show gets no window.
    """
    allowed = list(allowed_source_ids)
    numbered = {c.chunk_id for c in context}
    used = sum(max(0, c.token_count) for c in context)
    spent = 0
    shown: set[str] = set()
    windows: list[TranscriptWindow] = []
    for child in kids:
        o = child.ordinal
        offsets = [d for step in range(1, WINDOW_REACH + 1) for d in (-step, step)]
        rows = chunks_at_ordinals(engine, child.source_id, [o + d for d in offsets], allowed)
        title = _title(next(iter(rows.values())).source_title) if rows else ""
        own = _part(child.chunk_id, o, child.locator, None, 0)
        base = _window_tokens(title, [own])
        span = max(0, child.token_count)
        parts: dict[int, WindowPart] = {o: own}
        cost = base  # what this window adds (title and the child's line included)
        stopped: set[int] = set()
        for d in offsets:
            sign = 1 if d > 0 else -1
            row = rows.get(o + d)
            if sign in stopped:
                continue
            if row is None or not _is_transcript(row.locator):
                stopped.add(sign)
                continue
            if row.chunk_id in numbered:
                part = _part(row.chunk_id, row.ordinal, row.locator, None, 0)
            else:
                part = _part(row.chunk_id, row.ordinal, row.locator, row.text, row.token_count)
            extra = 0 if row.chunk_id in shown else part.tokens
            size = max(0, row.token_count)
            if (
                span + size > WINDOW_TOKENS
                or spent + cost + extra > WINDOW_ANSWER_TOKENS
                or not fit_free(used, cost + extra, max_tokens=max_tokens)
            ):
                stopped.add(sign)
                continue
            parts[row.ordinal] = part
            span += size
            cost += extra
        if not any(p.text is not None for p in parts.values()):
            continue
        ordered = tuple(parts[k] for k in sorted(parts))
        windows.append(TranscriptWindow(child.source_id, title, ordered, cost))
        shown.update(p.chunk_id for p in ordered if p.text is not None)
        spent += cost
        used += cost
    return [
        TranscriptWindow(w.source_id, w.title, w.parts, _window_tokens(w.title, w.parts))
        for w in _merged(windows)
    ]


def expand(
    engine: Engine,
    chunks: Sequence[Child],
    ranked: Sequence[Scored],
    *,
    file_rows: FileRowsLookup,
    allowed_source_ids: Iterable[str],
    outline_on: bool = True,
    max_tokens: int = CONTEXT_TOKENS,
) -> Expansion:
    """Promotions, the outline and the transcript windows for the context ``chunks`` (see the
    module docstring).

    ``ranked``: the reranked candidates; a promoted chunk that was ranked keeps its score, and a
    class head over :data:`HEAD_UNGATED_TOKENS` is promoted only when it was ranked.
    """
    expanded = children(chunks)
    promoted: list[Promotion] = []
    blocks: list[OutlineBlock] = []
    if expanded:
        scores = {c.chunk_id: c.score for c in ranked}
        promoted = promotions(
            engine,
            chunks,
            expanded,
            scores,
            file_rows=file_rows,
            allowed_source_ids=allowed_source_ids,
            max_tokens=max_tokens,
        )
        if outline_on:
            context: list[Child | ChunkRow] = [*chunks, *(p.row for p in promoted)]
            blocks = outline(expanded, context, file_rows=file_rows)
    windows: list[TranscriptWindow] = []
    kids = transcript_children(chunks)
    if kids:
        # After the promotions, which keep first claim on the free budget.
        windows = transcript_windows(
            engine,
            [*chunks, *(p.row for p in promoted)],
            kids,
            allowed_source_ids=allowed_source_ids,
            max_tokens=max_tokens,
        )
    return Expansion(
        promoted=tuple(promoted),
        outline=tuple(blocks),
        promoted_tokens=sum(max(0, p.row.token_count) for p in promoted),
        outline_tokens=sum(b.tokens for b in blocks),
        windows=tuple(windows),
        window_tokens=sum(w.tokens for w in windows),
    )
