"""tree-sitter code chunker for Python, JavaScript, TypeScript/TSX and C# (SPEC §7.2, D-047).

- One chunk per function, method or class. A class chunk holds its header, docstring and field
  declarations (everything that is not a method or nested class); methods and nested classes are
  chunks of their own with the enclosing symbol (``Client.send``, ``Outer.Inner.run``).
- Decorators, attributes and the comment lines directly above a definition belong to it.
- A symbol over :data:`SYMBOL_MAX_TOKENS` is split at statement boundaries (at any depth, so a
  long ``try`` block can be split inside), and a single over-long statement between lines.
- A big data literal assigned to a name at module or class level (a dict/list/tuple/set, a JS/TS
  object or array, a C# initializer) over :data:`DATA_MIN_TOKENS` is a member of its own: it is
  split at element boundaries into balanced groups of about :data:`DATA_TARGET_TOKENS`, each named
  by the assignment (``_codes``, header ``_codes (part 3)``). Comments go with the element below.
- Module-level code (imports, constants, top-level statements) is grouped up to
  :data:`MODULE_TARGET_TOKENS`. C# and TypeScript namespaces are containers without a symbol of
  their own: their members are chunked like module-level code.
- Every chunk is a run of whole lines, copied verbatim from the snapshot, and starts with
  ``File: <path> · <symbol> (lines a-b)`` (``File: <path> (lines a-b)`` without a symbol), the
  first-line format the fallback chunker uses and the grounding code relies on.
- ``locator.kind`` says what the chunk holds: ``function``, ``method`` (a function inside a class),
  ``class`` (a class's own lines: header, docstring, fields), ``module`` (module-level code) or
  ``data`` (a split data literal).

Files with syntax errors and languages without a grammar use the fallback chunker (SPEC §7.3).
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.chunking.code import chunk_code
from app.chunking.document import ChunkDraft
from app.chunking.header import header_line
from app.chunking.tokens import TokenCounter

if TYPE_CHECKING:
    from tree_sitter import Language, Node

logger = logging.getLogger("trustbook.chunking")

SYMBOL_MAX_TOKENS = 1_500
MODULE_TARGET_TOKENS = 500
# One module-level statement (a big dict, an ``if TYPE_CHECKING:`` block) is split above this,
# the SPEC §7.2/§7.3 chunk maximum: it has no symbol to keep together.
MODULE_MAX_TOKENS = 800
# A data literal assigned at module or class level becomes its own member above this size, and is
# split at element boundaries into groups of about DATA_TARGET_TOKENS: one table row in a 750-token
# chunk is invisible to every retriever (ref-near-10, D-056).
DATA_MIN_TOKENS = 250
DATA_TARGET_TOKENS = 150

ChunkKind = Literal["function", "method", "class", "module", "data"]

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

# Collection literals in the four grammars: Python, JavaScript/TypeScript, C#.
_DATA_LITERALS = frozenset(
    {"dictionary", "list", "tuple", "set", "object", "array"}
    | {"initializer_expression", "collection_expression"}
)
# Nodes between an assignment and its literal: ``frozenset({...})``, ``Object.freeze([...])``,
# ``{...} as const``, ``new Dictionary<K, V> { ... }``, ``new[] { ... }``.
_DATA_THROUGH = frozenset(
    {"call", "call_expression", "argument_list", "arguments", "parenthesized_expression"}
    | {"as_expression", "satisfies_expression", "equals_value_clause"}
    | {"object_creation_expression", "array_creation_expression"}
    | {"implicit_array_creation_expression", "implicit_object_creation_expression"}
)
_DATA_ARGUMENTS = frozenset({"argument_list", "arguments"})
_DATA_ASSIGNMENTS = frozenset({"assignment", "assignment_expression"})
_DATA_TARGETS = frozenset({"identifier", "attribute", "member_expression", "property_identifier"})
# Declarations holding ``variable_declarator``s directly (JS/TS) or through a
# ``variable_declaration`` (C# fields and locals).
_DATA_DECLARATIONS = frozenset(
    {"lexical_declaration", "variable_declaration", "field_declaration"}
    | {"local_declaration_statement"}
)


def _literal_in(value: Node | None) -> Node | None:
    """The collection literal ``value`` is, or wraps (a call with one argument, a cast)."""
    node = value
    for _ in range(6):
        if node is None:
            return None
        if node.type in _DATA_LITERALS:
            return node
        if node.type not in _DATA_THROUGH:
            return None
        named = [c for c in node.named_children if c.type != "comment"]
        if node.type in _DATA_ARGUMENTS:
            node = named[0] if len(named) == 1 else None
            continue
        inner = [c for c in named if c.type in _DATA_LITERALS or c.type in _DATA_THROUGH]
        node = inner[-1] if inner else None
    return None


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

    kind: str  # "function" | "container" | "data"
    node: Node  # the definition itself (unwrapped); for data, the collection literal
    name: str
    first: int  # rows, including attached decorators and comments
    last: int


@dataclass(frozen=True)
class _Piece:
    first: int
    last: int
    symbol: str | None
    kind: ChunkKind
    part: int | None = None  # 1, 2, … for the groups of a split data literal


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
        data = self._data_assignment(node, inner)
        if data is not None:
            first, last = _rows(node)
            if last > first and self._sum(first, last) > DATA_MIN_TOKENS:
                return "data", data[1], data[0]
        return None

    def _data_assignment(self, node: Node, inner: Node) -> tuple[str, Node] | None:
        """(name, literal) when ``inner`` assigns one collection literal to one name."""
        kind = inner.type
        if kind in _DATA_LITERALS and inner is not node:  # ``export default {...}``
            return "default", inner
        target: Node | None = None
        value: Node | None = None
        if kind == "expression_statement":
            named = [c for c in inner.named_children if c.type != "comment"]
            if len(named) == 1 and named[0].type in _DATA_ASSIGNMENTS:
                target = named[0].child_by_field_name("left")
                value = named[0].child_by_field_name("right")
        elif kind in _DATA_DECLARATIONS:
            holder = inner
            if not any(c.type == "variable_declarator" for c in holder.named_children):
                holder = next(
                    (c for c in inner.named_children if c.type == "variable_declaration"), inner
                )
            declarators = [c for c in holder.named_children if c.type == "variable_declarator"]
            if len(declarators) == 1:
                target = declarators[0].child_by_field_name("name")
                value = declarators[0].child_by_field_name("value")
                if value is None:  # C#: ``Name = <expression>`` without a field name
                    rest = [c for c in declarators[0].named_children if c != target]
                    value = rest[-1] if rest else None
        elif kind in self.grammar.fields:
            target = inner.child_by_field_name("property") or inner.child_by_field_name("name")
            value = inner.child_by_field_name("value")
        if target is None or target.type not in _DATA_TARGETS:
            return None
        literal = _literal_in(value)
        name = _name(target)
        if literal is None or not name:
            return None
        return name, literal

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
        run_symbol: str | None,
        run_target: int,
    ) -> None:
        """Chunk rows ``first..last``: carved members recursively, the rest as code runs.

        Runs belong to the class named ``run_symbol`` (kind ``class``), or to the module.
        """
        items = self._flatten(children, owner)
        members = self._members(items)
        cuts = sorted({c.start_point[0] for c, _ in items if self._starts_line(c)})
        cursor = first
        for member in members:
            if member.first > cursor:
                self._run(cursor, member.first - 1, cuts, run_symbol, run_target)
            self._member(member, scope)
            cursor = member.last + 1
        if cursor <= last:
            self._run(cursor, last, cuts, run_symbol, run_target)

    def _member(self, member: _Member, scope: tuple[str, ...]) -> None:
        symbol = ".".join((*scope, member.name))
        if member.kind == "data":
            self._literal(member, symbol)
            return
        body = member.node.child_by_field_name("body")
        if member.kind == "function" or body is None:
            kind: ChunkKind = "class" if member.kind == "container" else "function"
            if kind == "function" and scope:
                kind = "method"
            self._function(member.first, member.last, symbol, kind)
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

    def _function(self, first: int, last: int, symbol: str, kind: ChunkKind) -> None:
        tokens = self._sum(first, last)
        if tokens <= SYMBOL_MAX_TOKENS:
            self._emit(first, last, symbol, kind)
            return
        parts = -(-tokens // SYMBOL_MAX_TOKENS)
        target = -(-tokens // parts)
        cuts = sorted(r for r in self.statement_rows if first < r <= last)
        for a, b in self._pack(first, last, cuts, target, SYMBOL_MAX_TOKENS):
            self._emit(a, b, symbol, kind)

    def _run(self, first: int, last: int, cuts: list[int], symbol: str | None, target: int) -> None:
        """Module-level code, or a class's own lines (``symbol``: the class)."""
        while first <= last and self._blank(first):
            first += 1
        while last >= first and self._blank(last):
            last -= 1
        if first > last or self._empty(first, last):
            return
        limit = SYMBOL_MAX_TOKENS if symbol else MODULE_MAX_TOKENS
        kind: ChunkKind = "class" if symbol else "module"
        inside = [r for r in cuts if first < r <= last]
        for a, b in self._pack(first, last, inside, target, limit):
            self._emit(a, b, symbol, kind)

    def _literal(self, member: _Member, symbol: str) -> None:
        """A data literal: balanced groups of whole elements, each named ``symbol`` (part n).

        Cuts only fall where an element starts a line; the comment lines directly before an
        element go with it, so a section comment never ends the previous group.
        """
        first, last = member.first, member.last
        total = self._sum(first, last)
        if total <= DATA_TARGET_TOKENS + DATA_TARGET_TOKENS // 2:
            self._emit(first, last, symbol, "data")
            return
        parts = -(-total // DATA_TARGET_TOKENS)
        target = -(-total // parts)
        cuts: set[int] = set()
        comments: int | None = None  # first row of the comment lines above the next element
        for child in member.node.named_children:
            if not self._starts_line(child):
                comments = None
                continue
            row = child.start_point[0]
            if child.type == "comment":
                comments = row if comments is None else comments
                continue
            cut = row if comments is None else comments
            comments = None
            if first < cut <= last:
                cuts.add(cut)
        groups = self._pack(first, last, sorted(cuts), target, SYMBOL_MAX_TOKENS, balanced=True)
        pieces = [(a, b) for a, b in groups if not self._empty(a, b)]
        for n, (a, b) in enumerate(pieces, start=1):
            self._emit(a, b, symbol, "data", n if len(pieces) > 1 else None)

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
        self,
        first: int,
        last: int,
        cuts: list[int],
        target: int,
        limit: int,
        *,
        balanced: bool = False,
    ) -> list[tuple[int, int]]:
        """Group consecutive units up to ``target`` tokens; units over ``limit`` are split.

        ``balanced`` (data literals): a unit also joins the group when that leaves the group
        closer to the target than it was, and a small last group joins the one before it.
        """
        units: list[tuple[int, int]] = []
        for a, b in self._units(first, last, cuts):
            units.extend(self._fine_units(a, b, limit))
        # A small group may grow past the target, by half of it at most (a 200-token import block
        # takes the 450-token statement after it rather than leaving it alone).
        stretch = min(limit, target + target // 2)
        groups: list[tuple[int, int, int]] = []  # first row, last row, tokens
        group: tuple[int, int] | None = None
        total = 0
        for a, b in units:
            size = self._sum(a, b)
            if group is not None:
                grown = total + size
                fits = grown <= target
                small = total < target // 2 and grown <= stretch
                closer = balanced and grown <= stretch and grown - target < target - total
                if fits or small or closer:
                    group, total = (group[0], b), grown
                    continue
                groups.append((group[0], group[1], total))
            group, total = (a, b), size
        if group is not None:
            groups.append((group[0], group[1], total))
        if balanced and len(groups) > 1:
            (a1, _, t1), (_, b2, t2) = groups[-2], groups[-1]
            if t2 < target // 2 and t1 + t2 <= stretch:
                groups[-2:] = [(a1, b2, t1 + t2)]
        return [(a, b) for a, b, _ in groups]

    def _emit(
        self, first: int, last: int, symbol: str | None, kind: ChunkKind, part: int | None = None
    ) -> None:
        while first < last and self._blank(first):
            first += 1
        while last > first and self._blank(last):
            last -= 1
        if not self._empty(first, last):
            self.pieces.append(_Piece(first, last, symbol, kind, part))

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
        body = header_line(path, piece.symbol, piece.first + 1, piece.last + 1, piece.part)
        body += "\n" + text[c_start:c_end]
        locator = {
            "file": path,
            "start_line": piece.first + 1,
            "end_line": piece.last + 1,
            "symbol": piece.symbol,
            "kind": piece.kind,
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
