"""D-078: the D-032 constant and D-060 callee lookups through parent keys pick what they picked
before, except the disclosed differences (names bound in try/if blocks or JS/TS ``const``, import
targets breaking ties and naming a qualified call's module); rows without structure, and calls
without the row cache, behave exactly as before."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from app.chunking.dispatch import chunk_snapshot
from app.chunking.structure import class_key, module_key
from app.chunking.tokens import CharEstimate
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.ingest import incremental
from app.parse.snapshot import FileEntry, Snapshot, SnapshotBuilder
from app.retrieval import definitions
from app.retrieval.callees import callee_extras
from app.retrieval.definitions import Extra, definition_extras
from app.retrieval.store import ChunkRow, FileRows, load_chunks
from tests.chat_helpers import add_source

ADAPTERS = "src/pkg/adapters.py"
ADAPTERS_PY = """from .auth import _basic_auth_str
from . import helpers as h
from .consts import *
from .limits import MAX_SIZE
from . import settings

try:
    import fast
    FAST_PATH = True
except ImportError:
    FAST_PATH = False

DEFAULT_POOLSIZE = 10


class HTTPAdapter:
    def __init__(self, pool=DEFAULT_POOLSIZE):
        self.pool = pool

    def send(self, request):
        headers = _basic_auth_str("u", "p")
        if FAST_PATH and len(request) > MAX_SIZE and STAR_LIMIT and settings.TIMEOUT_S:
            return h.normalize(request)
        return self.close(request)

    def close(self, request):
        return request

    class Inner:
        def run(self):
            return self.step()

        def step(self):
            return 1
