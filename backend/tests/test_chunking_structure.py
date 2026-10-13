"""D-077: the structure record codec, the parent key helpers and the chunkers' structure pass."""

from __future__ import annotations

import json

import pytest

from app.chunking.dispatch import chunk_snapshot
from app.chunking.document import ChunkDraft
from app.chunking.header import header_context, header_part
from app.chunking.imports import SourceFiles
from app.chunking.structure import (
    Import,
    Structure,
    class_key,
    key_file,
    line_section_key,
    module_key,
    offset_section_key,
    parse_structure,
    structure_json,
)
from app.chunking.tokens import CharEstimate
from app.parse.snapshot import FileEntry, Segment, Snapshot, SnapshotBuilder
from app.retrieval.definitions import code_body, defined_names
from tests.test_chunking_headings import changelog

ADAPTERS = "src/requests/adapters.py"

CLASS_HEAD = Structure(
    kind="class",
    title="HTTPAdapter",
    up=f"{ADAPTERS}::",
    head=True,
    decl="class HTTPAdapter(BaseAdapter)",
    defines=("__attrs__", "__init__"),
)

MODULE_RUN = Structure(
    kind="module",
    title=ADAPTERS,
    up=None,
    head=True,
    defines=("DEFAULT_POOLBLOCK", "DEFAULT_POOLSIZE"),
    imports=(
        Import(
            line=17,
            end_line=21,
            text="from urllib3.exceptions import (ClosedPoolError, ConnectTimeoutError)",
            names=("ClosedPoolError", "ConnectTimeoutError"),
            target=None,
        ),
        Import(
            line=39,
            end_line=39,
            text="from .auth import _basic_auth_str",
            names=("_basic_auth_str",),
            target="src/requests/auth.py",
        ),
    ),
)

SECTION = Structure(
    kind="section", title="Release History > 2.32.0 (2024-05-20)", up=None, head=False
)


@pytest.mark.parametrize("value", [CLASS_HEAD, MODULE_RUN, SECTION])
def test_structure_round_trips(value: Structure) -> None:
    assert parse_structure(structure_json(value)) == value


def test_structure_json_is_compact_and_leaves_out_empty_fields() -> None:
    raw = structure_json(SECTION)
    assert json.loads(raw) == {
        "kind": "section",
        "title": "Release History > 2.32.0 (2024-05-20)",
        "up": None,
        "head": False,
    }
    assert " " not in raw.replace("Release History > 2.32.0 (2024-05-20)", "")
    assert structure_json(Structure("section", "Å > ö", None, True)) == (
        '{"kind":"section","title":"Å > ö","up":null,"head":true}'
    )
    # Deterministic: the same record always gives the same text.
    assert structure_json(MODULE_RUN) == structure_json(MODULE_RUN)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "{",
        "null",
        "[]",
        '"section"',
        "{}",
        '{"kind":"function","title":"x","up":null,"head":true}',
        '{"kind":"section","up":null,"head":true}',
        '{"kind":"section","title":3,"up":null,"head":true}',
        '{"kind":"section","title":"x","up":1,"head":true}',
        '{"kind":"section","title":"x","up":null,"head":1}',
        '{"kind":"section","title":"x","up":null}',
        '{"kind":"class","title":"A","up":null,"head":true,"decl":5}',
        '{"kind":"class","title":"A","up":null,"head":true,"defines":"A"}',
        '{"kind":"class","title":"A","up":null,"head":true,"defines":[1]}',
        '{"kind":"module","title":"m","up":null,"head":true,"imports":{}}',
        '{"kind":"module","title":"m","up":null,"head":true,"imports":[1]}',
        '{"kind":"module","title":"m","up":null,"head":true,'
        '"imports":[{"line":"1","end_line":1,"text":"import os","names":["os"],"target":null}]}',
        '{"kind":"module","title":"m","up":null,"head":true,'
        '"imports":[{"line":true,"end_line":1,"text":"import os","names":["os"],"target":null}]}',
        "[" * 100_000,
    ],
)
def test_broken_or_misshapen_structure_is_none(raw: str | None) -> None:
    assert parse_structure(raw) is None


