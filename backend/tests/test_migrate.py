from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.chat.repository import ChatStore
from app.db.engine import get_engine
from app.db.migrate import (
    MIGRATIONS_ROOT,
    MigrationError,
    latest_version,
    load_migrations,
    migrate,
)
from app.db.models import GLOBAL_TABLES, SPACE_TABLES


def _tables(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def test_global_migration_creates_tables(tmp_path: Path) -> None:
    db = tmp_path / "global.db"
    assert migrate(db, "global") == latest_version("global") >= 1
    assert {"jobs", "request_log", "schema_version"} <= _tables(db)


def test_space_migration_creates_full_schema(tmp_path: Path) -> None:
    db = tmp_path / "space.db"
    migrate(db, "space")
    expected = {
        "sources",
        "chunks",
        "conversations",
        "messages",
        "citations",
        "gate_log",
        "ideation_sessions",
        "ideation_nodes",
    }
    assert expected <= _tables(db)


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "global.db"
    first = migrate(db, "global")
    assert migrate(db, "global") == first
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == first


def test_wal_mode(tmp_path: Path) -> None:
    db = tmp_path / "global.db"
    migrate(db, "global")
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize(("kind", "tables"), [("global", GLOBAL_TABLES), ("space", SPACE_TABLES)])
def test_orm_models_match_schema(tmp_path: Path, kind: str, tables: tuple[type, ...]) -> None:
    db = tmp_path / f"{kind}.db"
    migrate(db, kind)  # type: ignore[arg-type]
    inspector = inspect(get_engine(db))
    for model in tables:
        table = model.__table__  # type: ignore[attr-defined]
        db_columns = {c["name"] for c in inspector.get_columns(table.name)}
        assert {c.name for c in table.columns} == db_columns, table.name


def test_later_migrations_apply_in_order(tmp_path: Path) -> None:
    root = tmp_path / "migrations"
    (root / "space").mkdir(parents=True)
    (root / "space" / "001_init.sql").write_text("CREATE TABLE a (id INTEGER);")
    db = tmp_path / "x.db"
    assert migrate(db, "space", root) == 1
    (root / "space" / "002_more.sql").write_text(
        "-- comment\nCREATE TABLE b (id INTEGER);\nINSERT INTO a VALUES (1);"
    )
    assert migrate(db, "space", root) == 2
    assert {"a", "b"} <= _tables(db)


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    root = tmp_path / "migrations"
    (root / "global").mkdir(parents=True)
    (root / "global" / "001_init.sql").write_text(
        "CREATE TABLE ok (id INTEGER);\nINSERT INTO missing VALUES (1);"
    )
    db = tmp_path / "x.db"
    with pytest.raises(MigrationError, match=r"001_init\.sql"):
        migrate(db, "global", root)
    assert "ok" not in _tables(db)


def test_bad_names_and_gaps(tmp_path: Path) -> None:
    root = tmp_path / "migrations"
    (root / "global").mkdir(parents=True)
    (root / "global" / "init.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="Badly named"):
        load_migrations("global", root)
    (root / "global" / "init.sql").unlink()
    (root / "global" / "002_x.sql").write_text("SELECT 1;")
    with pytest.raises(MigrationError, match="001"):
        load_migrations("global", root)


def test_incomplete_statement(tmp_path: Path) -> None:
    root = tmp_path / "migrations"
    (root / "global").mkdir(parents=True)
    (root / "global" / "001_x.sql").write_text("CREATE TABLE a (id INTEGER")
    with pytest.raises(MigrationError, match="incomplete"):
        migrate(tmp_path / "x.db", "global", root)


def test_migration_007_keeps_old_refusals_readable(tmp_path: Path) -> None:
    """D-118: a refusal stored before ``messages.refusal_reason`` existed reads with no reason
    and its stored title; new rows only take the four refusal reasons."""
    old = tmp_path / "migrations"
    (old / "space").mkdir(parents=True)
    for path in sorted((MIGRATIONS_ROOT / "space").glob("00[1-6]_*.sql")):
        shutil.copy(path, old / "space" / path.name)
    db = tmp_path / "space.db"
    assert migrate(db, "space", old) == 6
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) "
            "VALUES ('c', 'Q', 't', 't')"
        )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, badge, created_at) "
            "VALUES ('m', 'c', 'assistant', "
            '\'{"text": "Not in your sources", "suggestion": "Add sources.", "sentences": []}\', '
            "'refused', 't')"
        )
    assert migrate(db, "space") == latest_version("space") >= 7
    (message,) = ChatStore(get_engine(db)).list_messages("c")
    assert message.badge == "refused" and message.refusal_reason is None
    assert (message.content.text, message.content.suggestion) == (
        "Not in your sources",
        "Add sources.",
    )
    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE messages SET refusal_reason = 'because' WHERE id = 'm'")
