"""The users of a module-level data table (D-119, G3-2 F1).

A ``status_codes.py``-like file: a module docstring naming ``requests.codes``, a ``_codes`` table
split into parts (D-056), ``codes = LookupDict(...)`` and ``_init``, which loops over ``_codes`` and
sets each name on ``codes``. When a table part is in the context and no context chunk uses
``_codes``, the chunk that does is promoted (``data_user``), within the D-077 promotion caps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.chunking.structure import class_key
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.memory import InMemorySpaceIndex
from app.retrieval.parents import (
    DATA_USER,
    EXPANSION_TOKENS,
    MAX_PROMOTED,
    PROMOTE_MAX_TOKENS,
    Expansion,
    Promotion,
    data_symbol,
    data_user_promotions,
    expand,
    uses_name,
)
from app.retrieval.service import RetrievedChunk
from app.retrieval.store import FileRows, chunks_at_ordinals
from tests.chat_helpers import add_source
from tests.parent_helpers import DOC, INIT, STATUS, TABLE_HEAD, add_rows, status_specs


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return get_engine(db)


@pytest.fixture
def index() -> InMemorySpaceIndex:
    return InMemorySpaceIndex()


def chunks(
    engine: Engine, source: str, ordinals: Sequence[int], score: float = 0.9
) -> list[RetrievedChunk]:
    rows = chunks_at_ordinals(engine, source, ordinals, [source])
    return [RetrievedChunk.from_row(rows[o], score - i * 0.01) for i, o in enumerate(ordinals)]


def run(
    engine: Engine,
    context: Sequence[RetrievedChunk],
    ranked: Sequence[RetrievedChunk] | None = None,
    *,
    max_tokens: int = 6_000,
) -> Expansion:
    sources = sorted({c.source_id for c in context})
    return expand(
        engine,
        context,
        context if ranked is None else ranked,
        file_rows=FileRows(engine, sources),
        allowed_source_ids=sources,
        max_tokens=max_tokens,
    )


def setup(engine: Engine, index: InMemorySpaceIndex, **sizes: int) -> tuple[str, dict[int, str]]:
    source = add_source(engine, "requests", source_type="folder")
    return source, add_rows(engine, index, source, status_specs(**sizes))


# --- what a data table and a use are ---------------------------------------------------------


def test_a_data_symbol_is_a_module_level_literal_part(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source, _ = setup(engine, index)
    head, part6, codes_line, init = chunks(engine, source, [1, 2, 3, 4])
    assert (data_symbol(head), data_symbol(part6)) == ("_codes", "_codes")
    assert data_symbol(codes_line) is None and data_symbol(init) is None
    # A class-level table is not module-level: its parent is the class.
    nested = RetrievedChunk(
        "x",
        source,
        "requests",
        "File: a.py · Box.TABLE (part 2) (lines 5-9)\n    1: 2,",
        {"file": "a.py", "start_line": 5, "end_line": 9, "kind": "data", "symbol": "Box.TABLE"},
        10,
        0.1,
        parent_key=class_key("a.py", "Box"),
    )
    assert data_symbol(nested) is None


def test_uses_name_is_a_whole_word_read_not_its_definition_or_an_attribute() -> None:
    assert uses_name("_codes", f"File: {STATUS} · _init (lines 109-125)\n{INIT}")
    assert uses_name("_codes", DOC)
    assert not uses_name("_codes", f"File: {STATUS} · _codes (part 1) (lines 23-40)\n{TABLE_HEAD}")
    assert not uses_name("_codes", "self._codes = {}\nother_codes = 1\n_codes_x = 2")
    assert not uses_name("_codes", f"File: {STATUS} · _codes (lines 1-2)\n")  # the header only
    assert uses_name("_codes", "x = 1\n_codes: dict[int, str] = {}\nprint(_codes)")


# --- the promotion -----------------------------------------------------------------------------


def test_a_table_part_in_context_brings_the_chunk_that_reads_the_table(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source, ids = setup(engine, index)
    context = chunks(engine, source, [0, 2])  # the docstring and the 429 part, nothing using it
    found = run(engine, context)
    (promoted,) = found.promoted
    assert (promoted.row.chunk_id, promoted.kind, promoted.referrer) == (ids[4], DATA_USER, ids[2])
    assert promoted.kind == "data_user"
    assert promoted.row.text == f"File: {STATUS} · _init (lines 109-125)\n{INIT}"  # verbatim
    assert promoted.score == 0.0  # not reranked
    assert found.promoted_tokens == 120
    assert run(engine, context) == found  # deterministic


def test_a_reranked_user_comes_before_an_earlier_unranked_one(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source, ids = setup(engine, index)
    context = chunks(engine, source, [0, 2])
    ranked = [*context, *chunks(engine, source, [5], score=0.03)]
    (promoted,) = run(engine, context, ranked).promoted
    assert (promoted.row.chunk_id, promoted.referrer) == (ids[5], ids[2])
    assert promoted.score == pytest.approx(0.03)


def test_no_promotion_when_the_context_already_uses_the_table(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source, _ = setup(engine, index)
    assert run(engine, chunks(engine, source, [2, 5])).promoted == ()
    assert run(engine, chunks(engine, source, [0, 3])).promoted == ()  # no table in context


def test_two_parts_of_one_table_bring_one_user(engine: Engine, index: InMemorySpaceIndex) -> None:
    source, ids = setup(engine, index)
    found = run(engine, chunks(engine, source, [1, 2]))
    assert [(p.row.chunk_id, p.referrer) for p in found.promoted] == [(ids[4], ids[1])]


def test_the_promotion_caps_hold(engine: Engine, index: InMemorySpaceIndex) -> None:
    source, ids = setup(engine, index, init_tokens=PROMOTE_MAX_TOKENS + 1)
    context = chunks(engine, source, [0, 2])
    # _init is over the per-chunk cap: the next user that fits is taken.
    (promoted,) = run(engine, context).promoted
    assert promoted.row.chunk_id == ids[5]
    # No free budget left: nothing is promoted and nothing is displaced.
    used = sum(c.token_count for c in context)
    assert run(engine, context, max_tokens=used + 59).promoted == ()
    assert [p.row.chunk_id for p in run(engine, context, max_tokens=used + 60).promoted] == [ids[5]]


def test_the_answer_caps_are_shared_with_the_parent_promotions(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source, _ = setup(engine, index)
    context = chunks(engine, source, [0, 2])
    rows = chunks_at_ordinals(engine, source, [3], [source])
    files = FileRows(engine, [source])
    full = [Promotion(rows[3], 0.0, "class_head", "x")] * MAX_PROMOTED
    assert data_user_promotions(context, full, {}, file_rows=files) == []

    def spent(tokens: int) -> list[Promotion]:
        return [Promotion(replace(rows[3], token_count=tokens), 0.0, "class_head", "x")]

    # 100 tokens of EXPANSION_TOKENS left: _init (120) does not fit, doc (60) does.
    (left,) = data_user_promotions(context, spent(EXPANSION_TOKENS - 100), {}, file_rows=files)
    assert left.row.locator["symbol"] == "doc"
    assert data_user_promotions(context, spent(EXPANSION_TOKENS - 50), {}, file_rows=files) == []
