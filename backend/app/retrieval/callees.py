"""Pull in the bodies of functions that the context's code calls (D-060; D-032 for functions).

Code is layered: the chunks that match a question's wording are often thin call sites
(``headers["Authorization"] = _basic_auth_str(user, password)``, a ``__call__``, an adapter, a
test), while the logic that answers it sits in the helper they call. Without the helper's body the
model can only answer with its name, cite a call site, or find nothing to say.

After the context is chosen (and the D-032 constants are added), the calls in each code chunk are
collected: bare calls ``name(``, calls on the enclosing class's receiver ``self.name(`` /
``this.name(`` / ``cls.name(``, and qualified calls ``Q.name(`` (``AuthHelpers.BuildBasic(``,
``utils.basic_auth(``, ``pkg.Func(``, ``a.b.Q.name(``; ``Q`` is the last qualifier). Only chunks
made by a code chunker count (their first line is ``File: … (lines a-b)`` and their locator has no
``heading``, which every document file's chunk has since D-068); examples in a document file are
not the source's code. A callee is added when a code chunk of the **same
source**

- is a function or method: ``locator.kind`` when the chunker records it, otherwise a ``def`` /
  ``function`` / method-signature / ``procedure`` line for that name in its text;
- is the one the call names: its ``locator.symbol`` is ``name`` (bare call), ``Class.name`` for
  the caller's class (receiver or bare call), or ``Q.name`` (qualified call). A qualified call
  also names a module-level ``name`` (no class in the symbol) whose file is ``Q`` (``Q.py``,
  ``Q.java``) or lies in a folder ``Q`` (a Go package, ``Q/__init__.py``). A chunk without a
  symbol counts only if the fallback chunker made it (no grammar for the file): the syntax
  chunker's symbol-less chunks are module-level code. It is then matched by the definition line
  in its text, and for a qualified call its file or folder must also be ``Q``;
- is among this question's reranked candidates (the fused top 40): the relevance gate. Without it
  about half of all questions got unrelated helpers (fixtures, class bodies) in the measurement;
- is not in a test file, and is not already in the context.

Callees called from more context chunks come first, then by rerank position; a name defined in
several files prefers the caller's file. At most :data:`MAX_CALLEES` are added, within the token
budget, through the same :func:`app.retrieval.definitions.fit_extras` as the constants (the top
chunk, the calling chunks and the constants are never displaced). One level only: callees of
callees are not followed. They are ordinary numbered chunks with their rerank score: the model
may cite them and every grounding layer treats them like any other chunk; the refusal threshold
still reads only the ranked list.

With the retrieval's per-file row cache (D-077, D-078) the lookup also reads the stored structure:

- ``Q.name(`` also names a module-level ``name`` in the file the caller's module-level import of
  ``Q`` resolves to (``from . import utils as u`` → ``u.f(``), and that file comes first among
  the matches; D-062's file-or-folder rule for ``Q`` still applies (C# ``using`` and Java
  packages name namespaces, not files);
- receiver calls prefer the caller's class family (its parent key), the same class
  ``enclosing_class`` names, nested classes included;
- a candidate without a symbol is matched by its stored ``defines`` instead of the definition
  regex.

Rows without structure (indexed before chunker p3-treesitter-6, fallback-chunked languages) and
calls without the cache take the path above unchanged. The candidate pool, test-file exclusion,
:data:`MAX_CALLEES` and the ordering by callers stay the policy.
"""

from __future__ import annotations

import builtins
import keyword
import re
from collections.abc import Collection, Sequence
from typing import NamedTuple

from app.chunking.syntax import language_for_path
from app.retrieval.definitions import ContextChunk, code_body, code_file, structured_rows
from app.retrieval.store import FileRows

MAX_CALLEES = 2
FUNCTION_KINDS = frozenset({"function", "method"})
CONTAINER_KINDS = frozenset({"class"})

# ``name(`` with an optional chain of qualifiers ``a.b.name(``. The chain must start a name (not
# ``x[0].name(`` or ``f().name(``: the receiver is a value whose type is unknown).
_CALL = re.compile(r"(?<![\w.$@])((?:[A-Za-z_]\w*[ \t]*\.[ \t]*)*)([A-Za-z_]\w*)\s*\(")
_RECEIVERS = frozenset({"self", "this", "cls"})
# Qualifiers that name the parent class or an instance of it, never a file or a class symbol.
_NOT_QUALIFIERS = frozenset({"super", "base"})
_NEW = re.compile(r"\bnew\s*$")
# The first line of a code chunk (both code chunkers); a document file's chunks have no line range.
_CODE_HEADER = re.compile(r"File: [^\n]*\(lines \d+-\d+\)(?:\n|$)")

