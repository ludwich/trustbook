"""D-077: ChunkRow parent fields, FileRows, chunks_at_ordinals, migration 005 and refresh."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, text

from app.chunking.dispatch import CHUNKER_VERSION
from app.chunking.document import ChunkDraft
from app.chunking.structure import (
    Structure,
    class_key,
    line_section_key,
    module_key,
    structure_json,
)
from app.db.engine import get_engine
from app.db.migrate import MIGRATIONS_ROOT, migrate
from app.ingest import incremental
from app.jobs.calibration import pipeline_key
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
    """A space.db at the latest schema, or (``parent_columns=False``) one migrated only up to
    004, as a space.db looks before migration 005 (D-077)."""
    db = tmp_path / "space.db"
    if parent_columns:
        migrate(db, "space")
    else:
        root = tmp_path / "migrations"
        (root / "space").mkdir(parents=True)
        for path in sorted((MIGRATIONS_ROOT / "space").glob("00[1-4]_*.sql")):
            shutil.copy(path, root / "space" / path.name)
        migrate(db, "space", root)
    return get_engine(db)


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


# --- migration 005, rows and refresh (D-077) ---------------------------------------------------


def test_migration_005_applies_to_an_existing_space_db(tmp_path: Path) -> None:
    engine = _engine(tmp_path, parent_columns=False)  # a space.db as it was before 005
    source = add_source(engine, "requests")
    old = adder(engine, source, parent_columns=False)(0)
    engine.dispose()
    assert migrate(tmp_path / "space.db", "space") == 5
    engine = get_engine(tmp_path / "space.db")
    with engine.connect() as conn:
        columns = {r[1] for r in conn.execute(text("PRAGMA table_info(chunks)"))}
        indexes = {r[1] for r in conn.execute(text("PRAGMA index_list(chunks)"))}
    assert {"parent_key", "structure"} <= columns
    assert "chunks_parent" in indexes
    row = load_chunks(engine, [old], [source])[old]
    assert (row.parent_key, row.structure) == (None, None)  # old rows: no parent until refresh


def test_rows_carry_the_parent_through_the_orm(engine: Engine) -> None:
    source = add_source(engine, "requests")
    add = adder(engine, source)
    good = add(0, parent_key=class_key(ADAPTERS, "HTTPAdapter"), structure=structure_json(HEAD))
    broken = add(1, parent_key=module_key(ADAPTERS), structure='{"kind": "module", "title": 3}')
    rows = load_chunks(engine, [good, broken], [source])
    assert rows[good].parent_key == class_key(ADAPTERS, "HTTPAdapter")
    assert rows[good].structure == HEAD
    assert rows[broken].parent_key == module_key(ADAPTERS)
    assert rows[broken].structure is None  # a misshapen record reads as no structure


def test_new_rows_store_the_structure_and_moved_rows_keep_it() -> None:
    draft = ChunkDraft(
        "File: x.py (lines 1-2)\nA = 1\nB = 2",
        10,
        20,
        {"file": "x.py"},
        9,
        module_key("x.py"),
        MODULE,
    )
    (row,) = incremental._new_rows("s", [draft])
    assert row.parent_key == module_key("x.py")
    assert row.structure == structure_json(MODULE)
    plain = ChunkDraft("transcript turn", 0, 5, {"start_s": 0.0}, 3)
    (none,) = incremental._new_rows("s", [plain])
    assert (none.parent_key, none.structure) == (None, None)
    moved = incremental._moved(row, 100)
    assert (moved.start_offset, moved.end_offset) == (110, 120)
    assert (moved.parent_key, moved.structure) == (row.parent_key, row.structure)
    assert moved.id == row.id and moved.text == row.text


def test_chunker_version_six_changes_the_calibration_pipeline() -> None:
    """D-077: the chunker bump re-chunks every file on refresh and makes saved calibration
    rates stale (D-057), without anyone bumping CALIBRATION_VERSION."""
    assert CHUNKER_VERSION == "p3-treesitter-6"
    current = pipeline_key("google/embeddinggemma-2", "rev")
    assert "chunker-p3-treesitter-6" in current
    assert current != current.replace("p3-treesitter-6", "p3-treesitter-5")
