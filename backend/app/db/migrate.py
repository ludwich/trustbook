"""Numbered SQL migration runner.

Migrations live in ``app/db/migrations/<kind>/NNN_name.sql`` and are applied in order, each in its
own transaction. Applied versions are recorded in the ``schema_version`` table.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.timeutil import utc_now_iso

DbKind = Literal["global", "space"]

MIGRATIONS_ROOT = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    """A migration file is malformed or failed to apply."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations(kind: DbKind, root: Path = MIGRATIONS_ROOT) -> list[Migration]:
    """Return the migrations for ``kind`` sorted by version; versions must be 1..N without gaps."""
    folder = root / kind
    migrations: list[Migration] = []
    for path in sorted(folder.glob("*.sql")):
        match = _NAME_RE.match(path.name)
        if match is None:
            raise MigrationError(f"Badly named migration file: {path.name}")
        migrations.append(Migration(int(match.group(1)), path.name, path.read_text("utf-8")))
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise MigrationError(f"Migration {expected:03d} for {kind} is missing")
    return migrations


def latest_version(kind: DbKind, root: Path = MIGRATIONS_ROOT) -> int:
    """Return the newest migration version available for ``kind``."""
    migrations = load_migrations(kind, root)
    return migrations[-1].version if migrations else 0


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied version (0 for a fresh database)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0])


def migrate(db_path: Path, kind: DbKind, root: Path = MIGRATIONS_ROOT) -> int:
    """Apply all pending migrations to the SQLite file at ``db_path``; return the schema version."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = current_version(conn)
        for migration in load_migrations(kind, root):
            if migration.version <= version:
                continue
            try:
                conn.execute("BEGIN")
                for statement in _split_statements(migration.sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, utc_now_iso()),
                )
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                conn.execute("ROLLBACK")
                raise MigrationError(f"Migration {migration.name} failed: {exc}") from exc
            version = migration.version
        return version
    finally:
        conn.close()


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into complete statements (comments stripped)."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError("Migration ends with an incomplete statement")
    return statements
