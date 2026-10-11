"""tree-sitter code chunking (SPEC §7.2, D-047): one chunk per function, method or class."""

from __future__ import annotations

import itertools
import re
import textwrap

import pytest

from app.chunking import syntax
from app.chunking.document import ChunkDraft
from app.chunking.syntax import (
    chunk_source_code,
    chunk_syntax,
    header_line,
    language_for_path,
)
from app.chunking.tokens import CharEstimate


class WordCounter:
    def count(self, text: str) -> int:
        return len(text.split())


def _chunks(path: str, source: str, counter: object | None = None) -> list[ChunkDraft]:
    text = textwrap.dedent(source).strip("\n")
    prefix = "# some header\n\n"  # the file sits inside a bigger snapshot
    full = prefix + text
    drafts = chunk_source_code(full, len(prefix), len(full), path, counter or CharEstimate())  # type: ignore[arg-type]
    for d in drafts:
        # body = the snapshot slice, whole lines, and the header names the same lines
        assert d.text.split("\n", 1)[1] == full[d.start : d.end]
        lines = text.split("\n")[d.locator["start_line"] - 1 : d.locator["end_line"]]
        assert full[d.start : d.end] == "\n".join(lines)
        first_line = d.text.split("\n", 1)[0]
        part = re.search(r" \(part (\d+)\) \(lines", first_line)
        expected = header_line(
            path,
            d.locator["symbol"],
            d.locator["start_line"],
            d.locator["end_line"],
            int(part.group(1)) if part else None,
        )
        assert first_line == expected
        assert d.locator["kind"] in ("function", "method", "class", "module", "data", None)
    return drafts


def _summary(drafts: list[ChunkDraft]) -> list[tuple[str | None, int, int]]:
    return [(d.locator["symbol"], d.locator["start_line"], d.locator["end_line"]) for d in drafts]


def _kinds(drafts: list[ChunkDraft]) -> list[str | None]:
    return [d.locator["kind"] for d in drafts]


def test_language_by_extension() -> None:
    assert language_for_path("src/a.py") == "python"
    assert language_for_path("a.PYI") == "python"
    assert language_for_path("web/app.jsx") == "javascript"
    assert language_for_path("web/app.ts") == "typescript"
    assert language_for_path("web/App.tsx") == "tsx"
    assert language_for_path("Billing/Invoice.cs") == "c_sharp"
    assert language_for_path("x.al") is None
    assert language_for_path("Makefile") is None


def test_python_functions_classes_methods_nested_and_decorators() -> None:
    drafts = _chunks(
        "pkg/mod.py",
        '''
        """Module doc."""
        import os

        TIMEOUT = 10

        # Returns the value unchanged.
        @cached
        def ident(a):
            return a

        class Session(Base):
            """A session."""
            retries: int = 3

            @property
            def ok(self):
                return True

            class Adapter:
                """Inner."""
                def send(self):
                    pass

        if __name__ == "__main__":
            ident(1)
        ''',
    )
    assert _summary(drafts) == [
        (None, 1, 4),
        ("ident", 6, 9),
        ("Session", 11, 13),
        ("Session.ok", 15, 17),
        ("Session.Adapter", 19, 20),
        ("Session.Adapter.send", 21, 22),
        (None, 24, 25),
    ]
    assert _kinds(drafts) == ["module", "function", "class", "method", "class", "method", "module"]
    assert drafts[1].text.startswith("File: pkg/mod.py · ident (lines 6-9)\n# Returns")
    assert "@cached" in drafts[1].text  # the decorator belongs to its function
    assert "@property" in drafts[3].text
    assert drafts[0].text.startswith("File: pkg/mod.py (lines 1-4)\n")  # module code: no symbol


def test_javascript_arrow_functions_classes_and_exports() -> None:
    drafts = _chunks(
        "web/util.js",
        """
        const http = require('http');

        export const fetchJson = async (url) => {
          return http.get(url);
        };

        function* ids() {
          yield 1;
        }

        class Client {
          #retries = 2;

          send(req) {
            return req;
          }

          onError = (err) => {
            console.log(err);
          };
        }

        module.exports = { ids };
        """,
    )
    assert _summary(drafts) == [
        (None, 1, 1),
        ("fetchJson", 3, 5),
        ("ids", 7, 9),
        ("Client", 11, 12),
        ("Client.send", 14, 16),
        ("Client.onError", 18, 20),
        (None, 23, 23),
    ]


