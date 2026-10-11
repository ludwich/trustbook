"""D-077 interface: ChunkRow parent fields, FileRows and chunks_at_ordinals."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, text

from app.chunking.structure import (
    Structure,
    class_key,
    line_section_key,
    module_key,
    structure_json,
)
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.retrieval.service import RetrievedChunk
from app.retrieval.store import ChunkRow, FileRows, chunks_at_ordinals, load_chunks
from tests.chat_helpers import add_source

ADAPTERS = "src/requests/adapters.py"
HEAD = Structure(
    "class", "HTTPAdapter", module_key(ADAPTERS), True, "class HTTPAdapter(BaseAdapter)"
)
MODULE = Structure("module", ADAPTERS, None, True, defines=("DEFAULT_POOLSIZE",))

AddRow = Callable[..., str]


def _engine(tmp_path: Path, *, parent_columns: bool) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    engine = get_engine(db)
    if parent_columns:
        with engine.begin() as conn:
            names = {r[1] for r in conn.execute(text("PRAGMA table_info(chunks)"))}
            # The columns of migration 005 (D-077), added here until the migration exists.
            for column in ("parent_key", "structure"):
                if column not in names:
                    conn.execute(text(f"ALTER TABLE chunks ADD COLUMN {column} TEXT"))
    return engine


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return _engine(tmp_path, parent_columns=True)


def adder(engine: Engine, source_id: str, *, parent_columns: bool = True) -> AddRow:
    def add(
        ordinal: int,
        *,
        file: str = ADAPTERS,
        parent_key: str | None = None,
        structure: str | None = None,
    ) -> str:
        chunk_id = f"{source_id[:6]}-{ordinal}"
        locator = json.dumps({"file": file, "start_line": ordinal * 10 + 1, "end_line": 0})
        values = {
            "id": chunk_id,
            "source_id": source_id,
            "ordinal": ordinal,
            "text": f"File: {file}\nchunk {ordinal}",
            "locator": locator,
            "parent_key": parent_key,
            "structure": structure,
        }
        columns = "id, source_id, ordinal, text, start_offset, end_offset, locator, token_count"
        params = ":id, :source_id, :ordinal, :text, 0, 1, :locator, 7"
        if parent_columns:
            columns += ", parent_key, structure"
            params += ", :parent_key, :structure"
        with engine.begin() as conn:
            conn.execute(text(f"INSERT INTO chunks ({columns}) VALUES ({params})"), values)
        return chunk_id

    return add


def count_chunk_selects(engine: Engine) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    return statements


def test_chunk_row_and_retrieved_chunk_keep_old_constructors() -> None:
    row = ChunkRow("a", "s", "T", 0, "alpha", {}, 1)
    assert row.parent_key is None
    assert row.structure is None
    chunk = RetrievedChunk("a", "s", "T", "alpha", {}, 1, 0.5)
    assert chunk.parent_key is None
    assert chunk.structure is None


def test_from_row_carries_the_parent_and_keeps_the_child_text() -> None:
    row = ChunkRow("a", "s", "T", 3, "child", {}, 1, class_key(ADAPTERS, "HTTPAdapter"), HEAD)
    chunk = RetrievedChunk.from_row(row, 0.5)
    assert chunk.parent_key == class_key(ADAPTERS, "HTTPAdapter")
    assert chunk.structure == HEAD
    assert chunk.text == "child"


def test_file_rows_returns_the_files_code_families_by_ordinal(engine: Engine) -> None:
    source = add_source(engine, "requests")
    add = adder(engine, source)
    module = module_key(ADAPTERS)
    adapter = class_key(ADAPTERS, "HTTPAdapter")
    first = add(0, parent_key=module, structure=structure_json(MODULE))
    head = add(3, parent_key=adapter, structure=structure_json(HEAD))
    method = add(4, parent_key=adapter, structure="{broken")
    add(1, file="src/requests/auth.py", parent_key=module_key("src/requests/auth.py"))
    add(2, parent_key=None)  # fallback-chunked or old row: no family
    add(5, file="HISTORY.md", parent_key=line_section_key("HISTORY.md", 159))
    # A file whose name extends this one's key prefix stays out.
    add(6, file=f"{ADAPTERS}::x", parent_key=module_key(f"{ADAPTERS}::x"))

    rows = FileRows(engine, [source])(source, ADAPTERS)
    assert [r.chunk_id for r in rows] == [first, head, method]
    assert [r.parent_key for r in rows] == [module, adapter, adapter]
    assert rows[0].structure == MODULE
    assert rows[1].structure == HEAD
    assert rows[2].structure is None  # broken JSON is tolerated as "no structure"
    assert rows[0].source_title == "requests"
    assert rows[0].locator["file"] == ADAPTERS


def test_file_rows_queries_once_per_file_and_caches(engine: Engine) -> None:
    source = add_source(engine, "requests")
    add = adder(engine, source)
    add(0, parent_key=module_key(ADAPTERS), structure=structure_json(MODULE))
    add(1, file="b.py", parent_key=module_key("b.py"))
    file_rows = FileRows(engine, [source])
    selects = count_chunk_selects(engine)

    first = file_rows(source, ADAPTERS)
    again = file_rows(source, ADAPTERS)
    other = file_rows(source, "b.py")
    file_rows(source, "missing.py")
    file_rows(source, "missing.py")

    assert again is first
    assert [len(first), len(other)] == [1, 1]
    assert file_rows.queries == 3
    assert sum("parent_key >=" in s for s in selects) == 3


def test_file_rows_applies_the_allowed_source_filter(engine: Engine) -> None:
    allowed = add_source(engine, "allowed")
    hidden = add_source(engine, "hidden")
    adder(engine, allowed)(0, parent_key=module_key(ADAPTERS))
    adder(engine, hidden)(0, parent_key=module_key(ADAPTERS))
    file_rows = FileRows(engine, [allowed])
    assert [r.source_id for r in file_rows(allowed, ADAPTERS)] == [allowed]
    assert file_rows(hidden, ADAPTERS) == ()
    assert file_rows(allowed, "") == ()
    assert file_rows.queries == 1


def test_without_parent_columns_file_rows_is_empty_and_rows_have_no_parent(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, parent_columns=False)
    source = add_source(engine, "requests")
    add = adder(engine, source, parent_columns=False)
    first = add(0)
    add(1)
    file_rows = FileRows(engine, [source])
    assert file_rows(source, ADAPTERS) == ()
    assert file_rows.queries == 0
    found = chunks_at_ordinals(engine, source, [0, 1], [source])
    assert found[0].chunk_id == first
    assert (found[0].parent_key, found[0].structure) == (None, None)


def test_chunks_at_ordinals(engine: Engine) -> None:
    source = add_source(engine, "requests")
    other = add_source(engine, "other")
    add = adder(engine, source)
    ids = [
        add(0, parent_key=module_key(ADAPTERS), structure=structure_json(MODULE)),
        add(1),
        add(2, parent_key=class_key(ADAPTERS, "HTTPAdapter"), structure=structure_json(HEAD)),
    ]
    adder(engine, other)(1)

    found = chunks_at_ordinals(engine, source, [2, 0, 7, 2], [source, other])
    assert sorted(found) == [0, 2]
    assert found[0].chunk_id == ids[0]
    assert found[0].structure == MODULE
    assert found[2].parent_key == class_key(ADAPTERS, "HTTPAdapter")
    assert found[2].structure == HEAD
    assert found[2].source_title == "requests"
    # The same rows load_chunks returns, plus the parent fields.
    loaded = load_chunks(engine, [ids[1]], [source])[ids[1]]
    assert chunks_at_ordinals(engine, source, [1], [source])[1] == loaded

    assert chunks_at_ordinals(engine, source, [0], [other]) == {}
    assert chunks_at_ordinals(engine, source, [], [source]) == {}
