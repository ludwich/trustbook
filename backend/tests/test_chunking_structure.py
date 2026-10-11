"""D-077 interface: the structure record codec and the parent key helpers."""

from __future__ import annotations

import json

import pytest

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