def test_typescript_interfaces_enums_namespaces_and_decorators() -> None:
    drafts = _chunks(
        "src/app.ts",
        """
        import { Base } from "./base";

        /** A shape. */
        export interface Shape {
          area(): number;
          name: string;
        }

        export enum Kind {
          A,
          B,
        }

        export namespace Geometry {
          export function scale(s: Shape, k: number): number {
            return s.area() * k;
          }
        }

        @Component({ selector: "x" })
        export class Circle extends Base implements Shape {
          name = "circle";

          @Input()
          area(): number {
            return 3.14;
          }
        }
        """,
    )
    assert _summary(drafts) == [
        (None, 1, 1),
        ("Shape", 3, 7),
        ("Kind", 9, 12),
        (None, 14, 14),
        ("scale", 15, 17),
        ("Circle", 20, 22),
        ("Circle.area", 24, 27),
    ]
    assert "@Input()" in drafts[-1].text


def test_tsx_component() -> None:
    drafts = _chunks(
        "ui/Badge.tsx",
        """
        import React from "react";

        export function Badge({ label }: { label: string }) {
          return <span className="badge">{label}</span>;
        }
        """,
    )
    assert _summary(drafts) == [(None, 1, 1), ("Badge", 3, 5)]


def test_csharp_namespaces_properties_and_members() -> None:
    drafts = _chunks(
        "Billing/Invoice.cs",
        """
        using System;

        namespace Acme.Billing
        {
            /// <summary>An invoice.</summary>
            [Serializable]
            public class Invoice
            {
                private readonly int _rate = 25;
                public string Name { get; set; }

                public int Total
                {
                    get { return _rate * 2; }
                }

                public Invoice() { }

                public int Add(int a) => a + _rate;

                public class Line
                {
                    public int Qty;
                    public void Bump() { Qty++; }
                }
            }

            public interface IPayable
            {
                void Pay();
            }
        }
        """,
    )
    assert _summary(drafts) == [
        (None, 1, 4),  # usings + the namespace line: no symbol of its own
        ("Invoice", 5, 10),  # attributes, doc comment, header, fields, auto-property
        ("Invoice.Total", 12, 15),  # a property with code is a member of its own
        ("Invoice.Invoice", 17, 17),
        ("Invoice.Add", 19, 19),
        ("Invoice.Line", 21, 23),
        ("Invoice.Line.Bump", 24, 24),
        ("IPayable", 28, 31),  # bodiless interface members stay in the interface
    ]
    assert _kinds(drafts) == [
        "module",
        "class",
        "method",
        "method",
        "method",
        "class",
        "method",
        "class",
    ]
    assert "[Serializable]" in drafts[1].text and "/// <summary>" in drafts[1].text


def test_file_scoped_namespace_and_one_line_members_stay_whole() -> None:
    drafts = _chunks(
        "A.cs",
        """
        namespace Acme;

        public class Tiny { public int X; void M() { } }
        """,
    )
    assert _summary(drafts) == [(None, 1, 1), ("Tiny", 3, 3)]


def test_long_symbol_is_split_at_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(syntax, "SYMBOL_MAX_TOKENS", 40)
    body = "\n".join(
        f"    if x == {i}:\n        total = total + {i} * 2 + offset - {i}" for i in range(12)
    )
    drafts = _chunks("big.py", f"def big(x):\n    total = 0\n{body}\n    return total\n")
    assert len(drafts) > 1
    assert {d.locator["symbol"] for d in drafts} == {"big"}
    assert drafts[0].locator["start_line"] == 1
    assert drafts[-1].text.rstrip().endswith("return total")
    for d in drafts:
        assert d.token_count <= 40 + 12  # body within the maximum, plus the header line
        body_first = d.text.split("\n")[1]
        assert body_first.lstrip().startswith(("def ", "if ", "total", "return"))
    # consecutive and complete
    for a, b in itertools.pairwise(drafts):
        assert b.locator["start_line"] == a.locator["end_line"] + 1


