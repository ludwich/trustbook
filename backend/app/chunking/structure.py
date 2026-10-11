"""The parent of a chunk: deterministic parent keys and the ``structure`` record (D-077).

Retrieval ranks and cites small child chunks. A child's parent (its document section, its class
or its module) is named by a **parent key** stored next to the chunk, and described by a small
:class:`Structure` record stored as JSON. Both are file-relative, so a file reused unchanged by an
incremental refresh keeps them as they are.

Parent keys never contain chunk ids or snapshot offsets:

- code class: ``{file}::{Outer.Inner}`` (:func:`class_key`)
- code module: ``{file}::`` (:func:`module_key`)
- line-located document file: ``{file}#L{first line of its heading block}``
  (:func:`line_section_key`)
- paged document file in a folder: ``{file}#o{heading offset - file start}``, and a web, paste or
  upload snapshot: ``#o{segment start}`` (:func:`offset_section_key` with ``file=""``)

Structure is navigation data for retrieval only. Nothing in it is citable and no grounding layer
reads it: text that exists only in a parent or an outline can never verify a claim.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, cast, get_args

ParentKind = Literal["section", "class", "module"]
PARENT_KINDS: frozenset[str] = frozenset(get_args(ParentKind))
# How an import's target is resolved (:meth:`app.chunking.imports.SourceFiles.resolve`):
# ``import``: Python ``import a`` / ``import a.b as c`` (the name is the module ``spec``);
# ``from``: Python ``from spec import …``; ``script``: a JS/TS import of exports of ``spec``;
# ``namespace``: a JS/TS import of the module ``spec`` itself (``* as ns``, ``x = require()``);
# ``""``: nothing to resolve (C# ``using``, ``__future__``, several modules in one statement).
ImportForm = Literal["", "import", "from", "script", "namespace"]
IMPORT_FORMS: frozenset[str] = frozenset(get_args(ImportForm))

# One identifier (Python, JS/TS incl. ``$``, C#), and a dotted class path of them.
_QUALNAME = re.compile(r"(?:[^\W\d]|\$)[\w$]*(?:\.(?:[^\W\d]|\$)[\w$]*)*")


@dataclass(frozen=True)
class Import:
    """A module-level import statement of a code file.

    ``line`` / ``end_line``: file-relative, 1-based. ``text``: the statement on one line,
    whitespace collapsed. ``names``: the names it binds in the file (the alias when aliased).
    ``target``: the file of the same source it resolves to, else ``None``. ``module``: the names
    bind that file's module itself (``import a``, ``from . import utils as u``,
    ``import * as u from './u'``); otherwise they bind things the module defines
    (``from .b import Thing``), or ``target`` is ``None``.

    ``form``, ``spec``, ``member``: what the target is resolved from, so that a refresh can
    resolve it again against the files of the new snapshot. ``spec``: the module as written
    (``.b``, ``requests.utils``, ``./x``; for ``import a.b`` the bound ``a``). ``member``: the one
    name a Python ``from spec import name`` imports (it may be a submodule), else ``None``.
    """

    line: int
    end_line: int
    text: str
    names: tuple[str, ...]
    target: str | None
    module: bool = False
    form: ImportForm = ""
    spec: str = ""
    member: str | None = None


@dataclass(frozen=True)
class Structure:
    """Where a chunk sits in its parent.

    ``kind``: the kind of the chunk's PARENT. ``title``: section heading path (equal to the
    chunk's header context), class path ``Outer.Inner``, or the module's file path. ``up``: the
    parent's own parent key (class: outer class or module), else ``None``. ``head``: the chunk
    starts its parent. ``decl``: class head chunks only, the value-free declaration line.
    ``defines``: names the chunk binds at its parent's level. ``imports``: module-level imports
    inside the chunk (module chunks only).
    """

    kind: ParentKind
    title: str
    up: str | None
    head: bool
    decl: str | None = None
    defines: tuple[str, ...] = ()
    imports: tuple[Import, ...] = ()


def structure_json(s: Structure) -> str:
    """Compact JSON for the ``chunks.structure`` column; empty optional fields are left out."""
    data: dict[str, object] = {"kind": s.kind, "title": s.title, "up": s.up, "head": s.head}
    if s.decl is not None:
        data["decl"] = s.decl
    if s.defines:
        data["defines"] = list(s.defines)
    if s.imports:
        data["imports"] = [_import_json(i) for i in s.imports]
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _import_json(i: Import) -> dict[str, object]:
    data: dict[str, object] = {
        "line": i.line,
        "end_line": i.end_line,
        "text": i.text,
        "names": list(i.names),
        "target": i.target,
    }
    if i.module:
        data["module"] = True
    if i.form:
        data["form"] = i.form
        data["spec"] = i.spec
    if i.member is not None:
        data["member"] = i.member
    return data


class _InvalidError(ValueError):
    """A structure record that does not have the expected shape."""


def parse_structure(raw: str | None) -> Structure | None:
    """The :class:`Structure` stored as ``raw``, or ``None``.

    Tolerant: a missing, empty or broken value, or one of the wrong shape, is ``None``, which every
    caller treats as "no structure" (the lookups then take their path without parent keys).
    Unknown keys are ignored, so newer records still read.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise _InvalidError
        kind = data.get("kind")
        if kind not in PARENT_KINDS:
            raise _InvalidError
        return Structure(
            kind=cast(ParentKind, kind),
            title=_str(data.get("title")),
            up=_optional_str(data.get("up")),
            head=_bool(data.get("head")),
            decl=_optional_str(data.get("decl")),
            defines=_strings(data.get("defines", [])),
            imports=tuple(_import(item) for item in _list(data.get("imports", []))),
        )
    except (ValueError, RecursionError):  # json.JSONDecodeError is a ValueError
        return None