def test_parse_ignores_unknown_keys_and_reads_missing_optional_fields_as_empty() -> None:
    raw = '{"kind":"section","title":"A","up":null,"head":true,"later":[1,2]}'
    assert parse_structure(raw) == Structure("section", "A", None, True)
    raw = (
        '{"kind":"module","title":"m","head":true,'
        '"imports":[{"line":1,"end_line":1,"text":"import os","names":["os"]}]}'
    )
    assert parse_structure(raw) == Structure(
        "module", "m", None, True, imports=(Import(1, 1, "import os", ("os",), None),)
    )


def test_code_keys() -> None:
    assert module_key(ADAPTERS) == "src/requests/adapters.py::"
    assert class_key(ADAPTERS, "HTTPAdapter") == "src/requests/adapters.py::HTTPAdapter"
    assert class_key("a.ts", "Outer.Inner") == "a.ts::Outer.Inner"
    assert class_key("a.js", "$Store") == "a.js::$Store"
    for bad in ("", "A.", ".A", "A B", "A::B", "1A", "A#L3"):
        with pytest.raises(ValueError):
            class_key(ADAPTERS, bad)
    with pytest.raises(ValueError):
        module_key("")
    with pytest.raises(ValueError):
        class_key("", "A")


def test_section_keys() -> None:
    assert line_section_key("HISTORY.md", 159) == "HISTORY.md#L159"
    assert offset_section_key("docs/guide.pdf", 0) == "docs/guide.pdf#o0"
    # Web, paste and upload snapshots have no file part.
    assert offset_section_key("", 1234) == "#o1234"
    with pytest.raises(ValueError):
        line_section_key("HISTORY.md", 0)
    with pytest.raises(ValueError):
        line_section_key("", 3)
    with pytest.raises(ValueError):
        offset_section_key("", -1)


def test_key_file_names_the_file_of_code_keys_only() -> None:
    assert key_file(module_key(ADAPTERS)) == ADAPTERS
    assert key_file(class_key(ADAPTERS, "HTTPAdapter")) == ADAPTERS
    assert key_file(class_key("a.ts", "Outer.Inner")) == "a.ts"
    # A file name may itself contain "::"; the class path never does.
    assert key_file(module_key("odd::name.py")) == "odd::name.py"
    assert key_file(class_key("odd::name.py", "A")) == "odd::name.py"
    # Section keys and other text are not code keys.
    assert key_file(line_section_key("HISTORY.md", 159)) is None
    assert key_file(line_section_key("notes::x.md", 3)) is None
    assert key_file(offset_section_key("", 12)) is None
    assert key_file(offset_section_key("guide.pdf", 12)) is None
    assert key_file("::A") is None
    assert key_file("") is None
    assert key_file("plain") is None


# --- the structure pass of the chunkers (D-077) ------------------------------------------------

PY_FILE = "src/pkg/adapters.py"
PY_SOURCE = '''"""Adapters."""
from __future__ import annotations

import os.path
from .compat import (  # noqa: F401
    basestring,
    urlparse as parse,
)
from .auth import _basic_auth_str
import pkg.utils
from . import utils as u

try:
    import simplejson as json
    HAS_SIMPLEJSON = True
except ImportError:
    HAS_SIMPLEJSON = False

DEFAULT_POOLSIZE = 10
DEFAULT_RETRIES: int = 0


def build(x=1, *, y="v"):
    return x


class Base:
    pass


@decorated
class HTTPAdapter(Base, metaclass=Meta):
    """Doc."""

    __attrs__ = ["max_retries"]
    max_retries: int

    def __init__(self, pool=DEFAULT_POOLSIZE):
        self.pool = pool

    class Inner:
        LIMIT = 3

        def run(self):
            return self.LIMIT
'''
TS_FILE = "src/app/client.ts"
TS_SOURCE = """import D, { a, b as c } from './x';
import * as lib from './lib';
import type { T } from './missing';

export const LIMIT = 5, OTHER = 6;

export function helper(v = 1): number {
  return v;
}

export class Client<T = string> extends Base implements I {
  private size = 3;

  send(x: number = 2): void {
    helper(x);
  }
}
"""
CS_FILE = "src/Foo.cs"
CS_SOURCE = """using System.Text;
using Alias = Foo.Bar;

namespace N
{
    [Serializable]
    public sealed class Foo<T> : Bar, IBaz where T : new()
    {
        public const int MAX = 3;
        int[] P = { 80, 443 };

        public void Run(int a = 1)
        {
            System.Console.WriteLine(a);
        }
    }
}
"""
OTHER_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/compat.py": "basestring = str\n",
    "src/pkg/auth.py": "def _basic_auth_str(u, p):\n    return u\n",
    "src/pkg/utils.py": "def f():\n    return 1\n",
    "src/app/x.ts": "export default 1;\n",
    "src/app/lib/index.ts": "export const k = 1;\n",
    "src/Main.java": "public class Main {\n  static int PORT = 8080;\n  void run() {}\n}\n",
    "cmd/main.go": "package main\n\nfunc main() {\n}\n",
}