"""
ENTRIES = "\n".join(f'    "e{i}": "message number {i} of the error table",' for i in range(80))
FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/other.py": "MAX_SIZE = 200\n",  # before limits.py: the first by ordinal
    ADAPTERS: ADAPTERS_PY,
    "src/pkg/auth.py": "def _basic_auth_str(u, p):\n    return u + p\n",
    "src/pkg/helpers.py": "def normalize(r):\n    return r\n",
    "src/pkg/consts.py": "STAR_LIMIT = 4\n",
    "src/pkg/limits.py": "MAX_SIZE = 100\n",
    "src/pkg/settings.py": "TIMEOUT_S = 5\n",
    "src/pkg/errors.py": (
        f"ERROR_MESSAGES = {{\n{ENTRIES}\n}}\n\n\ndef message(code):\n"
        "    return ERROR_MESSAGES[code]\n"
    ),
    "web/client.ts": (
        "export const RETRY_LIMIT = 3;\n\nexport function retry(n: number): boolean {\n"
        "  return n < RETRY_LIMIT;\n}\n"
    ),
}


def snapshot(files: dict[str, str] | None = None) -> Snapshot:
    builder = SnapshotBuilder()
    entries: list[FileEntry] = []
    for path, body in (FILES if files is None else files).items():
        builder.text(f"# {path}\n\n")
        start, end = builder.add(body, file=path, start_line=1, end_line=body.count("\n") + 1)
        builder.text("\n\n")
        entries.append(FileEntry(path, start, end, "code"))
    return Snapshot(builder.build(), "files", builder.segments, files=entries)


def index(
    engine: Engine, *, structure: bool, files: dict[str, str] | None = None
) -> tuple[str, list[ChunkRow]]:
    """A source holding the fixture's chunks (``structure=False``: as rows indexed before
    chunker p3-treesitter-6, without parent data), and its rows by ordinal."""
    drafts = chunk_snapshot(snapshot(files), CharEstimate())
    if not structure:
        drafts = [dataclasses.replace(d, parent_key=None, structure=None) for d in drafts]
    source = add_source(engine, "pkg")
    rows = incremental._new_rows(source, drafts)
    for ordinal, row in enumerate(rows):
        row.ordinal = ordinal
    ids = [r.id for r in rows]
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()
    loaded = load_chunks(engine, ids, [source])
    return source, sorted(loaded.values(), key=lambda r: r.ordinal)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return get_engine(db)


def chunk(rows: Sequence[ChunkRow], file: str, symbol: str | None, line: int = 0) -> ChunkRow:
    for row in rows:
        locator = row.locator
        if (
            locator["file"] == file
            and locator.get("symbol") == symbol
            and (not line or locator["start_line"] <= line <= locator["end_line"])
        ):
            return row
    raise AssertionError(f"no chunk {file} {symbol}")


def named(extras: Sequence[Extra]) -> list[tuple[str, str, str]]:
    return [(e.name, e.row.locator["file"], e.row.chunk_id) for e in extras]


class Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        self.calls: list[tuple[Any, ...]] = []
        original = getattr(definitions, name)

        def spy(*args: Any) -> Any:
            self.calls.append(args)
            return original(*args)

        monkeypatch.setattr(definitions, name, spy)


def test_a_same_file_constant_comes_from_the_module_family_without_the_search(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, rows = index(engine, structure=True)
    init = chunk(rows, ADAPTERS, "HTTPAdapter.__init__")
    before = named(definition_extras(engine, [init]))
    assert [n for n, _, _ in before] == ["DEFAULT_POOLSIZE"]
    glob = Spy(monkeypatch, "chunks_assigning")
    file_rows = FileRows(engine, [source])
    after = named(definition_extras(engine, [init], file_rows=file_rows))
    assert after == before
    assert glob.calls == []  # D-078: no source-wide GLOB for a same-file name
    assert file_rows.queries == 1


def test_cross_file_star_import_and_attribute_names_still_take_the_search(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, rows = index(engine, structure=True)
    send = chunk(rows, ADAPTERS, "HTTPAdapter.send")
    before = {n: f for n, f, _ in named(definition_extras(engine, [send], max_extra=10))}
    glob = Spy(monkeypatch, "chunks_assigning")
    file_rows = FileRows(engine, [source])
    found = definition_extras(engine, [send], max_extra=10, file_rows=file_rows)
    after = {n: f for n, f, _ in named(found)}
    searched = {name for call in glob.calls for name in call[2]}
    assert {"STAR_LIMIT", "TIMEOUT_S", "MAX_SIZE"} <= searched  # misses in the file: GLOB
    assert after["STAR_LIMIT"] == before["STAR_LIMIT"] == "src/pkg/consts.py"  # star import
    assert after["TIMEOUT_S"] == before["TIMEOUT_S"] == "src/pkg/settings.py"  # settings.X
    # Disclosed differences: the import target breaks the tie between two defining files, and
    # a name bound in a module-level try block is now found in the referrer's own file.
    assert before["MAX_SIZE"] == "src/pkg/other.py"
    assert after["MAX_SIZE"] == "src/pkg/limits.py"
    assert "FAST_PATH" not in before
    assert after["FAST_PATH"] == ADAPTERS


def test_a_js_const_is_found_in_its_file(engine: Engine) -> None:
    source, rows = index(engine, structure=True)
    retry = chunk(rows, "web/client.ts", "retry")
    assert definition_extras(engine, [retry]) == []  # ``export const X`` is not at column 0
    (extra,) = definition_extras(engine, [retry], file_rows=FileRows(engine, [source]))
    assert extra.name == "RETRY_LIMIT" and extra.row.locator["file"] == "web/client.ts"


def test_split_literal_parts_are_the_same_from_the_file_rows(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, rows = index(engine, structure=True)
    message = chunk(rows, "src/pkg/errors.py", "message")
    before = named(definition_extras(engine, [message]))
    assert len(before) > 2 and {n for n, _, _ in before} == {"ERROR_MESSAGES"}
    parts = Spy(monkeypatch, "chunks_of_symbol")
    after = named(definition_extras(engine, [message], file_rows=FileRows(engine, [source])))
    assert after == before
    assert parts.calls == []
    scores = {before[-1][2]: 0.9}
    assert named(definition_extras(engine, [message], scores=scores)) == named(
        definition_extras(engine, [message], scores=scores, file_rows=FileRows(engine, [source]))
    )


def callees(
    selected: Sequence[ChunkRow], ranked: Sequence[ChunkRow], file_rows: FileRows | None
) -> list[tuple[str, str, tuple[str, ...]]]:
    found = callee_extras(selected, ranked, max_callees=5, file_rows=file_rows)
    return [(c.locator["symbol"], name, callers) for c, name, callers in found]


def test_callees_are_the_same_except_an_import_target_names_the_module(engine: Engine) -> None:
    source, rows = index(engine, structure=True)
    send = chunk(rows, ADAPTERS, "HTTPAdapter.send")
    before = callees([send], rows, None)
    after = callees([send], rows, FileRows(engine, [source]))
    assert ("_basic_auth_str", "_basic_auth_str", (send.chunk_id,)) in before
    assert ("HTTPAdapter.close", "HTTPAdapter.close", (send.chunk_id,)) in before
    assert set(before) <= set(after)
    # ``from . import helpers as h`` then ``h.normalize(``: the stored import target names the
    # module that D-062's file-or-folder rule cannot (``h`` is no file name).
    assert set(after) - set(before) == {("normalize", "h.normalize", (send.chunk_id,))}


def test_receiver_calls_resolve_in_the_nested_class_family(engine: Engine) -> None:
    source, rows = index(engine, structure=True)
    run = chunk(rows, ADAPTERS, "HTTPAdapter.Inner.run")
    assert run.parent_key == class_key(ADAPTERS, "HTTPAdapter.Inner")
    found = callee_extras([run], rows, file_rows=FileRows(engine, [source]))
    assert [(c.locator["symbol"], c.parent_key) for c, _, _ in found] == [
        ("HTTPAdapter.Inner.step", class_key(ADAPTERS, "HTTPAdapter.Inner"))
    ]
    assert [c.chunk_id for c, _, _ in found] == [
        c.chunk_id for c, _, _ in callee_extras([run], rows)
    ]


def test_rows_without_structure_give_exactly_the_old_output(engine: Engine) -> None:
    source, rows = index(engine, structure=False)
    assert all(r.parent_key is None and r.structure is None for r in rows)
    file_rows = FileRows(engine, [source])
    for selected in (
        [chunk(rows, ADAPTERS, "HTTPAdapter.send")],
        [chunk(rows, ADAPTERS, "HTTPAdapter.__init__")],
        [chunk(rows, "src/pkg/errors.py", "message")],
        [chunk(rows, "web/client.ts", "retry"), chunk(rows, ADAPTERS, "HTTPAdapter.Inner.run")],
    ):
        assert definition_extras(engine, selected, max_extra=10, file_rows=file_rows) == (
            definition_extras(engine, selected, max_extra=10)
        )
        assert callee_extras(selected, rows, max_callees=5, file_rows=file_rows) == (
            callee_extras(selected, rows, max_callees=5)
        )
    assert file_rows(source, ADAPTERS) == ()


def test_without_the_row_cache_structured_rows_take_the_old_path(engine: Engine) -> None:
    _, rows = index(engine, structure=True)
    _, plain = index(engine, structure=False)
    send = chunk(rows, ADAPTERS, "HTTPAdapter.send")
    plain_send = chunk(plain, ADAPTERS, "HTTPAdapter.send")
    with_structure = [(e.name, e.row.locator) for e in definition_extras(engine, [send])]
    without = [(e.name, e.row.locator) for e in definition_extras(engine, [plain_send])]
    assert with_structure == without
    assert [(c.locator, n) for c, n, _ in callee_extras([send], rows)] == [
        (c.locator, n) for c, n, _ in callee_extras([plain_send], plain)
    ]
    assert module_key(ADAPTERS) in {r.parent_key for r in rows}


BLOCK_SCOPED = {
    "lib/config.js": "export const TIMEOUT = 30;\n",
    "lib/client.js": (
        "import { TIMEOUT } from './config';\nif (process.env.FAST) {\n  const TIMEOUT = 1;\n"
        "  warm(TIMEOUT);\n}\n"
        "export function fetchIt(url) { return fetch(url, { timeout: TIMEOUT }); }\n"
    ),
}


def test_a_block_scoped_js_const_is_never_a_modules_definition(engine: Engine) -> None:
    """``const TIMEOUT = 1`` inside a top-level ``if`` block is not the module's ``TIMEOUT``: the
    function uses the imported one, so the block's value must not be added as its definition."""
    source, rows = index(engine, structure=True, files=BLOCK_SCOPED)
    fetch_it = chunk(rows, "lib/client.js", "fetchIt")
    file_rows = FileRows(engine, [source])
    wanted = [w for w in definitions.wanted_names([fetch_it]) if w.name == "TIMEOUT"]
    assert wanted and definitions.family_definition(file_rows, wanted[0]) is None
    found = definition_extras(engine, [fetch_it], file_rows=file_rows)
    assert not any("TIMEOUT = 1" in e.row.text for e in found)
    assert found == definition_extras(engine, [fetch_it])