def _import(value: object) -> Import:
    if not isinstance(value, dict):
        raise _InvalidError
    form = value.get("form", "")
    if form not in IMPORT_FORMS:
        raise _InvalidError
    return Import(
        line=_int(value.get("line")),
        end_line=_int(value.get("end_line")),
        text=_str(value.get("text")),
        names=_strings(value.get("names", [])),
        target=_optional_str(value.get("target")),
        module=_bool(value.get("module", False)),
        form=cast(ImportForm, form),
        spec=_str(value.get("spec", "")),
        member=_optional_str(value.get("member")),
    )


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise _InvalidError
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise _InvalidError
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _InvalidError
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _InvalidError
    return cast(list[object], value)


def _strings(value: object) -> tuple[str, ...]:
    return tuple(_str(item) for item in _list(value))


def module_key(file: str) -> str:
    """The parent key of a code file's module level: ``{file}::``."""
    if not file:
        raise ValueError("a module key needs a file")
    return f"{file}::"


def class_key(file: str, qualname: str) -> str:
    """The parent key of a class: ``{file}::{Outer.Inner}``."""
    if not file:
        raise ValueError("a class key needs a file")
    if not _QUALNAME.fullmatch(qualname):
        raise ValueError(f"not a class path: {qualname!r}")
    return f"{file}::{qualname}"


def line_section_key(file: str, line: int) -> str:
    """The parent key of a section of a line-located document file: ``{file}#L{line}``.

    ``line``: the first line (1-based) of the section's heading block.
    """
    if not file:
        raise ValueError("a line section key needs a file")
    if line < 1:
        raise ValueError(f"line numbers start at 1, got {line}")
    return f"{file}#L{line}"


def offset_section_key(file: str, offset: int) -> str:
    """The parent key of a section located by a character offset: ``{file}#o{offset}``.

    A paged document file in a folder passes its path and the heading offset relative to the
    file's start; a web, paste or upload snapshot passes ``file=""`` and the segment start.
    """
    if offset < 0:
        raise ValueError(f"offsets are not negative, got {offset}")
    return f"{file}#o{offset}"


def key_file(key: str) -> str | None:
    """The file of a code parent key (class or module), else ``None`` (sections, other text)."""
    file, sep, qualname = key.rpartition("::")
    if not sep or not file:
        return None
    if qualname and not _QUALNAME.fullmatch(qualname):
        return None
    return file