def code_snapshot(files: dict[str, str]) -> Snapshot:
    builder = SnapshotBuilder()
    entries: list[FileEntry] = []
    for path, body in files.items():
        builder.text(f"# {path}\n\n")
        start, end = builder.add(body, file=path, start_line=1, end_line=body.count("\n") + 1)
        builder.text("\n\n")
        entries.append(FileEntry(path, start, end, "code"))
    return Snapshot(builder.build(), "files", builder.segments, files=entries)


SNAPSHOT_FILES = {PY_FILE: PY_SOURCE, TS_FILE: TS_SOURCE, CS_FILE: CS_SOURCE, **OTHER_FILES}


@pytest.fixture(scope="module")
def code_drafts() -> list[ChunkDraft]:
    return chunk_snapshot(code_snapshot(SNAPSHOT_FILES), CharEstimate())


def of_file(drafts: list[ChunkDraft], path: str) -> list[ChunkDraft]:
    return [d for d in drafts if d.locator.get("file") == path]


def at_line(drafts: list[ChunkDraft], path: str, line: int) -> ChunkDraft:
    (found,) = [
        d for d in of_file(drafts, path) if d.locator["start_line"] <= line <= d.locator["end_line"]
    ]
    return found


def line_of(source: str, needle: str) -> int:
    return next(i for i, text in enumerate(source.split("\n"), 1) if needle in text)


def test_python_parent_keys_heads_and_declarations(code_drafts: list[ChunkDraft]) -> None:
    drafts = of_file(code_drafts, PY_FILE)
    module = module_key(PY_FILE)
    assert all(d.structure is not None and d.parent_key is not None for d in drafts)
    first = drafts[0]
    assert first.parent_key == module
    assert first.structure == Structure(
        "module",
        PY_FILE,
        None,
        True,
        defines=first.structure.defines if first.structure else (),
        imports=first.structure.imports if first.structure else (),
    )
    build = at_line(drafts, PY_FILE, line_of(PY_SOURCE, "def build"))
    assert build.locator["kind"] == "function"
    assert build.parent_key == module
    assert build.structure is not None
    assert (build.structure.kind, build.structure.head, build.structure.decl) == (
        "module",
        False,
        None,
    )
    assert build.structure.defines == ("build",)

    base = at_line(drafts, PY_FILE, line_of(PY_SOURCE, "class Base"))
    assert base.parent_key == class_key(PY_FILE, "Base")
    assert base.structure == Structure("class", "Base", module, True, "class Base")

    head = at_line(drafts, PY_FILE, line_of(PY_SOURCE, "class HTTPAdapter"))
    assert head.locator["kind"] == "class"
    assert head.parent_key == class_key(PY_FILE, "HTTPAdapter")
    assert head.structure == Structure(
        "class",
        "HTTPAdapter",
        module,
        True,
        "class HTTPAdapter(Base, metaclass)",  # decorator and the value ``Meta`` left out
        defines=("__attrs__", "max_retries"),
    )
    init = at_line(drafts, PY_FILE, line_of(PY_SOURCE, "def __init__"))
    assert init.locator["kind"] == "method"
    assert init.parent_key == class_key(PY_FILE, "HTTPAdapter")
    assert init.structure == Structure("class", "HTTPAdapter", module, False, defines=("__init__",))
    inner = at_line(drafts, PY_FILE, line_of(PY_SOURCE, "class Inner"))
    assert inner.parent_key == class_key(PY_FILE, "HTTPAdapter.Inner")
    assert inner.structure == Structure(
        "class",
        "HTTPAdapter.Inner",
        class_key(PY_FILE, "HTTPAdapter"),
        True,
        "class Inner",
        defines=("LIMIT",),
    )
    run = at_line(drafts, PY_FILE, line_of(PY_SOURCE, "def run"))
    assert run.locator["symbol"] == "HTTPAdapter.Inner.run"
    assert run.parent_key == class_key(PY_FILE, "HTTPAdapter.Inner")
    assert run.structure is not None and run.structure.head is False
    assert run.structure.up == class_key(PY_FILE, "HTTPAdapter")
    # Exactly one head per class family, and the module's head is the file's first chunk.
    heads = [(d.parent_key, d.structure.head) for d in drafts if d.structure is not None]
    for key in {k for k, _ in heads}:
        assert sum(head for k, head in heads if k == key) == 1


