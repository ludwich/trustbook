"""Gate 1 G3: code chunks bring the definitions of the module-level constants they use."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.memory import InMemorySpaceIndex
from app.retrieval.definitions import (
    MAX_EXTRA,
    code_body,
    defined_names,
    fit_extras,
    referenced_names,
)
from app.retrieval.service import RetrievedChunk, retrieve, select_context, with_definitions
from tests.chat_helpers import KeywordEmbedder, OverlapReranker, add_chunk, add_source

ADAPTERS = "src/requests/adapters.py"

CONSTANTS = """File: src/requests/adapters.py (lines 60-64)
DEFAULT_POOLBLOCK = False
DEFAULT_POOLSIZE = 10
DEFAULT_RETRIES = 0
DEFAULT_POOL_TIMEOUT: float | None = None"""

INIT = """File: src/requests/adapters.py (lines 180-200)
class HTTPAdapter(BaseAdapter):
    def __init__(
        self,
        pool_connections=DEFAULT_POOLSIZE,
        pool_maxsize=DEFAULT_POOLSIZE,
        max_retries=DEFAULT_RETRIES,
        pool_block=DEFAULT_POOLBLOCK,
    ):
        if max_retries == DEFAULT_RETRIES:
            self.max_retries = Retry(0, read=False)"""


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return get_engine(db)


def code(file: str, start: int, end: int) -> dict[str, object]:
    return {"file": file, "start_line": start, "end_line": end, "symbol": None}


def retrieved(
    cid: str,
    text: str,
    locator: dict[str, object],
    *,
    source: str = "s",
    tokens: int = 50,
    score: float = 0.9,
) -> RetrievedChunk:
    return RetrievedChunk(cid, source, "requests", text, locator, tokens, score)


def test_names_used_and_defined() -> None:
    assert defined_names(code_body(CONSTANTS)) == {
        "DEFAULT_POOLBLOCK",
        "DEFAULT_POOLSIZE",
        "DEFAULT_RETRIES",
        "DEFAULT_POOL_TIMEOUT",
    }
    assert referenced_names(code_body(INIT)) == [
        "DEFAULT_POOLSIZE",
        "DEFAULT_RETRIES",
        "DEFAULT_POOLBLOCK",
    ]
    # Indented (class-level) and comparisons are not module-level definitions.
    assert defined_names("    INNER = 1\nX_Y == 2\nOK_ = 3\nAB = 4") == {"OK_"}
    assert referenced_names("LIMIT = 3\nuse(LIMIT, OTHER_ONE)") == ["OTHER_ONE"]
    assert code_body("no prefix") == "no prefix"


def test_definition_chunk_is_added_from_the_same_file_first(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests", source_type="folder", group="code")
    other = add_source(engine, "fork", source_type="folder", group="code")
    # Another file of the same source also assigns DEFAULT_POOLSIZE (lower ordinal).
    add_chunk(
        engine,
        index,
        source,
        "File: src/x.py (lines 1-1)\nDEFAULT_POOLSIZE = 99",
        ordinal=0,
        locator=code("src/x.py", 1, 1),
    )
    wanted = add_chunk(engine, index, source, CONSTANTS, ordinal=5, locator=code(ADAPTERS, 60, 64))
    add_chunk(engine, index, other, CONSTANTS, ordinal=0, locator=code(ADAPTERS, 60, 64))
    top = retrieved("top", INIT, code(ADAPTERS, 180, 200), source=source)
    chunks = with_definitions(engine, [top])
    assert [c.chunk_id for c in chunks] == ["top", wanted]
    extra = chunks[1]
    assert extra.definition_of == "DEFAULT_POOLSIZE" and extra.score == 0.0
    assert extra.referrer == "top"  # with_callees must keep the chunk it was added for
    assert "DEFAULT_POOLSIZE = 10" in extra.text and extra.source_id == source


def test_falls_back_to_another_file_of_the_same_source_only(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests", source_type="folder", group="code")
    other = add_source(engine, "fork", source_type="folder", group="code")
    add_chunk(engine, index, other, CONSTANTS, locator=code(ADAPTERS, 60, 64))
    elsewhere = add_chunk(
        engine,
        index,
        source,
        "File: src/c.py (lines 1-2)\nDEFAULT_RETRIES = 0\nDEFAULT_POOLBLOCK = False",
        ordinal=2,
        locator=code("src/c.py", 1, 2),
    )
    # A document chunk (no line locator) with the same text is not code.
    add_chunk(engine, index, source, "x\nDEFAULT_POOLSIZE = 3", ordinal=3, locator={"pages": [1]})
    top = retrieved("top", INIT, code(ADAPTERS, 180, 200), source=source)
    assert [c.chunk_id for c in with_definitions(engine, [top])] == ["top", elsewhere]


def test_nothing_added_for_defined_names_documents_or_unknown_names(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests", source_type="folder", group="code")
    add_chunk(engine, index, source, CONSTANTS, locator=code(ADAPTERS, 60, 64))
    has_defs = retrieved("defs", CONSTANTS, code(ADAPTERS, 60, 64), source=source)
    init = retrieved("init", INIT, code(ADAPTERS, 180, 200), source=source)
    assert [c.chunk_id for c in with_definitions(engine, [init, has_defs])] == ["init", "defs"]
    doc = retrieved("doc", "Set DEFAULT_POOLSIZE in the config.", {"pages": [2]}, source=source)
    assert [c.chunk_id for c in with_definitions(engine, [doc])] == ["doc"]
    unknown = retrieved(
        "u", "File: a.py (lines 1-1)\nx = NOT_DEFINED_ANYWHERE", code("a.py", 1, 1), source=source
    )
    assert [c.chunk_id for c in with_definitions(engine, [unknown])] == ["u"]
    assert with_definitions(engine, []) == []


def test_at_most_three_extra_chunks(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests", source_type="folder", group="code")
    names = [f"CONST_{i}" for i in range(5)]
    for i, name in enumerate(names):
        add_chunk(
            engine,
            index,
            source,
            f"File: k.py (lines {i + 1}-{i + 1})\n{name} = {i}",
            ordinal=i,
            locator=code("k.py", i + 1, i + 1),
        )
    top = retrieved(
        "top",
        "File: m.py (lines 1-1)\nf(" + ", ".join(names) + ")",
        code("m.py", 1, 1),
        source=source,
    )
    chunks = with_definitions(engine, [top])
    assert len(chunks) == 1 + MAX_EXTRA
    assert [c.definition_of for c in chunks[1:]] == names[:MAX_EXTRA]


def test_extras_fit_the_budget_and_never_displace_the_top_chunk() -> None:
    def item(cid: str, tokens: int) -> RetrievedChunk:
        return retrieved(cid, "t", {}, tokens=tokens)

    top, mid, low = item("top", 3000), item("mid", 1500), item("low", 1000)
    extra = item("def", 800)
    # 5,500 + 800 > 6,000: the lowest-ranked chunk makes room.
    assert [c.chunk_id for c in fit_extras([top, mid, low], [(extra, "mid")], max_tokens=6000)] == [
        "top",
        "mid",
        "def",
    ]
    # Room left: nothing is displaced.
    assert [c.chunk_id for c in fit_extras([top, low], [(extra, "top")], max_tokens=6000)] == [
        "top",
        "low",
        "def",
    ]
    # The referrer is protected; then only the top chunk remains, so the extra is left out.
    assert [
        c.chunk_id for c in fit_extras([top, low], [(item("big", 2500), "low")], max_tokens=6000)
    ] == ["top", "low"]
    assert [
        c.chunk_id for c in fit_extras([item("huge", 6000)], [(extra, "huge")], max_tokens=6000)
    ] == ["huge"]


def test_retrieve_adds_the_constants_behind_a_code_answer(engine: Engine) -> None:
    """code-03: the answer needs 10 / 10 / False, defined far from ``HTTPAdapter.__init__``."""
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests", source_type="folder", group="code")
    constants = add_chunk(
        engine, index, source, CONSTANTS, ordinal=0, locator=code(ADAPTERS, 60, 64), token_count=40
    )
    init = add_chunk(
        engine,
        index,
        source,
        INIT + "\n# httpadapter pool connections maxsize block",
        ordinal=1,
        locator=code(ADAPTERS, 180, 200),
        token_count=120,
    )
    for i in range(10):  # outrank the constants chunk, which shares no words with the question
        add_chunk(
            engine,
            index,
            source,
            f"File: src/requests/f{i}.py (lines 1-3)\nhttpadapter pool note {i}",
            ordinal=2 + i,
            locator=code(f"src/requests/f{i}.py", 1, 3),
            token_count=30,
        )
    question = "HTTPAdapter default pool connections maxsize block"
    result = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), question)
    ids = [c.chunk_id for c in result.chunks]
    assert ids[0] == init
    assert constants not in [c.chunk_id for c in select_context(result.ranked)]
    assert ids[-1] == constants and result.chunks[-1].definition_of == "DEFAULT_POOLSIZE"
    assert sum(c.token_count for c in result.chunks) <= 6000
    assert result.top_score == result.ranked[0].score  # refusal still uses the ranked list
