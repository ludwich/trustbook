"""Pull in the bodies of functions that the context's code calls (D-057; D-032 for functions).

Code is layered: the chunks that match a question's wording are often thin call sites
(``headers["Authorization"] = _basic_auth_str(user, password)``, a ``__call__``, an adapter, a
test), while the logic that answers it sits in the helper they call. Without the helper's body the
model can only answer with its name, cite a call site, or find nothing to say.

After the context is chosen (and the D-032 constants are added), the calls in each code chunk are
collected: bare calls ``name(`` and ``self.name(`` / ``this.name(`` / ``cls.name(``. Only chunks
made by a code chunker count (their first line is ``File: … (lines a-b)``); examples in a
document file are not the source's code. A callee is added when a code chunk of the **same
source**

- has the called name as its ``locator.symbol`` (``name``, or ``Class.name`` for the caller's
  class) and is a function or method: ``locator.kind`` when the chunker records it, otherwise a
  ``def`` / ``function`` / method-signature / ``procedure`` line for that name in its text. A
  chunk without a symbol counts only if the fallback chunker made it (no grammar for the file);
  the syntax chunker's symbol-less chunks are module-level code;
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
"""

from __future__ import annotations

import builtins
import keyword
import re
from collections.abc import Sequence

from app.chunking.syntax import language_for_path
from app.retrieval.definitions import ContextChunk, code_body, code_file

MAX_CALLEES = 2
FUNCTION_KINDS = frozenset({"function", "method"})
CONTAINER_KINDS = frozenset({"class"})

# ``name(`` not preceded by a word character or a dot (``obj.name(`` is someone else's method),
# optionally qualified by the receiver of the enclosing class.
_CALL = re.compile(r"(?<![\w.$@])(?:(self|this|cls)\.)?([A-Za-z_]\w*)\s*\(")
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
    if file is None or not _CODE_HEADER.match(chunk.text):
        return None
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


def called_names(chunk: ContextChunk) -> list[tuple[str, ...]]:
    """The calls in a code chunk, each as the symbols it may refer to, in order, unique.

    ``self.f(`` in a method of ``A`` may only be ``A.f``; a bare ``f(`` may be ``f`` or, in
    languages with an implicit receiver (C#, Java), ``A.f``. Calls to names the chunk defines
    itself, constructors (``new X(``) and language keywords are left out.
    """
    body = code_body(chunk.text)
    own = defined_functions(body) | {_symbol(chunk.locator).rpartition(".")[2]}
    owner = enclosing_class(chunk.locator)
    found: dict[tuple[str, ...], None] = {}
    for match in _CALL.finditer(body):
        receiver, name = match.group(1), match.group(2)
        if name in _NOT_CALLS or name in own:
            continue
        line_start = body.rfind("\n", 0, match.start()) + 1
        if _NEW.search(body, line_start, match.start()):
            continue
        if receiver:
            if owner:
                found.setdefault((f"{owner}.{name}",), None)
        else:
            found.setdefault((name, f"{owner}.{name}") if owner else (name,), None)
    return list(found)


def defines(chunk: ContextChunk, symbols: tuple[str, ...]) -> bool:
    """Whether code chunk ``chunk`` is the definition of a function or method named by ``symbols``.

    With a ``locator.kind`` it must be a function or method; with a ``locator.symbol`` that must be
    one of ``symbols``. A chunk without a symbol counts only when the fallback chunker made it (no
    grammar for the file): the syntax chunker gives every function its own symbol, so its
    symbol-less chunks are module-level code (imports, conditional stubs). The text must define
    the name unless the chunker recorded the kind.
    """
    symbol = _symbol(chunk.locator)
    kind = _kind(chunk.locator)
    if kind is not None and kind not in FUNCTION_KINDS:
        return False
    if symbol:
        if symbol not in symbols:
            return False
        if kind is not None:
            return True
    elif kind is None and language_for_path(code_file(chunk.locator) or "") is not None:
        return False
    return symbols[0].rpartition(".")[2] in defined_functions(code_body(chunk.text))


def callee_extras[C: ContextChunk](
    selected: Sequence[C], ranked: Sequence[C], *, max_callees: int = MAX_CALLEES
) -> list[tuple[C, str, tuple[str, ...]]]:
    """Up to ``max_callees`` ``(chunk, name, caller ids)`` of functions the context's code calls.

    Callees come from ``ranked`` only (the relevance gate), from the caller's source, outside test
    files and the context. Most context callers first, then the best rerank position; a name
    defined in several files prefers the caller's file.
    """
    taken = {c.chunk_id for c in selected}
    position = {c.chunk_id: i for i, c in enumerate(ranked)}
    pool = [
        c
        for c in ranked
        if c.chunk_id not in taken and (file := code_path(c)) is not None and not is_test_file(file)
    ]
    if not pool:
        return []
    by_callee: dict[str, tuple[C, str, dict[str, None]]] = {}
    for chunk in selected:
        if code_path(chunk) is None:
            continue
        own_file = code_file(chunk.locator)
        for symbols in called_names(chunk):
            matches = [c for c in pool if c.source_id == chunk.source_id and defines(c, symbols)]
            if not matches:
                continue
            best = min(
                matches,
                key=lambda c: (code_file(c.locator) != own_file, position[c.chunk_id]),
            )
            entry = by_callee.setdefault(best.chunk_id, (best, symbols[0], {}))
            entry[2].setdefault(chunk.chunk_id, None)
    order = sorted(by_callee.values(), key=lambda item: (-len(item[2]), position[item[0].chunk_id]))
    return [(chunk, name, tuple(ids)) for chunk, name, ids in order[:max_callees]]