def test_python_defines_and_imports(code_drafts: list[ChunkDraft]) -> None:
    module_chunk = at_line(code_drafts, PY_FILE, line_of(PY_SOURCE, "DEFAULT_POOLSIZE = 10"))
    structure = module_chunk.structure
    assert structure is not None and structure.kind == "module"
    # The column-0 lookup's names, plus the names bound inside a module-level try block.
    assert defined_names(code_body(module_chunk.text)) == {"DEFAULT_POOLSIZE", "DEFAULT_RETRIES"}
    assert {"DEFAULT_POOLSIZE", "DEFAULT_RETRIES", "HAS_SIMPLEJSON"} <= set(structure.defines)
    imports = {i.text: i for i in structure.imports}
    compat = imports["from .compat import (basestring, urlparse as parse)"]
    first, last = line_of(PY_SOURCE, "from .compat"), line_of(PY_SOURCE, "urlparse as parse") + 1
    assert (compat.line, compat.end_line) == (first, last)  # one Import for the whole statement
    assert compat.names == ("basestring", "parse")
    assert (compat.target, compat.module) == ("src/pkg/compat.py", False)
    assert (compat.form, compat.spec, compat.member) == ("from", ".compat", None)
    auth = imports["from .auth import _basic_auth_str"]
    # The name is something module ``auth`` defines, not the module itself.
    assert (auth.target, auth.module, auth.member) == ("src/pkg/auth.py", False, "_basic_auth_str")
    absolute = imports["import pkg.utils"]
    # ``import pkg.utils`` binds ``pkg``: the target is the module ``pkg``, not ``pkg/utils.py``.
    assert (absolute.names, absolute.target, absolute.module) == (
        ("pkg",),
        "src/pkg/__init__.py",
        True,
    )
    assert (absolute.form, absolute.spec) == ("import", "pkg")
    aliased = imports["from . import utils as u"]
    assert (aliased.names, aliased.target, aliased.module) == (("u",), "src/pkg/utils.py", True)
    future = imports["from __future__ import annotations"]
    assert (future.names, future.target, future.module, future.form) == ((), None, False, "")
    assert imports["import os.path"].names == ("os",)
    assert imports["import simplejson as json"].names == ("json",)  # inside ``try:``


