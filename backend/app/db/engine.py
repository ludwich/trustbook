"""SQLAlchemy engines for SQLite files (WAL mode, foreign keys on), cached per path."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import create_engine

_engines: dict[Path, Engine] = {}
_lock = threading.Lock()


def _on_connect(dbapi_conn: sqlite3.Connection, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(db_path: Path) -> Engine:
    """Create a new engine for ``db_path`` (not cached)."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _on_connect)
    return engine


def get_engine(db_path: Path) -> Engine:
    """Return the cached engine for ``db_path``, creating it on first use."""
    key = db_path.resolve()
    with _lock:
        engine = _engines.get(key)
        if engine is None:
            engine = make_engine(key)
            _engines[key] = engine
        return engine


def dispose_engine(db_path: Path) -> None:
    """Close and forget the cached engine for ``db_path`` (before deleting the file)."""
    key = db_path.resolve()
    with _lock:
        engine = _engines.pop(key, None)
    if engine is not None:
        engine.dispose()


def dispose_all() -> None:
    """Close every cached engine (app shutdown, tests)."""
    with _lock:
        engines = list(_engines.values())
        _engines.clear()
    for engine in engines:
        engine.dispose()
