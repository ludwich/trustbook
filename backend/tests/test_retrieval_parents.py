"""Parent context (D-077): citable promotions and the non-citable code outline."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.chunking.structure import Import, Structure, module_key
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.memory import InMemorySpaceIndex
from app.retrieval import parents, service
from app.retrieval.parents import (
    CLASS_HEAD,
    EXPANSION_TOKENS,
    OUTLINE_BLOCK_TOKENS,
    OUTLINE_TOKENS,
    SECTION_PREVIOUS,
    Expansion,
    expand,
    outline_tokens,
    shorten_import,
)
from app.retrieval.service import RetrievedChunk, retrieve
from app.retrieval.store import FileRows, chunks_at_ordinals
from tests.chat_helpers import KeywordEmbedder, OverlapReranker, add_source
from tests.parent_helpers import (
    ADAPTERS,
    HTTP_ADAPTER,
    MODULE,
    Spec,
    adapters_specs,
    add_rows,
    code,
    doc,
    method,
    outline_text_of,
    with_parent_rows,
)


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
    """Context chunks read back from ``space.db`` (with their parent fields), in this order."""
    rows = chunks_at_ordinals(engine, source, ordinals, [source])
    return [RetrievedChunk.from_row(rows[o], score - i * 0.01) for i, o in enumerate(ordinals)]


def run(
    engine: Engine,
    context: Sequence[RetrievedChunk],
    ranked: Sequence[RetrievedChunk] | None = None,
    *,
    outline_on: bool = True,
    max_tokens: int = 6_000,
) -> Expansion:
    sources = sorted({c.source_id for c in context})
    return expand(
        engine,
        context,
        context if ranked is None else ranked,
        file_rows=FileRows(engine, sources),
        allowed_source_ids=sources,
        outline_on=outline_on,
        max_tokens=max_tokens,
    )


# --- promotion: documents -------------------------------------------------------------------

HISTORY_SPECS = [
    doc(
        0,
        150,
        158,
        "Release History › 2.31.0\n## 2.31.0\nOlder notes.",
        section=150,
        head=True,
        title="Release History › 2.31.0",
    ),
    doc(1, 159, 199, "## 2.32.0 (2024-05-20)\nSecurity fixes.", section=159, head=True),
    doc(2, 200, 240, "More 2.32.0 notes about verify=False.", section=159, head=False),
    doc(3, 241, 260, "Even more 2.32.0 notes.", section=159, head=False, tokens=601),
]


def test_a_continuing_section_child_gets_the_chunk_before_it(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    ids = add_rows(engine, index, source, HISTORY_SPECS)
    found = run(engine, chunks(engine, source, [2]))
    (promoted,) = found.promoted
    assert (promoted.row.chunk_id, promoted.kind, promoted.referrer) == (
        ids[1],
        SECTION_PREVIOUS,
        ids[2],
    )
    assert promoted.row.text == "## 2.32.0 (2024-05-20)\nSecurity fixes."  # the stored child
    assert found.outline == ()  # documents get no non-citable text
    assert found.promoted_tokens == promoted.row.token_count


def test_a_section_head_child_gets_nothing(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, HISTORY_SPECS)
    assert run(engine, chunks(engine, source, [1])) == Expansion()


def test_the_previous_chunk_must_be_in_the_section_or_reach_its_start(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    ids = add_rows(
        engine,
        index,
        source,
        [
            # Starts in 2.31.0 but its lines reach the 2.32.0 heading at line 159.
            doc(
                0,
                140,
                165,
                "tail of 2.31.0\n## 2.32.0",
                section=140,
                head=True,
                title="Release History › 2.31.0",
            ),
            doc(1, 166, 199, "2.32.0 body", section=159, head=False),
            doc(2, 1, 9, "other file", section=1, head=True, file="NEWS.md"),
            doc(3, 10, 30, "continues 3.0", section=5, head=False, file="NEWS2.md"),
        ],
    )
    (promoted,) = run(engine, chunks(engine, source, [1])).promoted
    assert promoted.row.chunk_id == ids[0]
    # The chunk before is another file: nothing.
    assert run(engine, chunks(engine, source, [3])).promoted == ()


def test_a_previous_chunk_over_600_tokens_is_not_promoted(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(
        engine,
        index,
        source,
        [
            doc(0, 1, 50, "big part", section=1, head=True, tokens=601),
            doc(1, 51, 60, "second part", section=1, head=False),
        ],
    )
    assert run(engine, chunks(engine, source, [1])).promoted == ()


# --- promotion: code ------------------------------------------------------------------------


def test_a_method_child_gets_its_small_class_head(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    ids = add_rows(engine, index, source, adapters_specs(head_tokens=200))
    found = run(engine, chunks(engine, source, [5]))
    (promoted,) = found.promoted
    assert (promoted.row.chunk_id, promoted.kind, promoted.referrer) == (ids[2], CLASS_HEAD, ids[5])
    assert promoted.score == 0.0  # not ranked: it never enters ``ranked``


def test_a_large_class_head_is_promoted_only_when_ranked(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    ids = add_rows(engine, index, source, adapters_specs(head_tokens=201))
    context = chunks(engine, source, [5])
    assert run(engine, context).promoted == ()
    ranked = [*context, *chunks(engine, source, [2], score=0.4)]
    (promoted,) = run(engine, context, ranked).promoted
    assert promoted.row.chunk_id == ids[2]
    assert promoted.score == pytest.approx(0.4)  # keeps its rerank score


def test_module_and_head_children_get_no_promotion(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs())
    assert run(engine, chunks(engine, source, [0, 2])).promoted == ()


def test_only_the_first_three_ranked_context_chunks_are_expanded_never_extras(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    ids = add_rows(
        engine,
        index,
        source,
        [*adapters_specs(), *[replace(s, ordinal=s.ordinal + 10) for s in HISTORY_SPECS[:3]]],
    )
    context = chunks(engine, source, [0, 1, 11, 5])  # the method is 4th
    assert run(engine, context).promoted == ()
    # An extra (a D-032 / D-060 addition) is never expanded, even at the top.
    extra = replace(chunks(engine, source, [12])[0], definition_of="X", referrer=ids[0])
    assert run(engine, [*chunks(engine, source, [0]), extra]).promoted == ()


def test_caps_three_promotions_and_900_tokens(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "requests")
    specs: list[Spec] = []
    for n in range(4):  # four documents, each a head (500 tokens) and a continuation
        file = f"doc{n}.md"
        specs.append(doc(2 * n, 1, 10, f"head {n}", section=1, head=True, file=file, tokens=500))
        specs.append(doc(2 * n + 1, 11, 20, f"cont {n}", section=1, head=False, file=file))
    add_rows(engine, index, source, specs)
    found = run(engine, chunks(engine, source, [1, 3, 5]))
    # 500 + 500 > 900: only the first child's previous chunk.
    assert len(found.promoted) == 1 and found.promoted_tokens == 500
    assert EXPANSION_TOKENS == 900


def test_at_most_three_promotions(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "requests")
    specs: list[Spec] = []
    for n in range(4):
        file = f"doc{n}.md"
        specs.append(doc(2 * n, 1, 10, f"head {n}", section=1, head=True, file=file, tokens=50))
        specs.append(doc(2 * n + 1, 11, 20, f"cont {n}", section=1, head=False, file=file))
    add_rows(engine, index, source, specs)
    context = chunks(engine, source, [1, 3, 5, 7])
    found = run(engine, context)
    assert [p.referrer for p in found.promoted] == [c.chunk_id for c in context[:3]]


def test_promotion_uses_only_free_budget_and_never_displaces(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, HISTORY_SPECS)
    context = chunks(engine, source, [2])
    used = sum(c.token_count for c in context)
    assert run(engine, context, max_tokens=used).promoted == ()  # no free budget
    size = chunks(engine, source, [1])[0].token_count
    assert len(run(engine, context, max_tokens=used + size).promoted) == 1


def test_a_chunk_already_in_the_context_is_not_promoted_twice(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs())
    assert run(engine, chunks(engine, source, [5, 2])).promoted == ()  # the head is in context
    # Two methods of one class: one class head.
    found = run(engine, chunks(engine, source, [5, 3]))
    assert len(found.promoted) == 1


# --- outline --------------------------------------------------------------------------------


def test_the_outline_lists_used_imports_module_names_and_class_members(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs())
    found = run(engine, chunks(engine, source, [5]))
    (block,) = found.outline
    assert block.file == ADAPTERS and block.source_id == source
    assert block.lines == (
        # ``urlparse`` is used by ``send``; ``basestring`` and ``unquote`` are cut to "…".
        "imports: from .compat import …, urlparse, …",
        "module: DEFAULT_POOLBLOCK, DEFAULT_POOLSIZE, DEFAULT_RETRIES, DEFAULT_POOL_TIMEOUT, "
        "class BaseAdapter, class HTTPAdapter",
        # The class head is promoted into the context, so its declaration is not repeated.
        "HTTPAdapter: __init__, init_poolmanager, send, close",
    )
    assert block.tokens == outline_tokens(block.render())
    assert found.outline_tokens == block.tokens


def test_the_class_declaration_is_shown_when_the_head_is_not_in_context(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs(head_tokens=300))  # not promoted
    (block,) = run(engine, chunks(engine, source, [5])).outline
    assert (
        block.lines[-1] == "class HTTPAdapter(BaseAdapter): __init__, init_poolmanager, send, close"
    )


def test_outline_lines_visible_in_a_context_chunk_are_left_out(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs())
    # The module head chunk holds every import: no imports line.
    (block,) = run(engine, chunks(engine, source, [5, 0])).outline
    assert not any(line.startswith("imports:") for line in block.lines)


def test_outline_off_keeps_the_promotions(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs())
    found = run(engine, chunks(engine, source, [5]), outline_on=False)
    assert found.outline == () and found.outline_tokens == 0 and len(found.promoted) == 1


def test_the_outline_has_no_values_line_numbers_or_labels(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    specs = adapters_specs(head_tokens=300)
    # A TypeScript-style alias import and a value-carrying declaration never reach the outline.
    module = specs[0]
    assert module.structure is not None
    specs[0] = replace(
        module,
        structure=replace(
            module.structure,
            imports=(
                *module.structure.imports,
                Import(10, 10, "import x = require('y')", ("x",), None),
            ),
        ),
    )
    specs[2] = replace(specs[2], structure=replace(specs[2].structure, decl="class A(B=1)"))  # type: ignore[arg-type]
    specs.append(
        method(7, "HTTPAdapter", "build", 212, 220, "    def build(self):\n        return x(1)")
    )
    add_rows(engine, index, source, specs)
    text = outline_text_of(run(engine, chunks(engine, source, [5, 7])).outline)
    assert text
    assert "=" not in text
    assert not re.search(r"\[c\d+\]|\bc\d+\b", text)
    assert not re.search(r"\blines?\b|\d+\s*[-–]\s*\d+", text)
    for spec in specs:  # no numbers from any locator
        assert f"lines {spec.locator.get('start_line')}" not in text


def test_module_names_are_capped_at_24_with_the_count_left_out(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    names = tuple(f"N{i:02d}" for i in range(30))
    specs = [
        code(
            0,
            None,
            "module",
            1,
            40,
            "x",
            parent_key=MODULE,
            structure=Structure("module", ADAPTERS, None, True, defines=names),
        ),
        code(
            1,
            "f",
            "function",
            41,
            50,
            "def f():\n    pass",
            parent_key=MODULE,
            structure=Structure("module", ADAPTERS, None, False, defines=("f",)),
        ),
    ]
    add_rows(engine, index, source, specs)
    (block,) = run(engine, chunks(engine, source, [1])).outline
    (line,) = [line for line in block.lines if line.startswith("module:")]
    assert line.endswith(", … and 7 more")
    shown = line.removeprefix("module: ").split(", … and")[0].split(", ")
    assert len(shown) <= 24
    assert "f" in shown  # the child's own name first


def test_class_members_are_capped_with_gaps(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "requests")
    specs = adapters_specs(head_tokens=300)[:3]
    specs += [
        method(10 + i, "HTTPAdapter", f"m{i:02d}", 300 + i, 300 + i, "pass") for i in range(40)
    ]
    add_rows(engine, index, source, specs)
    (block,) = run(engine, chunks(engine, source, [30])).outline
    line = block.lines[-1]
    assert "m20" in line and "…" in line
    assert block.tokens <= OUTLINE_BLOCK_TOKENS


def test_blocks_are_capped_per_file_and_per_prompt_lowest_ranked_dropped(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    specs: list[Spec] = []
    for n in range(4):
        file = f"pkg/m{n}.py"
        key = module_key(file)
        names = tuple(f"NAME_{n}_{i:02d}_LONG_ENOUGH" for i in range(14))
        specs.append(
            code(
                n,
                f"f{n}",
                "function",
                1,
                9,
                f"def f{n}(): pass",
                parent_key=key,
                structure=Structure("module", file, None, True, defines=names),
                file=file,
            )
        )
    add_rows(engine, index, source, specs)
    context = chunks(engine, source, [0, 1, 2])
    blocks = run(engine, context).outline
    assert all(b.tokens <= OUTLINE_BLOCK_TOKENS for b in blocks)
    assert sum(b.tokens for b in blocks) <= OUTLINE_TOKENS
    assert [b.file for b in blocks] == ["pkg/m0.py", "pkg/m1.py", "pkg/m2.py"][: len(blocks)]
    assert len(blocks) < 3  # the third (lowest-ranked) block did not fit the prompt cap


def test_outline_tokens_count_utf8_bytes() -> None:
    assert outline_tokens("abc") == 1
    assert outline_tokens("abcd") == 2
    assert outline_tokens("日本") == 2  # 6 bytes


def test_shorten_import_cuts_unused_names_and_never_invents() -> None:
    imp = Import(1, 3, "from .compat import (a, b as c, d)", ("a", "c", "d"), None)
    assert shorten_import(imp, {"c"}) == "from .compat import …, b as c, …"
    assert shorten_import(imp, {"a", "d"}) == "from .compat import a, …, d"
    assert shorten_import(imp, {"zzz"}) is None
    assert shorten_import(imp, {"a", "c", "d"}) == "from .compat import (a, b as c, d)"
    js = Import(1, 1, 'import { a, b, c } from "./x"', ("a", "b", "c"), "x.ts")
    assert shorten_import(js, {"b"}) == 'import { …, b, … } from "./x"'


# --- determinism, switches, errors ----------------------------------------------------------


@pytest.mark.parametrize(
    "order", [(0, 1, 2, 3, 4, 5, 6), (6, 5, 4, 3, 2, 1, 0), (3, 0, 6, 1, 5, 2, 4)]
)
def test_expansion_is_the_same_for_any_index_write_order(
    tmp_path: Path, order: tuple[int, ...]
) -> None:
    db = tmp_path / "space.db"
    migrate(db, "space")
    engine = get_engine(db)
    source = add_source(engine, "requests")
    specs = adapters_specs()
    add_rows(engine, InMemorySpaceIndex(), source, [specs[i] for i in order])
    found = run(engine, chunks(engine, source, [5, 3]))
    shape = (
        [(p.row.ordinal, p.kind, p.row.text) for p in found.promoted],
        [b.render() for b in found.outline],
    )
    assert shape == (
        [(2, CLASS_HEAD, specs[2].text)],
        [found.outline[0].render()],
    )
    assert (
        found.outline[0].lines[0]
        == "imports: from urllib3.util.retry import Retry; from .compat import …, urlparse, …"
    )


def retrieval_env(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> tuple[InMemorySpaceIndex, str]:
    """``adapters.py`` plus notes that outrank its class head, so the head is not in context."""
    with_parent_rows(monkeypatch)
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests")
    notes = [
        doc(
            20 + n,
            1,
            5,
            f"Note {n} about the request.",
            section=None,
            head=False,
            file=f"notes{n}.md",
        )
        for n in range(8)
    ]
    add_rows(engine, index, source, [*adapters_specs(), *notes])
    return index, source


QUESTION = "How does send use the connection and urlparse request url?"


def test_retrieve_appends_promotions_and_the_outline(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, _ = retrieval_env(engine, monkeypatch)
    found = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION)
    assert found.chunks[0].locator.get("symbol") == "HTTPAdapter.send"
    promoted = found.promoted
    assert [c.expansion for c in promoted] == ["class_head"]
    assert found.chunks[-1] == promoted[0]  # promoted chunks go last
    assert promoted[0].referrer == found.chunks[0].chunk_id
    assert found.outline and found.outline[0].file == ADAPTERS


def test_ranked_and_top_score_do_not_change_with_expansion(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, _ = retrieval_env(engine, monkeypatch)
    on = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION)
    off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, parent_context=False
    )
    assert on.ranked == off.ranked and on.top_score == off.top_score
    assert on.chunks[: len(off.chunks)] == off.chunks


def test_parent_context_off_restores_todays_retrieval_exactly(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, source = retrieval_env(engine, monkeypatch)
    off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, parent_context=False
    )
    # Today's pipeline: search, rerank, select, agreement, constants, callees; nothing else.
    found = service.search(engine, index, KeywordEmbedder(), QUESTION, [source])
    ranked = service.rerank(QUESTION, found.rows, OverlapReranker())
    chosen = service.keep_agreed(service.select_context(ranked), ranked, found.agreed)
    today = service.with_callees(service.with_definitions(engine, chosen, ranked), ranked)
    assert off == service.Retrieval(chunks=today, ranked=ranked, source_ids=[source])
    assert off.outline == () and off.promoted == []
    outline_off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, outline=False
    )
    assert outline_off.outline == () and outline_off.promoted


def test_an_expansion_error_gives_no_expansion_and_a_log_line(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    index, _ = retrieval_env(engine, monkeypatch)
    off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, parent_context=False
    )

    def broken(*_args: object, **_kwargs: object) -> Expansion:
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "expand", broken)
    with caplog.at_level(logging.WARNING, logger="trustbook.retrieval"):
        found = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION)
    assert found == off
    assert "Expanding parent context failed" in caplog.text


def test_rows_without_structure_get_no_expansion(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "requests")
    specs = [replace(s, parent_key=None, structure=None) for s in adapters_specs()]
    add_rows(engine, index, source, specs)
    assert run(engine, chunks(engine, source, [5, 3, 0])) == Expansion()


def test_promoted_chunk_text_is_the_stored_child_text(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "requests")
    add_rows(engine, index, source, adapters_specs())
    (promoted,) = run(engine, chunks(engine, source, [5])).promoted
    stored = chunks_at_ordinals(engine, source, [2], [source])[2]
    assert promoted.row == stored
    assert parents.MAX_PROMOTED == 3 and HTTP_ADAPTER.endswith("::HTTPAdapter")