def test_typescript_and_csharp_structure(code_drafts: list[ChunkDraft]) -> None:
    ts = of_file(code_drafts, TS_FILE)
    top = ts[0].structure
    assert top is not None and ts[0].parent_key == module_key(TS_FILE)
    imports = {i.text: i for i in top.imports}
    x = imports["import D, { a, b as c } from './x';"]
    assert (x.names, x.target, x.module) == (("D", "a", "c"), "src/app/x.ts", False)
    assert (x.form, x.spec) == ("script", "./x")
    lib = imports["import * as lib from './lib';"]
    assert (lib.names, lib.target, lib.module) == (("lib",), "src/app/lib/index.ts", True)
    assert (lib.form, lib.spec) == ("namespace", "./lib")
    assert imports["import type { T } from './missing';"].target is None
    constants = at_line(code_drafts, TS_FILE, line_of(TS_SOURCE, "export const LIMIT"))
    assert constants.structure is not None
    assert {"LIMIT", "OTHER"} <= set(constants.structure.defines)
    client = at_line(code_drafts, TS_FILE, line_of(TS_SOURCE, "export class Client"))
    assert client.parent_key == class_key(TS_FILE, "Client")
    assert client.structure is not None and client.structure.head
    assert client.structure.decl == "class Client<T> extends Base implements I"
    assert "size" in client.structure.defines
    send = at_line(code_drafts, TS_FILE, line_of(TS_SOURCE, "send(x"))
    assert send.parent_key == class_key(TS_FILE, "Client")

    cs = of_file(code_drafts, CS_FILE)
    usings = cs[0].structure
    assert usings is not None and cs[0].parent_key == module_key(CS_FILE)
    assert [(i.text, i.names, i.target) for i in usings.imports] == [
        ("using System.Text;", (), None),
        ("using Alias = Foo.Bar;", ("Alias",), None),
    ]
    foo = at_line(code_drafts, CS_FILE, line_of(CS_SOURCE, "public sealed class Foo"))
    assert foo.parent_key == class_key(CS_FILE, "Foo")
    assert foo.structure is not None and foo.structure.head
    assert foo.structure.decl == "public sealed class Foo<T> : Bar, IBaz where T : new()"
    assert foo.structure.defines == ("MAX", "P")
    run = at_line(code_drafts, CS_FILE, line_of(CS_SOURCE, "public void Run"))
    assert run.parent_key == class_key(CS_FILE, "Foo")


def test_structure_holds_no_values(code_drafts: list[ChunkDraft]) -> None:
    """Declarations and imports never carry a default or assigned value (D-077)."""
    for draft in code_drafts:
        if draft.structure is None:
            continue
        record = json.loads(structure_json(draft.structure))
        visible = " ".join(
            [record.get("decl") or "", *(i["text"] for i in record.get("imports", []))]
        )
        for value in ("Meta", "T =", '"v"', "80", "443", "= 1", "=1", "= 2", "= 3", "= 5"):
            assert value not in visible, (draft.locator, value)


def test_fallback_chunked_code_has_no_parent(code_drafts: list[ChunkDraft]) -> None:
    for path in ("src/Main.java", "cmd/main.go"):
        drafts = of_file(code_drafts, path)
        assert drafts
        assert all(d.parent_key is None and d.structure is None for d in drafts)


def test_split_literal_parts_define_their_name_on_part_one_only() -> None:
    entries = "\n".join(
        f'    "code_{i}": "message number {i} of the error table",' for i in range(80)
    )
    source = f"ERROR_MESSAGES = {{\n{entries}\n}}\n\n_codes = {{\n{entries}\n}}\n"
    drafts = chunk_snapshot(code_snapshot({"errors.py": source}), CharEstimate())
    for name in ("ERROR_MESSAGES", "_codes"):
        parts = [d for d in drafts if d.locator.get("symbol") == name]
        assert len(parts) > 2
        for draft in parts:
            assert draft.parent_key == module_key("errors.py")
            assert draft.structure is not None and draft.structure.kind == "module"
            first = header_part(draft.text) == 1
            assert draft.structure.defines == ((name,) if first else ())
            upper = {n for n in draft.structure.defines if n.isupper()}
            assert upper == defined_names(code_body(draft.text))


