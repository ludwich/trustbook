"""tree-sitter code chunker for Python, JavaScript, TypeScript/TSX and C# (SPEC §7.2, D-047).

- One chunk per function, method or class. A class chunk holds its header, docstring and field
  declarations (everything that is not a method or nested class); methods and nested classes are
  chunks of their own with the enclosing symbol (``Client.send``, ``Outer.Inner.run``).
- Decorators, attributes and the comment lines directly above a definition belong to it.
- A symbol over :data:`SYMBOL_MAX_TOKENS` is split at statement boundaries (at any depth, so a
  long ``try`` block can be split inside), and a single over-long statement between lines.
- Module-level code (imports, constants, top-level statements) is grouped up to
  :data:`MODULE_TARGET_TOKENS`. C# and TypeScript namespaces are containers without a symbol of
  their own: their members are chunked like module-level code.
- Every chunk is a run of whole lines, copied verbatim from the snapshot, and starts with
  ``File: <path> · <symbol> (lines a-b)`` (``File: <path> (lines a-b)`` without a symbol), the
  first-line format the fallback chunker uses and the grounding code relies on.

Files with syntax errors and languages without a grammar use the fallback chunker (SPEC §7.3).
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.chunking.code import chunk_code
from app.chunking.document import ChunkDraft
from app.chunking.tokens import TokenCounter

if TYPE_CHECKING:
    from tree_sitter import Language, Node

logger = logging.getLogger("trustbook.chunking")

SYMBOL_MAX_TOKENS = 1_500
MODULE_TARGET_TOKENS = 500
# One module-level statement (a big dict, an ``if TYPE_CHECKING:`` block) is split above this,
# the SPEC §7.2/§7.3 chunk maximum: it has no symbol to keep together.
MODULE_MAX_TOKENS = 800

LANGUAGE_BY_EXTENSION = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "tsx",
    "cs": "c_sharp",
}

# Lines that carry no content of their own (a class's closing brace).
_PUNCTUATION_ONLY = re.compile(r"[\s{}\[\]();,]*")
_STATEMENT_SUFFIXES = ("_statement", "_declaration", "_definition")
_STATEMENT_TYPES = frozenset(
    {
        "comment",
        "decorated_definition",
        "method_definition",
        "else_clause",
        "elif_clause",
        "except_clause",
        "finally_clause",
        "catch_clause",
        "switch_case",
        "switch_default",
        "switch_section",
        "case_clause",
    }
)
_ATTACHED_ABOVE = frozenset({"comment", "decorator"})


@dataclass(frozen=True)
class Grammar:
    """Which node types are functions, classes and namespaces in one language."""

    functions: frozenset[str]
    containers: frozenset[str]
    namespaces: frozenset[str] = frozenset()
    wrappers: frozenset[str] = frozenset()
    variables: frozenset[str] = frozenset()  # ``const f = () => …``
    fields: frozenset[str] = frozenset()  # ``handle = () => …`` in a class body
    function_values: frozenset[str] = frozenset()
    properties: frozenset[str] = frozenset()  # C#: a method when its accessors have bodies


_JS_FUNCTIONS = frozenset({"function_declaration", "generator_function_declaration"})
_JS_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})

_JAVASCRIPT = Grammar(
    functions=_JS_FUNCTIONS | {"method_definition"},
    containers=frozenset({"class_declaration"}),
    wrappers=frozenset({"export_statement"}),
    variables=frozenset({"lexical_declaration", "variable_declaration"}),
    fields=frozenset({"field_definition"}),
    function_values=_JS_VALUES,
)
_TYPESCRIPT = Grammar(
    functions=_JS_FUNCTIONS | {"method_definition"},
    containers=frozenset(
        {"class_declaration", "abstract_class_declaration", "interface_declaration"}
        | {"enum_declaration"}
    ),
    namespaces=frozenset({"internal_module", "module"}),
    wrappers=frozenset({"export_statement", "expression_statement", "ambient_declaration"}),
    variables=frozenset({"lexical_declaration", "variable_declaration"}),
    fields=frozenset({"field_definition", "public_field_definition"}),
    function_values=_JS_VALUES,
)
GRAMMARS: dict[str, Grammar] = {
    "python": Grammar(
        functions=frozenset({"function_definition"}),
        containers=frozenset({"class_definition"}),
        wrappers=frozenset({"decorated_definition"}),
    ),
    "javascript": _JAVASCRIPT,
    "typescript": _TYPESCRIPT,
    "tsx": _TYPESCRIPT,
    "c_sharp": Grammar(
        functions=frozenset(
            {"method_declaration", "constructor_declaration", "destructor_declaration"}
            | {"operator_declaration", "conversion_operator_declaration"}
            | {"indexer_declaration", "local_function_statement"}
        ),
        containers=frozenset(
            {"class_declaration", "struct_declaration", "record_declaration"}
            | {"record_struct_declaration", "interface_declaration", "enum_declaration"}
        ),
        namespaces=frozenset({"namespace_declaration"}),
        wrappers=frozenset({"global_statement"}),
        properties=frozenset({"property_declaration"}),
    ),
}


def language_for_path(path: str) -> str | None:
    """The tree-sitter language for ``path`` (``None``: use the fallback chunker)."""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    return LANGUAGE_BY_EXTENSION.get(name.rsplit(".", 1)[-1].lower())


@functools.cache
def _language(name: str) -> Language | None:
    """The compiled grammar (``None`` if its package is missing: the fallback is used)."""
    try:
        from tree_sitter import Language

        if name == "python":
            import tree_sitter_python as grammar

            return Language(grammar.language())
        if name == "javascript":
            import tree_sitter_javascript as grammar_js

            return Language(grammar_js.language())
        if name in ("typescript", "tsx"):
            import tree_sitter_typescript as grammar_ts

            raw = grammar_ts.language_tsx() if name == "tsx" else grammar_ts.language_typescript()
            return Language(raw)
        if name == "c_sharp":
            import tree_sitter_c_sharp as grammar_cs

            return Language(grammar_cs.language())
    except (ImportError, ValueError):
        logger.warning("No tree-sitter grammar for %s; using the fallback chunker", name)
    return None


def _rows(node: Node) -> tuple[int, int]:
    """First and last line (0-based) of ``node``; a node ending at column 0 ends a line earlier."""
    first, last = node.start_point[0], node.end_point[0]
    if last > first and node.end_point[1] == 0:
        last -= 1
    return first, last


def _name(node: Node | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.decode("utf-8", errors="replace")


def _is_statement(kind: str) -> bool:
    return kind.endswith(_STATEMENT_SUFFIXES) or kind in _STATEMENT_TYPES


@dataclass(frozen=True)
class _Member:
    """A definition carved out of its container: its own chunk(s)."""

    kind: str  # "function" | "container"
    node: Node  # the definition itself (unwrapped)
    name: str
    first: int  # rows, including attached decorators and comments
    last: int


@dataclass(frozen=True)
class _Piece:
    first: int
    last: int
    symbol: str | None


class _FileChunker:
    """Chunks one parsed file. Rows are 0-based line numbers within the file."""

    def __init__(
        self, text: str, lines: list[tuple[int, int]], counter: TokenCounter, grammar: Grammar
    ) -> None:
        self.text = text
        self.lines = lines
        self.line_text = [text[s:e] for s, e in lines]
        self.tokens = [counter.count(line) + 1 for line in self.line_text]
        self.grammar = grammar
        self.statement_rows: set[int] = set()
        self.pieces: list[_Piece] = []

    # --- line helpers ------------------------------------------------------------------------

    def _starts_line(self, node: Node) -> bool:
        row, column = node.start_point
        return row < len(self.line_text) and not self.line_text[row][:column].strip()

    def _blank(self, row: int) -> bool:
        return not self.line_text[row].strip()

    def _empty(self, first: int, last: int) -> bool:
        return all(_PUNCTUATION_ONLY.fullmatch(self.line_text[r]) for r in range(first, last + 1))

    def _sum(self, first: int, last: int) -> int:
        return sum(self.tokens[first : last + 1])

    def _collect_statement_rows(self, root: Node) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.is_named and _is_statement(node.type) and self._starts_line(node):
                self.statement_rows.add(node.start_point[0])
            stack.extend(node.children)

    # --- classification ----------------------------------------------------------------------

    def _unwrap(self, node: Node) -> Node:
        """The declaration inside ``export …``, ``@decorator def …``, ``namespace`` statements."""
        grammar = self.grammar
        while node.type in grammar.wrappers:
            inner = node.child_by_field_name("declaration") or node.child_by_field_name(
                "definition"
            )
            if inner is None:
                named = [c for c in node.named_children if c.type not in _ATTACHED_ABOVE]
                inner = named[0] if len(named) == 1 else None
            if inner is None:
                return node
            if node.type == "expression_statement" and inner.type not in grammar.namespaces:
                return node
            node = inner
        return node

    def _has_body(self, node: Node) -> bool:
        if node.child_by_field_name("body") is not None:
            return True
        return any(c.type in ("block", "arrow_expression_clause") for c in node.children)

    def _function_value(self, node: Node, field: str) -> Node | None:
        value = node.child_by_field_name(field)
        return value if value is not None and value.type in self.grammar.function_values else None

    def _classify(self, node: Node) -> tuple[str, Node, str] | None:
        """(kind, definition, name) of a member that gets its own chunk, else ``None``."""
        grammar = self.grammar
        inner = self._unwrap(node)
        kind = inner.type
        if kind in grammar.containers:
            return "container", inner, _name(inner.child_by_field_name("name")) or "(anonymous)"
        if kind in grammar.namespaces:
            return "namespace", inner, ""
        if kind in grammar.functions and self._has_body(inner):
            return "function", inner, self._function_name(inner)
        if kind in grammar.properties and self._bodied_property(inner):
            return "function", inner, _name(inner.child_by_field_name("name")) or "(property)"
        if kind in grammar.variables:
            declarators = [c for c in inner.named_children if c.type == "variable_declarator"]
            if len(declarators) == 1 and self._function_value(declarators[0], "value"):
                name = _name(declarators[0].child_by_field_name("name"))
                return "function", inner, name or "(anonymous)"
        if kind in grammar.fields and self._function_value(inner, "value") is not None:
            name = _name(inner.child_by_field_name("property") or inner.child_by_field_name("name"))
            return "function", inner, name or "(anonymous)"
        return None

    def _function_name(self, node: Node) -> str:
        name = _name(node.child_by_field_name("name"))
        if name:
            return name
        if node.type == "operator_declaration":
            return "operator " + (_name(node.child_by_field_name("operator")) or "")
        if node.type == "conversion_operator_declaration":
            return "operator " + (_name(node.child_by_field_name("type")) or "")
        if node.type == "indexer_declaration":
            return "this[]"
        return "(anonymous)"

    def _bodied_property(self, node: Node) -> bool:
        """A C# property whose getter or setter has code (not ``{ get; set; }``), over 1 line."""
        first, last = _rows(node)
        if last == first:
            return False
        accessors = next((c for c in node.named_children if c.type == "accessor_list"), None)
        if accessors is None:
            return any(c.type == "arrow_expression_clause" for c in node.named_children)
        return any(
            child.type in ("block", "arrow_expression_clause")
            for accessor in accessors.named_children
            for child in accessor.named_children
        )

    # --- carving -----------------------------------------------------------------------------

    def _attached_first(self, node: Node, first: int) -> int:
        """Extend ``first`` up over the decorators and comment lines directly above ``node``."""
        sibling = node.prev_named_sibling
        while (
            sibling is not None
            and sibling.type in _ATTACHED_ABOVE
            and _rows(sibling)[1] >= first - 1
            and self._starts_line(sibling)
        ):
            first = sibling.start_point[0]
            sibling = sibling.prev_named_sibling
        return first

    def _flatten(self, children: list[Node], owner: Node | None) -> list[tuple[Node, Node | None]]:
        """``children`` with each namespace replaced by its members (paired with their owner).

        A namespace has no chunk of its own: its header line joins the module-level code around
        it and its members are chunked as if they were at the top level.
        """
        items: list[tuple[Node, Node | None]] = []
        for child in children:
            found = self._classify(child)
            body = None
            if found is not None and found[0] == "namespace":
                body = found[1].child_by_field_name("body")
            if body is not None:
                items.extend(self._flatten(list(body.named_children), body))
            else:
                items.append((child, owner))
        return items

    def _members(self, items: list[tuple[Node, Node | None]]) -> list[_Member]:
        """The items that get their own chunks, each paired with its owner (``None``: the file).

        A member is only carved out when it owns its lines: nothing else on its first or last line
        (one-line classes, ``class K { m() {} }``, stay whole).
        """
        members: list[_Member] = []
        previous_end = -1
        for i, (child, owner) in enumerate(items):
            if child.type in _ATTACHED_ABOVE:
                continue
            found = self._classify(child)
            if found is None or found[0] == "namespace":
                previous_end = max(previous_end, _rows(child)[1])
                continue
            kind, inner, name = found
            first, last = _rows(child)
            first = self._attached_first(child, first)
            nxt = items[i + 1][0] if i + 1 < len(items) else None
            owns_lines = (
                self._starts_line(child)
                and first > previous_end
                # a trailing comment on the last line may stay; any other code may not
                and (nxt is None or nxt.start_point[0] > last or nxt.type == "comment")
            )
            if owner is not None:
                owner_first, owner_last = _rows(owner)
                ends_owner = child.end_byte >= owner.end_byte
                inside = first > owner_first and (last < owner_last or ends_owner)
                owns_lines = owns_lines and inside
            if owns_lines:
                members.append(_Member(kind, inner, name, first, last))
            previous_end = max(previous_end, last)
        return members

    def _region(
        self,
        first: int,
        last: int,
        children: list[Node],
        owner: Node | None,
        scope: tuple[str, ...],
        data_symbol: str | None,
        data_target: int,
    ) -> None:
        """Chunk rows ``first..last``: carved members recursively, the rest as data runs."""
        items = self._flatten(children, owner)
        members = self._members(items)
        cuts = sorted({c.start_point[0] for c, _ in items if self._starts_line(c)})
        cursor = first
        for member in members:
            if member.first > cursor:
                self._data(cursor, member.first - 1, cuts, data_symbol, data_target)
            self._member(member, scope)
            cursor = member.last + 1
        if cursor <= last:
            self._data(cursor, last, cuts, data_symbol, data_target)

    def _member(self, member: _Member, scope: tuple[str, ...]) -> None:
        symbol = ".".join((*scope, member.name))
        body = member.node.child_by_field_name("body")
        if member.kind == "function" or body is None:
            self._function(member.first, member.last, symbol)
            return
        self._region(
            member.first,
            member.last,
            list(body.named_children),
            member.node,
            (*scope, member.name),
            symbol,
            SYMBOL_MAX_TOKENS,
        )

    # --- packing -----------------------------------------------------------------------------

    def _function(self, first: int, last: int, symbol: str) -> None:
        tokens = self._sum(first, last)
        if tokens <= SYMBOL_MAX_TOKENS:
            self._emit(first, last, symbol)
            return
        parts = -(-tokens // SYMBOL_MAX_TOKENS)
        target = -(-tokens // parts)
        cuts = sorted(r for r in self.statement_rows if first < r <= last)
        self._pack(first, last, cuts, target, symbol)

    def _data(
        self, first: int, last: int, cuts: list[int], symbol: str | None, target: int
    ) -> None:
        while first <= last and self._blank(first):
            first += 1
        while last >= first and self._blank(last):
            last -= 1
        if first > last or self._empty(first, last):
            return
        self._pack(first, last, [r for r in cuts if first < r <= last], target, symbol)

    def _units(self, first: int, last: int, cuts: Iterable[int]) -> Iterator[tuple[int, int]]:
        """Row ranges of ``first..last`` split at ``cuts``."""
        start = first
        for cut in cuts:
            if start < cut <= last:
                yield start, cut - 1
                start = cut
        yield start, last

    def _fine_units(self, first: int, last: int, limit: int) -> list[tuple[int, int]]:
        """Units of at most ``limit`` tokens: statements at any depth, then lines."""
        if self._sum(first, last) <= limit:
            return [(first, last)]
        deeper = sorted(r for r in self.statement_rows if first < r <= last)
        units: list[tuple[int, int]] = []
        for a, b in self._units(first, last, deeper):
            if self._sum(a, b) <= limit:
                units.append((a, b))
            else:
                units.extend((r, r) for r in range(a, b + 1))
        return units

    def _pack(
        self, first: int, last: int, cuts: list[int], target: int, symbol: str | None
    ) -> None:
        """Group consecutive units up to ``target`` tokens; split units over the limit.

        Module-level code (no symbol) is split above :data:`MODULE_MAX_TOKENS`, symbols above
        :data:`SYMBOL_MAX_TOKENS`.
        """
        limit = SYMBOL_MAX_TOKENS if symbol else MODULE_MAX_TOKENS
        units: list[tuple[int, int]] = []
        for a, b in self._units(first, last, cuts):
            units.extend(self._fine_units(a, b, limit))
        # A small group may grow past the target, by half of it at most (a 200-token import block
        # takes the 450-token statement after it rather than leaving it alone).
        stretch = min(limit, target + target // 2)
        group: tuple[int, int] | None = None
        total = 0
        for a, b in units:
            size = self._sum(a, b)
            if group is not None:
                fits = total + size <= target
                small = total < target // 2 and total + size <= stretch
                if fits or small:
                    group, total = (group[0], b), total + size
                    continue
                self._emit(group[0], group[1], symbol)
            group, total = (a, b), size
        if group is not None:
            self._emit(group[0], group[1], symbol)

    def _emit(self, first: int, last: int, symbol: str | None) -> None:
        while first < last and self._blank(first):
            first += 1
        while last > first and self._blank(last):
            last -= 1
        if not self._empty(first, last):
            self.pieces.append(_Piece(first, last, symbol))

    def chunk(self, root: Node) -> list[_Piece]:
        self._collect_statement_rows(root)
        self._region(
            0,
            len(self.lines) - 1,
            list(root.named_children),
            None,
            (),
            None,
            MODULE_TARGET_TOKENS,
        )
        return sorted(self.pieces, key=lambda p: p.first)


def _line_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    while True:
        newline = text.find("\n", cursor, end)
        if newline == -1:
            spans.append((cursor, end))
            return spans
        spans.append((cursor, newline))
        cursor = newline + 1


def header_line(path: str, symbol: str | None, first_line: int, last_line: int) -> str:
    """``File: path · symbol (lines a-b)``: the first line of every code chunk."""
    where = f"{path} · {symbol}" if symbol else path
    return f"File: {where} (lines {first_line}-{last_line})"


def chunk_syntax(
    text: str, start: int, end: int, path: str, counter: TokenCounter, language: str
) -> list[ChunkDraft] | None:
    """tree-sitter chunks of the file at ``text[start:end]``, or ``None`` when it does not parse."""
    compiled = _language(language)
    if compiled is None:
        return None
    from tree_sitter import Parser

    tree = Parser(compiled).parse(text[start:end].encode("utf-8"))
    if tree.root_node.has_error:
        return None
    lines = _line_spans(text, start, end)
    pieces = _FileChunker(text, lines, counter, GRAMMARS[language]).chunk(tree.root_node)
    drafts: list[ChunkDraft] = []
    for piece in pieces:
        c_start, c_end = lines[piece.first][0], lines[piece.last][1]
        body = header_line(path, piece.symbol, piece.first + 1, piece.last + 1) + "\n"
        body += text[c_start:c_end]
        locator = {
            "file": path,
            "start_line": piece.first + 1,
            "end_line": piece.last + 1,
            "symbol": piece.symbol,
        }
        drafts.append(ChunkDraft(body, c_start, c_end, locator, counter.count(body)))
    return drafts


def chunk_source_code(
    text: str, start: int, end: int, path: str, counter: TokenCounter
) -> list[ChunkDraft]:
    """Chunks of one code file: tree-sitter when the language has a grammar, else SPEC §7.3."""
    language = language_for_path(path)
    if language is not None:
        try:
            drafts = chunk_syntax(text, start, end, path, counter, language)
        except Exception:
            logger.warning("tree-sitter chunking failed for a %s file", language, exc_info=True)
            drafts = None
        if drafts:
            return drafts
    return chunk_code(text, start, end, path, counter)