# Words that look like calls but are not: control flow and operators of the supported languages.
_NOT_CALLS = (
    frozenset(keyword.kwlist)
    | frozenset(dir(builtins))
    | frozenset(
        {
            "if", "for", "foreach", "while", "switch", "catch", "return", "typeof", "sizeof",
            "nameof", "function", "lock", "using", "fixed", "await", "yield", "throw", "new",
            "super", "base", "checked", "unchecked", "default", "when", "do", "else", "case",
            "require", "import", "constructor", "procedure", "trigger", "begin", "end",
        }
    )
)  # fmt: skip

# Words that can stand before ``name(`` but are not a return type (C#/TS signature heuristic).
_NOT_TYPES = "|".join(
    sorted(
        set(keyword.kwlist)
        | {
            "return", "await", "new", "throw", "yield", "else", "case", "in", "of", "typeof",
            "class", "struct", "interface", "record", "enum", "namespace", "using", "goto", "is",
            "as", "out", "ref", "params", "var", "let", "const", "delegate", "event", "operator",
            "if", "for", "foreach", "while", "switch", "catch", "lock", "when", "print", "exec",
        },
        key=lambda word: (-len(word), word),
    )
)  # fmt: skip
_DEFINITIONS = [
    # Python
    re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)[ \t]*\(", re.MULTILINE),
    # JavaScript / TypeScript function declarations
    re.compile(r"\bfunction\b[ \t]*\*?[ \t]*([A-Za-z_$][\w$]*)[ \t]*[<(]"),
    # const name = function … / const name = (…) => / const name = x =>
    re.compile(
        r"^[ \t]*(?:export[ \t]+)?(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)[ \t]*(?::[^=\n]+)?="
        r"[ \t]*(?:async[ \t]+)?(?:function\b|\([^)\n]*\)[ \t]*(?::[^=\n]+)?=>|[A-Za-z_$][\w$]*"
        r"[ \t]*=>)",
        re.MULTILINE,
    ),
    # C# / Java / TypeScript methods: modifiers, a return type, then the name and "(" (one line).
    re.compile(
        r"^[ \t]*(?:\[[^\]\n]*\][ \t]*)*"
        r"(?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|"
        r"extern|unsafe|partial|readonly|final|synchronized)[ \t]+)*"
        rf"(?!(?:{_NOT_TYPES})\b)[A-Za-z_][\w<>\[\],.?]*[ \t]+([A-Za-z_]\w*)[ \t]*(?:<[^>\n]*>)?"
        r"[ \t]*\([^;\n]*$",
        re.MULTILINE,
    ),
    # TypeScript / JavaScript class methods: name(…) { or name(…): Type {
    re.compile(
        r"^[ \t]*(?:(?:public|private|protected|static|async|override|readonly|get|set)[ \t]+)*"
        r"([A-Za-z_$][\w$]*)[ \t]*(?:<[^>\n]*>)?[ \t]*\([^)\n]*\)[ \t]*(?::[^{\n]+)?\{",
        re.MULTILINE,
    ),
    # AL / Pascal-like procedures
    re.compile(
        r"^[ \t]*(?:(?:local|internal)[ \t]+)?(?:procedure|function)[ \t]+([A-Za-z_]\w*)[ \t]*\(",
        re.MULTILINE | re.IGNORECASE,
    ),
]
_TEST_PATH = re.compile(
    r"(?:^|/)(?:tests?|testing|__tests__|spec|specs)/"
    r"|(?:^|/)test_[^/]*\.py$|_test\.(?:py|go|js|ts)$|(?:^|/)conftest\.py$"
    r"|\.(?:test|spec)\.[jt]sx?$"
    r"|(?:^|/)[^/]*(?-i:Tests?)(?:\.cs|/)",
    re.IGNORECASE,
)


def defined_functions(code: str) -> set[str]:
    """Function and method names that ``code`` defines (by syntax, any supported language)."""
    names: set[str] = set()
    for pattern in _DEFINITIONS:
        names.update(match.group(1) for match in pattern.finditer(code))
    return names - _NOT_CALLS


def code_path(chunk: ContextChunk) -> str | None:
    """The file of a chunk made by a code chunker, else ``None`` (documents such as ``.rst`` files
    also have a file and lines, and their examples are not the source's code)."""
    file = code_file(chunk.locator)
    if file is None or "heading" in chunk.locator or not _CODE_HEADER.match(chunk.text):
        return None  # a document file's header names lines too (D-068); its locator a heading
    return file