def docs_snapshot(path: str, body: str, **where: object) -> Snapshot:
    builder = SnapshotBuilder()
    builder.text(f"# {path}\n\n")
    start, end = builder.add(body, file=path, **where)
    return Snapshot(
        builder.build(), "files", builder.segments, files=[FileEntry(path, start, end, "document")]
    )


def test_a_document_chunk_is_parented_by_the_section_of_its_first_line() -> None:
    body = changelog()
    lines = body.split("\n")
    snap = docs_snapshot("HISTORY.md", body, start_line=1, end_line=len(lines))
    drafts = chunk_snapshot(snap, CharEstimate())
    entry = next(i for i, text in enumerate(lines, 1) if "CPython 3.12" in text)
    release = lines.index("2.32.0 (2024-05-20)") + 1
    holding = [d for d in drafts if d.locator["start_line"] <= entry <= d.locator["end_line"]]
    assert holding
    assert holding[0].parent_key == line_section_key("HISTORY.md", release)
    assert holding[0].structure == Structure(
        "section", "Release History › 2.32.0 (2024-05-20)", None, True
    )
    with_heading = 0
    for draft in drafts:
        context = header_context(draft.text, "HISTORY.md")
        if draft.structure is None:
            assert context == "" and draft.parent_key is None
            continue
        with_heading += 1
        assert draft.structure.title == context  # the parent title IS the header path
        assert draft.parent_key is not None and draft.parent_key.startswith("HISTORY.md#L")
    assert with_heading == len(drafts)


def test_continuing_and_heading_less_document_chunks() -> None:
    body = "Guide\n=====\n\n" + "\n".join(
        f"    line {i} of a long literal block." for i in range(300)
    )
    snap = docs_snapshot("docs/guide.rst", body, start_line=1, end_line=302)
    drafts = chunk_snapshot(snap, CharEstimate())
    assert len(drafts) > 2
    assert drafts[0].structure == Structure("section", "Guide", None, True)
    for later in drafts[1:]:
        assert later.parent_key == line_section_key("docs/guide.rst", 1)
        assert later.structure == Structure("section", "Guide", None, False)
    plain = docs_snapshot("notes.txt", "Just a note.\nAnother line.", start_line=1, end_line=2)
    (note,) = chunk_snapshot(plain, CharEstimate())
    assert (note.parent_key, note.structure) == (None, None)
    preamble = "Intro text before any heading.\n\n# First\n\nBody of the first section.\n"
    drafts = chunk_snapshot(
        docs_snapshot("README.md", preamble, start_line=1, end_line=6), CharEstimate()
    )
    assert (drafts[0].parent_key, drafts[0].structure) == (None, None)


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("a.md", "# Usage\n"),
        ("b.md", "Usage\n=====\n"),
        ("c.rst", "Usage\n-----\n"),
        ("d.rst", "=====\nUsage\n=====\n"),
    ],
)
def test_heading_forms_give_the_same_parent(path: str, heading: str) -> None:
    body = f"Intro line.\n\n{heading}\nThe usage text.\n"
    lines = body.split("\n")
    snap = docs_snapshot(path, body, start_line=1, end_line=len(lines))
    drafts = chunk_snapshot(snap, CharEstimate())
    # Every heading starts a chunk (D-104): the preamble has no parent, the section has its own.
    preamble, section = drafts
    assert (preamble.parent_key, preamble.structure) == (None, None)
    assert section.parent_key == line_section_key(path, 3)
    assert section.structure == Structure("section", "Usage", None, True)
    later = chunk_snapshot(
        docs_snapshot(path, f"{heading}\nThe usage text.\n", start_line=1, end_line=4),
        CharEstimate(),
    )
    assert later[0].parent_key == line_section_key(path, 1)
    assert later[0].structure == Structure("section", "Usage", None, True)