def test_module_code_grouped_up_to_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(syntax, "MODULE_TARGET_TOKENS", 20)
    monkeypatch.setattr(syntax, "MODULE_MAX_TOKENS", 30)
    constants = "\n".join(f"NAME_{i} = 'value number {i}'" for i in range(12))
    drafts = _chunks("consts.py", constants, WordCounter())
    assert len(drafts) > 1
    assert all(d.locator["symbol"] is None for d in drafts)
    for d in drafts:
        assert d.token_count - len(d.text.split("\n", 1)[0].split()) <= 30


def test_huge_module_statement_is_split_between_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(syntax, "MODULE_TARGET_TOKENS", 20)
    monkeypatch.setattr(syntax, "MODULE_MAX_TOKENS", 30)
    entries = "\n".join(f"    {i}: ('code_{i}', 'alias_{i}')," for i in range(30))
    drafts = _chunks("codes.py", "_codes = {\n" + entries + "\n}", WordCounter())
    assert len(drafts) > 2
    assert drafts[0].locator["start_line"] == 1 and drafts[-1].text.endswith("}")


def test_syntax_errors_and_other_languages_use_the_fallback() -> None:
    broken = _chunks("bad.py", "def broken(:\n    return 1\n")
    assert _summary(broken) == [(None, 1, 2)]
    assert broken[0].text.startswith("File: bad.py (lines 1-2)\n")
    text = "procedure A;\nbegin\nend;"
    assert chunk_syntax(text, 0, len(text), "x.py", CharEstimate(), "python") is None
    al = _chunks("Codeunit.al", "procedure Post()\nbegin\nend;\n\ntrigger OnRun()\nbegin\nend;")
    assert [d.locator["symbol"] for d in al] == [None]


def test_chunker_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("grammar crashed")

    monkeypatch.setattr(syntax, "chunk_syntax", boom)
    drafts = _chunks("a.py", "def f():\n    return 1\n")
    assert _summary(drafts) == [(None, 1, 2)]


def test_closing_braces_alone_are_not_chunks() -> None:
    drafts = _chunks("a.ts", "class A {\n  m() {\n    return 1;\n  }\n}\n")
    assert _summary(drafts) == [("A", 1, 1), ("A.m", 2, 4)]
    assert all(d.text.split("\n", 1)[1].strip() not in ("}", "") for d in drafts)


# --- data literals (ref-near-10, D-056) --------------------------------------------------------


def _assert_data_parts(drafts: list[ChunkDraft], symbol: str) -> list[ChunkDraft]:
    """The literal's chunks: consecutive, numbered parts, all named ``symbol``, kind ``data``."""
    parts = [d for d in drafts if d.locator["symbol"] == symbol]
    assert len(parts) > 2
    assert all(d.locator["kind"] == "data" for d in parts)
    for n, d in enumerate(parts, start=1):
        assert f" · {symbol} (part {n}) (lines " in d.text.split("\n", 1)[0]
    for a, b in itertools.pairwise(parts):
        assert b.locator["start_line"] == a.locator["end_line"] + 1
    return parts