def is_test_file(path: str) -> bool:
    """Whether ``path`` looks like a test, fixture or spec file."""
    return _TEST_PATH.search(path) is not None


def _symbol(locator: dict[str, object]) -> str:
    symbol = locator.get("symbol")
    return symbol if isinstance(symbol, str) else ""


def _kind(locator: dict[str, object]) -> str | None:
    kind = locator.get("kind")
    return kind if isinstance(kind, str) and kind else None


def enclosing_class(locator: dict[str, object]) -> str:
    """The class a code chunk belongs to (``Client`` for ``Client.send``), "" when none."""
    symbol = _symbol(locator)
    if _kind(locator) in CONTAINER_KINDS:
        return symbol
    return symbol.rpartition(".")[0]


class Call(NamedTuple):
    """A call in a code chunk.

    ``name``: the called function's name. ``symbols``: the ``locator.symbol`` values it may refer
    to. ``qualifier``: ``Q`` of a qualified call ``Q.name(``, else ``None``; the module or package a
    module-level function must be in (its file ``Q.*`` or a folder ``Q``).
    """

    name: str
    symbols: tuple[str, ...]
    qualifier: str | None = None


def called_names(chunk: ContextChunk) -> list[Call]:
    """The calls in a code chunk, in order, unique.

    ``self.f(`` in a method of ``A`` may only be ``A.f``; a bare ``f(`` may be ``f`` or, in
    languages with an implicit receiver (C#, Java), ``A.f``; ``Q.f(`` may be ``Q.f`` or a function
    ``f`` of module ``Q``. Calls to names the chunk defines itself, constructors (``new X(``) and
    language keywords are left out.
    """
    body = code_body(chunk.text)
    symbol = _symbol(chunk.locator)
    own = defined_functions(body) | {symbol.rpartition(".")[2]}
    owner = enclosing_class(chunk.locator)
    found: dict[Call, None] = {}
    for match in _CALL.finditer(body):
        chain, name = match.group(1), match.group(2)
        if name in _NOT_CALLS:
            continue
        line_start = body.rfind("\n", 0, match.start()) + 1
        if _NEW.search(body, line_start, match.start()):
            continue
        qualifiers = [part.strip() for part in chain.split(".") if part.strip()]
        if not qualifiers:
            if name not in own:
                symbols = (name, f"{owner}.{name}") if owner else (name,)
                found.setdefault(Call(name, symbols), None)
        elif qualifiers == [qualifiers[0]] and qualifiers[0] in _RECEIVERS:
            if owner and name not in own:
                found.setdefault(Call(name, (f"{owner}.{name}",)), None)
        else:
            qualifier = qualifiers[-1]
            if qualifier in _NOT_QUALIFIERS or qualifier in _RECEIVERS:
                continue
            if f"{qualifier}.{name}" == symbol:
                continue
            found.setdefault(Call(name, (f"{qualifier}.{name}",), qualifier), None)
    return list(found)


def in_module(path: str, qualifier: str) -> bool:
    """Whether file ``path`` is module ``qualifier``: named ``qualifier.*`` or in a folder of it."""
    folder, _, name = path.rpartition("/")
    return name.partition(".")[0] == qualifier or folder.rpartition("/")[2] == qualifier


def defines(
    chunk: ContextChunk,
    call: Call,
    defined: Collection[str] | None = None,
    target: str | None = None,
) -> bool:
    """Whether code chunk ``chunk`` is the definition of the function or method ``call`` names.

    With a ``locator.kind`` it must be a function or method; with a ``locator.symbol`` that must be
    one of ``call.symbols``, or, for a qualified call, ``call.name`` in module ``call.qualifier``.
    A chunk without a symbol counts only when the fallback chunker made it (no grammar for the
    file): the syntax chunker gives every function its own symbol, so its symbol-less chunks are
    module-level code (imports, conditional stubs); for a qualified call its file must be the
    module. The text must define the name unless the chunker recorded the kind. ``defined``: the
    function names the chunk's text defines, when already known (computed here otherwise).
    ``target``: the file the caller's import of ``call.qualifier`` resolves to (D-078), which is
    that module too.
    """
    symbol = _symbol(chunk.locator)
    kind = _kind(chunk.locator)
    file = code_file(chunk.locator) or ""

    def is_module(qualifier: str) -> bool:
        return in_module(file, qualifier) or (target is not None and file == target)

    if kind is not None and kind not in FUNCTION_KINDS:
        return False
    if symbol:
        in_its_module = (
            call.qualifier is not None and symbol == call.name and is_module(call.qualifier)
        )
        if symbol not in call.symbols and not in_its_module:
            return False
        if kind is not None:
            return True
    elif kind is None and language_for_path(file) is not None:
        return False
    elif call.qualifier is not None and not is_module(call.qualifier):
        return False
    if defined is None:
        defined = defined_functions(code_body(chunk.text))
    return call.name in defined