def test_web_pdf_paged_and_transcript_parents() -> None:
    builder = SnapshotBuilder()
    builder.add("Opening words without a heading. " * 5)
    builder.separate()
    first = builder.add("# Install\n\n" + "Install it with pip. " * 300, heading="Install")
    builder.separate()
    builder.add("# Usage\n\n" + "Use it like this. " * 20, heading="Usage")
    web = Snapshot(builder.build(), "sections", builder.segments)
    drafts = chunk_snapshot(web, CharEstimate())
    keys = [(d.parent_key, d.structure.head if d.structure else None) for d in drafts]
    install = offset_section_key("", first[0])
    assert (install, True) in keys and (install, False) in keys
    for draft in drafts:
        if draft.structure is not None:
            assert draft.structure.title == draft.locator["heading"]
            assert draft.text.startswith(f"Section: {draft.locator['heading']}\n")  # D-105
        else:
            assert not draft.locator["heading"]

    text = "Preface.\n\n# Results\n\nThe results are good.\n"
    pdf = Snapshot(text, "pages", [Segment(0, len(text), page=1)])
    preface, results = chunk_snapshot(pdf, CharEstimate())  # every heading starts a chunk
    assert (preface.parent_key, preface.structure) == (None, None)
    assert preface.locator == {"pages": [1]}
    assert results.parent_key == offset_section_key("", 10)
    assert results.locator == {"pages": [1], "heading": "Results"}
    assert results.text == "Section: Results\n# Results\n\nThe results are good."
    pdf = Snapshot(text[10:], "pages", [Segment(0, len(text) - 10, page=1)])
    (page,) = chunk_snapshot(pdf, CharEstimate())
    assert page.parent_key == offset_section_key("", 0)
    assert page.structure == Structure("section", "Results", None, True)

    paged = docs_snapshot("docs/manual.pdf", "# Manual\n\nRead this first.\n", page=1)
    (draft,) = chunk_snapshot(paged, CharEstimate())
    assert draft.parent_key == offset_section_key("docs/manual.pdf", 0)  # file-relative
    assert draft.structure == Structure("section", "Manual", None, True)

    builder = SnapshotBuilder()
    for i in range(10):
        builder.separate()
        builder.add(
            f"Turn {i} says a few words.", start_s=float(i), end_s=float(i + 1), speaker="A"
        )
    transcript = Snapshot(builder.build(), "timestamps", builder.segments)
    assert all(
        d.parent_key is None and d.structure is None
        for d in chunk_snapshot(transcript, CharEstimate())
    )


def test_import_resolution_within_the_snapshot() -> None:
    files = SourceFiles(
        [
            "src/requests/auth.py",
            "src/requests/utils.py",
            "src/requests/__init__.py",
            "src/requests/packages/__init__.py",
            "tests/utils.py",
            "web/src/x.ts",
            "web/src/lib/index.tsx",
            "web/src/y.js",
        ]
    )
    importer = "src/requests/adapters.py"
    assert files.python(importer, ".auth") == "src/requests/auth.py"
    assert files.python(importer, "requests.utils") == "src/requests/utils.py"
    assert files.python(importer, "utils") is None  # two files are module ``utils``
    assert files.python(importer, ".packages") == "src/requests/packages/__init__.py"
    assert files.python(importer, ".", "utils") == "src/requests/utils.py"
    assert files.python(importer, ".", "nothing") == "src/requests/__init__.py"
    assert files.python(importer, "...x") is None
    assert files.python(importer, "json") is None
    # ``from requests import utils``: the submodule when it is a file, else the package.
    assert files.python(importer, "requests", "utils") == "src/requests/utils.py"
    assert files.python(importer, "requests", "Session") == "src/requests/__init__.py"
    assert files.resolve(importer, "from", "requests", "utils") == ("src/requests/utils.py", True)
    assert files.resolve(importer, "from", "requests", "Session") == (
        "src/requests/__init__.py",
        False,
    )
    assert files.resolve(importer, "from", ".", "auth") == ("src/requests/auth.py", True)
    assert files.resolve(importer, "from", ".auth", None) == ("src/requests/auth.py", False)
    assert files.resolve(importer, "import", "requests", None) == ("src/requests/__init__.py", True)
    assert files.resolve(importer, "import", "json", None) == (None, False)
    assert files.resolve(importer, "", "", None) == (None, False)
    assert files.resolve("web/src/a.ts", "script", "./x", None) == ("web/src/x.ts", False)
    assert files.resolve("web/src/a.ts", "namespace", "./x", None) == ("web/src/x.ts", True)
    assert files.resolve("web/src/a.ts", "namespace", "react", None) == (None, False)
    assert files.script("web/src/a.ts", "./x") == "web/src/x.ts"
    assert files.script("web/src/a.ts", "./x.js") == "web/src/x.ts"  # ESM names the output
    assert files.script("web/src/a.ts", "./lib") == "web/src/lib/index.tsx"
    assert files.script("web/src/a.ts", "./y") == "web/src/y.js"
    assert files.script("web/src/a.ts", "react") is None
    assert files.script("web/src/a.ts", "../../../x") is None


