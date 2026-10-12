"""The ideation store: migration 006 on an existing database, seeding once, turns that no longer
apply are not stored, risks are de-duplicated and capped, delete removes the whole tree."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from app.db.engine import get_engine
from app.db.migrate import MIGRATIONS_ROOT, migrate
from app.ideation.repository import (
    MAX_RISKS,
    AskedQuestion,
    IdeationStore,
    SessionNotFoundError,
)
from app.ideation.schemas import Recommendation, Risk


@pytest.fixture
def store(tmp_path: Path) -> IdeationStore:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return IdeationStore(get_engine(db))


def asked(question: str = "Q?") -> AskedQuestion:
    return AskedQuestion(question, Recommendation(grounding="suggestion"))


def test_migration_006_upgrades_existing_rows(tmp_path: Path) -> None:
    old = tmp_path / "migrations"
    (old / "space").mkdir(parents=True)
    for path in sorted((MIGRATIONS_ROOT / "space").glob("00[1-5]_*.sql")):
        shutil.copy(path, old / "space" / path.name)
    db = tmp_path / "space.db"
    assert migrate(db, "space", old) == 5
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO ideation_sessions (id, idea, created_at, updated_at) "
            "VALUES ('s', 'An idea', 't', 't')"
        )
        conn.execute(
            "INSERT INTO ideation_nodes (id, session_id, title, created_at, updated_at) "
            "VALUES ('n', 's', 'Branch', 't', 't')"
        )
    assert migrate(db, "space") == 6
    session = IdeationStore(get_engine(db)).get("s")
    assert session.risks == [] and session.brief_at is None
    node = session.nodes[0]
    assert (node.origin, node.recommendation, node.asked_at) == ("answer", None, None)


def test_seed_happens_once_and_only_while_active(store: IdeationStore) -> None:
    sid = store.create("Bird trips to Gull Island").id
    assert store.seed(sid, "Bird trips", ["A", "B", "C"])
    assert not store.seed(sid, "Again", ["D", "E", "F"])
    session = store.get(sid)
    assert session.title == "Bird trips"
    assert [(n.title, n.origin, n.ordinal) for n in session.nodes] == [
        ("A", "seed", 0),
        ("B", "seed", 1),
        ("C", "seed", 2),
    ]
    other = store.create("Other").id
    store.set_status(other, "finished", None)
    assert not store.seed(other, "x", ["A", "B", "C"])


def test_turns_that_no_longer_apply_are_not_stored(store: IdeationStore) -> None:
    sid = store.create("Idea").id
    store.seed(sid, None, ["A", "B", "C"])
    a, b, c = (n.id for n in store.get(sid).nodes)
    store.dismiss(sid, [b])
    assert not store.apply_turn(sid, b, asked(), [], [])  # dismissed meanwhile
    assert store.apply_turn(sid, a, asked(), [("Child", a)], [Risk(text="R", node_id=a)])
    store.answer(sid, a, "Yes", "user")
    assert not store.apply_turn(sid, a, asked("Again?"), [], [])  # answered meanwhile
    store.set_status(sid, "finished", None)
    assert not store.apply_turn(sid, c, asked(), [], [])
    session = store.get(sid)
    by_title = {n.title: n for n in session.nodes}
    assert by_title["A"].question == "Q?" and by_title["A"].final_answer == "Yes"
    assert by_title["C"].question is None
    assert by_title["Child"].parent_id == a and by_title["Child"].depth == 1


def test_risks_are_deduplicated_and_capped(store: IdeationStore) -> None:
    sid = store.create("Idea").id
    risks = [Risk(text=f"Risk {i}") for i in range(MAX_RISKS + 5)] + [Risk(text="risk 0")]
    store.apply_turn(sid, None, None, [], risks)
    stored = store.get(sid).risks
    assert len(stored) == MAX_RISKS and stored[0].text == "Risk 0"


def test_delete_removes_the_tree(store: IdeationStore) -> None:
    sid = store.create("Idea").id
    store.seed(sid, None, ["A", "B", "C"])
    a = store.get(sid).nodes[0].id
    store.apply_turn(sid, None, None, [("Child", a)], [])
    store.delete(sid)
    with pytest.raises(SessionNotFoundError):
        store.get(sid)
    assert store.nodes(sid) == [] and store.list_sessions() == []