def _in_class(chunk: ContextChunk) -> bool:
    """Whether the chunk belongs to a class family by its stored structure (D-077)."""
    return chunk.parent_key is not None and (
        chunk.structure is not None and chunk.structure.kind == "class"
    )


def imported_modules(file_rows: FileRows, source_id: str, file: str) -> dict[str, str]:
    """``{bound name: file}`` of the module-level imports of ``file`` that resolve to a file of the
    same source (D-077), from the file's stored structure; ``{}`` without one."""
    found: dict[str, str] = {}
    for row in structured_rows(file_rows, source_id, file):
        if row.structure is None or row.structure.kind != "module":
            continue
        for item in row.structure.imports:
            if item.target is not None:
                for name in item.names:
                    found.setdefault(name, item.target)
    return found


def callee_extras[C: ContextChunk](
    selected: Sequence[C],
    ranked: Sequence[C],
    *,
    max_callees: int = MAX_CALLEES,
    file_rows: FileRows | None = None,
) -> list[tuple[C, str, tuple[str, ...]]]:
    """Up to ``max_callees`` ``(chunk, name, caller ids)`` of functions the context's code calls.

    Callees come from ``ranked`` only (the relevance gate), from the caller's source, outside test
    files and the context. Most context callers first, then the best rerank position; a name
    defined in several files prefers the caller's file. Each candidate's text is scanned for
    definitions once, and calls are looked up by name, so the cost grows with the context and the
    candidates, not with their product.

    ``file_rows``: the retrieval's per-file row cache for lookups through parent keys (D-077,
    D-078: import targets, class families, stored ``defines``); ``None`` keeps the lookup
    without parent keys.
    """
    taken = {c.chunk_id for c in selected}
    position = {c.chunk_id: i for i, c in enumerate(ranked)}
    # Candidates by (source, function name): a chunk with a symbol under its last part, a
    # symbol-less one under every function its text defines (scanned once, kept for ``defines``).
    by_name: dict[tuple[str, str], list[C]] = {}
    defined: dict[str, set[str]] = {}
    for c in ranked:
        file = code_path(c)
        if c.chunk_id in taken or file is None or is_test_file(file):
            continue
        symbol = _symbol(c.locator)
        if symbol:
            names = {symbol.rpartition(".")[2]}
        elif file_rows is not None and c.structure is not None:
            names = defined[c.chunk_id] = set(c.structure.defines)  # D-078: no regex scan
        else:
            names = defined[c.chunk_id] = defined_functions(code_body(c.text))
        for name in names:
            by_name.setdefault((c.source_id, name), []).append(c)
    if not by_name:
        return []
    by_callee: dict[str, tuple[C, str, dict[str, None]]] = {}
    for chunk in selected:
        if code_path(chunk) is None:
            continue
        own_file = code_file(chunk.locator)
        targets = (
            imported_modules(file_rows, chunk.source_id, own_file)
            if file_rows is not None and own_file is not None
            else {}
        )
        family = chunk.parent_key if file_rows is not None and _in_class(chunk) else None
        for call in called_names(chunk):
            target = targets.get(call.qualifier) if call.qualifier is not None else None
            matches = [
                c
                for c in by_name.get((chunk.source_id, call.name), ())
                if defines(c, call, defined.get(c.chunk_id), target)
            ]
            if not matches:
                continue
            # ``self.f(`` names only ``Class.f`` (a bare ``f(`` names ``f`` too).
            receiver = family is not None and call.qualifier is None and len(call.symbols) == 1
            receiver = receiver and "." in call.symbols[0]
            best = min(
                matches,
                key=lambda c: (
                    receiver and c.parent_key != family,
                    code_file(c.locator) != own_file,
                    target is not None and code_file(c.locator) != target,
                    position[c.chunk_id],
                ),
            )
            entry = by_callee.setdefault(best.chunk_id, (best, call.symbols[0], {}))
            entry[2].setdefault(chunk.chunk_id, None)
    order = sorted(by_callee.values(), key=lambda item: (-len(item[2]), position[item[0].chunk_id]))
    return [(chunk, name, tuple(ids)) for chunk, name, ids in order[:max_callees]]