def test_python_data_literal_is_split_at_elements_with_its_name() -> None:
    entries = []
    for i in range(60):
        if i % 20 == 0:
            entries.append(f"    # Section {i // 20}.")
        if i == 31:  # a multi-line element stays whole
            entries.append(f"    {i}: (\n        'code_{i}',\n        'alias_{i}',\n    ),")
        else:
            entries.append(f"    {i}: ('code_number_{i}', 'alias_number_{i}'),")
    source = (
        '"""Status codes."""\n\nfrom .structures import LookupDict\n\n_codes = {\n'
        + "\n".join(entries)
        + "\n}\n\ncodes = LookupDict(name='status_codes')\n"
    )
    drafts = _chunks("pkg/status_codes.py", source)
    parts = _assert_data_parts(drafts, "_codes")
    lines = source.split("\n")
    assert parts[0].locator["start_line"] == 5  # ``_codes = {`` opens the first part
    assert parts[-1].text.endswith("}")  # the closing brace ends the last
    body_tokens = [CharEstimate().count(d.text.split("\n", 1)[1]) for d in parts]
    assert max(body_tokens) <= syntax.DATA_TARGET_TOKENS * 2
    for d in parts:
        last = lines[d.locator["end_line"] - 1].strip()
        assert not last.startswith("#")  # a comment goes with the element below it
        first = lines[d.locator["start_line"] - 1].strip()
        assert first.startswith(("_codes", "#")) or re.match(r"\d+: \(", first)
    multi = next(d for d in parts if "'code_31'" in d.text)
    assert "'alias_31',\n    )," in multi.text
    # Code around the literal stays module code, in its own chunks.
    assert drafts[0].locator["kind"] == "module" and "_codes = {" not in drafts[0].text
    assert drafts[-1].locator["kind"] == "module" and "LookupDict(name" in drafts[-1].text


def test_class_level_and_other_language_literals() -> None:
    styles = "\n".join(f"        Token.Kind{i}: 'bold #00{i:02d}ff underline'," for i in range(50))
    py = _chunks(
        "theme.py",
        f"class FlaskyStyle(Style):\n    default = ''\n    styles = {{\n{styles}\n    }}\n",
    )
    _assert_data_parts(py, "FlaskyStyle.styles")
    assert py[0].locator == {
        "file": "theme.py",
        "start_line": 1,
        "end_line": 2,
        "symbol": "FlaskyStyle",
        "kind": "class",
    }

    items = "\n".join(
        f'  E{i}: {{ code: {i}, name: "entry_number_{i}", text: "description {i}" }},'
        for i in range(40)
    )
    ts = _chunks(
        "errors.ts",
        f"import x from 'y';\n\nexport const ERRORS = {{\n{items}\n}} as const;\n\n"
        "export function f() {\n  return ERRORS;\n}\n",
    )
    _assert_data_parts(ts, "ERRORS")
    assert (ts[-1].locator["symbol"], ts[-1].locator["kind"]) == ("f", "function")

    rows = "\n".join(f"  'value_number_{i}_with_some_text'," for i in range(60))
    js = _chunks("list.js", f"module.exports = [\n{rows}\n];\n")
    _assert_data_parts(js, "module.exports")

    cs_rows = "\n".join(
        f'        {{ {i}, "entry_number_{i} some description" }},' for i in range(50)
    )
    cs = _chunks(
        "Codes.cs",
        "namespace N {\npublic static class Codes {\n"
        "    public static readonly Dictionary<int, string> Names = "
        f"new Dictionary<int, string>\n    {{\n{cs_rows}\n    }};\n"
        "    public static string Get(int c) { return Names[c]; }\n}\n}\n",
    )
    _assert_data_parts(cs, "Codes.Names")
    assert ("Codes.Get", "method") in [(d.locator["symbol"], d.locator["kind"]) for d in cs]


def test_small_literals_and_non_literal_values_stay_module_code() -> None:
    small = _chunks("a.py", "TABLE = {\n    1: 'one',\n    2: 'two',\n}\nX = 1\n")
    assert [(d.locator["symbol"], d.locator["kind"]) for d in small] == [(None, "module")]
    calls = "\n".join(f"    make_value({i}, 'argument number {i}', other={i})," for i in range(80))
    # A call with several arguments is not a data literal: it is grouped like other module code.
    drafts = _chunks("b.py", f"VALUES = build(\n    1,\n{calls}\n)\n")
    assert all(d.locator["kind"] == "module" and d.locator["symbol"] is None for d in drafts)


def test_search_text_drops_line_numbers_and_part_numbers() -> None:
    from app.index.base import search_text

    text = "File: src/codes.py · _codes (part 3) (lines 81-88)\n    429: ('too_many',),"
    assert search_text(text) == "File: src/codes.py · _codes\n    429: ('too_many',),"
    plain = "File: src/a.py · f (lines 1-2)\ndef f(): pass"
    assert search_text(plain) == "File: src/a.py · f\ndef f(): pass"