def test_python_import_forms_bind_modules_or_their_members() -> None:
    files = {
        "pkg/__init__.py": "",
        "pkg/b.py": "class Thing:\n    pass\n",
        "pkg/sub.py": "X = 1\n",
        "pkg/a.py": (
            "from .b import Thing\nimport pkg.sub\nimport pkg.sub as s\nfrom pkg import sub\n"
            "from pkg import b, sub as t\nimport os, pkg\n"
        ),
    }
    drafts = chunk_snapshot(code_snapshot(files), CharEstimate())
    (first,) = [d for d in of_file(drafts, "pkg/a.py") if d.structure and d.structure.imports]
    assert first.structure is not None
    found = [(i.text, i.names, i.target, i.module) for i in first.structure.imports]
    assert found == [
        ("from .b import Thing", ("Thing",), "pkg/b.py", False),  # a class of module b
        ("import pkg.sub", ("pkg",), "pkg/__init__.py", True),  # binds ``pkg``
        ("import pkg.sub as s", ("s",), "pkg/sub.py", True),
        ("from pkg import sub", ("sub",), "pkg/sub.py", True),  # the submodule
        ("from pkg import b, sub as t", ("b", "t"), "pkg/__init__.py", False),
        ("import os, pkg", ("os", "pkg"), None, False),  # several modules: no one target
    ]


JS_BLOCKS = """import { TIMEOUT } from './config';
if (process.env.FAST) {
  const TIMEOUT = 1;
  let RETRIES = 2;
  var HOISTED = 3;
  function local() { return 4; }
  class Local {}
  warm(TIMEOUT);
} else {
  const OTHER = 5;
}
try {
  const TRIED = 6;
} catch (e) {
  let CAUGHT = 7;
} finally {
  var LAST = 8;
}
{
  const BARE = 9;
}
export const TOP = 10;
export function fetchIt(url) { return fetch(url, { timeout: TIMEOUT }); }
"""
TS_NAMESPACE = """namespace Inner {
  export const HIDDEN = 1;
}
export const SHOWN = 2;
"""
PY_BLOCKS = """if FAST:
    TIMEOUT = 1
else:
    TIMEOUT = 2
try:
    LIMIT = 3
except ImportError:
    LIMIT = 4
"""


def test_only_names_of_the_module_scope_are_module_defines() -> None:
    """JS/TS blocks have their own scope: a ``const`` / ``let`` / ``function`` / ``class`` in a
    top-level ``if``, ``try`` or bare block is not the module's, a ``var`` is; a TS namespace's
    members are ``Inner.x``. Python's ``if`` / ``try`` blocks share the module's scope."""
    files = {"lib/client.js": JS_BLOCKS, "lib/ns.ts": TS_NAMESPACE, "lib/flags.py": PY_BLOCKS}
    drafts = chunk_snapshot(code_snapshot(files), CharEstimate())

    def module_defines(path: str) -> set[str]:
        return {
            name
            for d in of_file(drafts, path)
            if d.structure is not None and d.parent_key == module_key(path)
            for name in d.structure.defines
        }

    assert module_defines("lib/client.js") == {"HOISTED", "LAST", "TOP", "fetchIt"}
    assert module_defines("lib/ns.ts") == {"SHOWN"}
    assert module_defines("lib/flags.py") == {"TIMEOUT", "LIMIT"}
