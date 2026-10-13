"""Transcript windows (D-091): non-citable neighbours around the top transcript children."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.chat.prompt import describe_locator
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.memory import InMemorySpaceIndex
from app.retrieval import parents, service
from app.retrieval.parents import (
    WINDOW_ANSWER_TOKENS,
    WINDOW_PLACEHOLDER,
    WINDOW_REACH,
    WINDOW_TOKENS,
    Expansion,
    TranscriptWindow,
    expand,
    outline_tokens,
    transcript_children,
    transcript_windows,
    window_fingerprint,
    window_text,
)
from app.retrieval.service import RetrievedChunk, retrieve
from app.retrieval.store import FileRows, chunks_at_ordinals
from tests.chat_helpers import KeywordEmbedder, OverlapReranker, add_source
from tests.parent_helpers import adapters_specs, add_rows, meeting_specs, turn


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
    *,
    allowed: Sequence[str] | None = None,
    max_tokens: int = 6_000,
) -> Expansion:
    sources = sorted({c.source_id for c in context}) if allowed is None else list(allowed)
    return expand(
        engine,
        context,
        context,
        file_rows=FileRows(engine, sources),
        allowed_source_ids=sources,
        max_tokens=max_tokens,
    )


def ordinals(window: TranscriptWindow) -> list[tuple[int, bool]]:
    """(ordinal, shows text) per line."""
    return [(p.ordinal, p.text is not None) for p in window.parts]


def meeting(engine: Engine, index: InMemorySpaceIndex, *, tokens: int | None = None) -> str:
    source = add_source(engine, "Planning call", source_type="upload")
    add_rows(engine, index, source, meeting_specs(tokens=tokens))
    return source


def test_constants() -> None:
    assert (WINDOW_TOKENS, WINDOW_ANSWER_TOKENS, WINDOW_REACH) == (2_000, 2_400, 2)


def test_a_transcript_child_gets_two_neighbours_on_each_side_in_time_order(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = meeting(engine, index)
    found = run(engine, chunks(engine, source, [3]))
    (window,) = found.windows
    assert ordinals(window) == [(1, True), (2, True), (3, False), (4, True), (5, True)]
    assert window.title == "Planning call" and window.source_id == source
    assert found.promoted == () and found.outline == ()  # transcripts have no parent key
    assert found.window_tokens == window.tokens > 0


def test_the_window_stops_at_the_first_and_last_chunk_of_its_source(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = meeting(engine, index)
    (first,) = run(engine, chunks(engine, source, [0])).windows
    assert ordinals(first) == [(0, False), (1, True), (2, True)]
    (last,) = run(engine, chunks(engine, source, [6])).windows
    assert ordinals(last) == [(4, True), (5, True), (6, False)]


def test_only_transcript_children_among_the_first_three_ranked_count(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    code = add_source(engine, "requests", source_type="folder")
    add_rows(engine, index, code, adapters_specs())
    source = meeting(engine, index)
    talk = chunks(engine, source, [1, 5])
    code_chunks = chunks(engine, code, [0, 1])
    # An extra (D-032 / D-060) is never a child, and a fourth ranked chunk is not either.
    extra = replace(talk[0], definition_of="BUDGET", referrer=code_chunks[0].chunk_id)
    context = [*code_chunks, extra, talk[1]]
    assert transcript_children(context) == [talk[1]]
    context = [code_chunks[0], talk[1], code_chunks[1], talk[0]]
    assert [c.chunk_id for c in transcript_children(context)] == [talk[1].chunk_id]
    # A chunk without a known position gets no window.
    assert transcript_children([replace(talk[0], ordinal=-1)]) == []
    found = run(engine, [code_chunks[0], talk[1], code_chunks[1], talk[0]])
    (window,) = found.windows
    assert window.numbered_ids == (talk[1].chunk_id,)


def test_neighbours_go_minus_one_plus_one_then_minus_two_plus_two(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = meeting(engine, index, tokens=700)
    (window,) = run(engine, chunks(engine, source, [3])).windows
    # 700 child + 700 (-1) = 1,400; +1 would make 2,100 > 2,000: one neighbour, the earlier one.
    assert ordinals(window) == [(2, True), (3, False)]


def test_a_direction_stops_at_the_first_neighbour_that_does_not_fit(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "Call", source_type="upload")
    specs = [
        turn(0, "Small talk before.", tokens=50),
        turn(1, "A very long monologue about budgets.", tokens=1_700),
        turn(2, "The child turn.", tokens=400),
        turn(3, "A short reply.", tokens=50),
        turn(4, "Another short reply.", tokens=50),
    ]
    add_rows(engine, index, source, specs)
    (window,) = run(engine, chunks(engine, source, [2])).windows
    # -1 does not fit (400 + 1,700 > 2,000), so -2 is never shown: no gap in a window.
    assert ordinals(window) == [(2, False), (3, True), (4, True)]
    span = sum(s.tokens or 0 for s in specs if s.ordinal in (2, 3, 4))
    assert span <= WINDOW_TOKENS


def test_all_windows_add_at_most_2400_tokens(engine: Engine, index: InMemorySpaceIndex) -> None:
    sources = [meeting(engine, index, tokens=450) for _ in range(3)]
    context = [chunks(engine, s, [3])[0] for s in sources]
    found = run(engine, context)
    assert found.window_tokens <= WINDOW_ANSWER_TOKENS
    assert found.window_tokens == sum(w.tokens for w in found.windows)
    # The first window gets ±1 and -2 (450 × 4 = 1,800 ≤ 2,000); what is left of the 2,400 goes
    # to the next (±1), and nothing to the third.
    first, second = found.windows
    assert ordinals(first) == [(1, True), (2, True), (3, False), (4, True)]
    assert ordinals(second) == [(2, True), (3, False), (4, True)]
    assert first.tokens + second.tokens + 456 > WINDOW_ANSWER_TOKENS  # no third line fits
    assert {w.source_id for w in found.windows} == set(sources[:2])


def test_windows_use_only_the_free_context_budget(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = meeting(engine, index, tokens=300)
    context = chunks(engine, source, [3])
    assert run(engine, context, max_tokens=300 + 200).windows == ()  # no neighbour fits
    found = run(engine, context, max_tokens=300 + 400)
    (window,) = found.windows
    assert ordinals(window) == [(2, True), (3, False)]
    assert 300 + found.window_tokens <= 300 + 400


def test_the_window_tokens_add_up(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = meeting(engine, index, tokens=100)
    (window,) = run(engine, chunks(engine, source, [3])).windows
    expected = outline_tokens(f"{window.title}\n")
    for part in window.parts:
        if part.text is None:
            expected += outline_tokens(f"  {part.render()}\n")
        else:
            expected += outline_tokens(f"  {part.label()} \n") + 100
    assert window.tokens == expected


def test_numbered_neighbours_are_placeholders_and_overlapping_windows_merge(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = meeting(engine, index)
    context = chunks(engine, source, [3, 4])
    found = run(engine, context)
    (window,) = found.windows  # 1..5 and 2..6 join into one block
    assert ordinals(window) == [
        (1, True),
        (2, True),
        (3, False),
        (4, False),
        (5, True),
        (6, True),
    ]
    assert window.numbered_ids == tuple(c.chunk_id for c in context)
    assert window.render().count(WINDOW_PLACEHOLDER) == 2
    assert found.window_tokens == window.tokens  # each line counted once


def test_windows_that_touch_merge_and_sources_never_mix(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "Long call", source_type="upload")
    add_rows(engine, index, source, [turn(n, f"Turn number {n} words.") for n in range(12)])
    other = meeting(engine, index)
    context = [*chunks(engine, source, [2, 7]), chunks(engine, other, [3])[0]]
    found = run(engine, context)
    first, second = found.windows
    # 0..4 and 5..9 touch: one block of ten turns; the other source has its own block.
    assert [p.ordinal for p in first.parts] == list(range(10))
    assert first.source_id == source and second.source_id == other
    assert all(p.chunk_id not in first.chunk_ids for p in second.parts)


def test_a_disallowed_source_gets_no_window(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = meeting(engine, index)
    assert run(engine, chunks(engine, source, [3]), allowed=["another"]).windows == ()


def test_a_child_without_a_neighbour_to_show_gets_no_window(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = add_source(engine, "Clip", source_type="upload")
    add_rows(engine, index, source, [turn(0, "Only turn.")])
    assert run(engine, chunks(engine, source, [0])) == Expansion()
    two = add_source(engine, "Pair", source_type="upload")
    add_rows(engine, index, two, [turn(0, "First."), turn(1, "Second.")])
    assert run(engine, chunks(engine, two, [0, 1])).windows == ()  # both are numbered


def test_the_window_renders_times_speakers_and_placeholders(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    source = meeting(engine, index)
    context = chunks(engine, source, [3])
    (window,) = run(engine, context).windows
    assert window.render() == (
        "Planning call\n"
        "  [1:00–2:00] Bo: The budget for the bridge project is forty thousand euros.\n"
        "  [2:00–3:00] Anna: We agreed to ship the release on Friday.\n"
        f"  [3:00–4:00] {WINDOW_PLACEHOLDER}\n"
        "  [4:00–5:00] Anna: The venue for the launch party is the harbour hall.\n"
        "  [5:00–6:00] Bo: Testing starts on Monday morning."
    )
    # The child's line shows exactly the times of its numbered header.
    assert describe_locator(context[0].locator).startswith("3:00–4:00")
    assert "[c" not in window.render()
    assert window_text([window]) == window.render()
    fingerprint = window_fingerprint([window])
    assert isinstance(fingerprint, str) and len(fingerprint) == 16
    assert window_fingerprint([]) is None


def test_window_text_is_flat_and_unescaped(engine: Engine, index: InMemorySpaceIndex) -> None:
    source = add_source(engine, "Call", source_type="upload")
    add_rows(
        engine,
        index,
        source,
        [turn(0, "Child."), turn(1, "Read \\~/.netrc\n  and   then\n\nstop.", speaker=" Bo ")],
    )
    (window,) = run(engine, chunks(engine, source, [0])).windows
    assert window.parts[1].render() == "[1:00–2:00] Bo: Read ~/.netrc and then stop."


def test_windows_come_after_promotions_in_the_budget(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    code = add_source(engine, "requests", source_type="folder")
    add_rows(engine, index, code, adapters_specs())
    source = meeting(engine, index, tokens=300)
    context = [*chunks(engine, code, [5]), *chunks(engine, source, [3])]
    used = sum(c.token_count for c in context)
    with_promotion = run(engine, context, max_tokens=used + 1_000)
    assert with_promotion.promoted  # the class head keeps first claim on the free budget
    promoted = with_promotion.promoted_tokens
    assert promoted + with_promotion.window_tokens <= 1_000
    alone = transcript_windows(
        engine, context, transcript_children(context), allowed_source_ids=[code, source]
    )
    assert sum(w.tokens for w in alone) >= with_promotion.window_tokens


# --- retrieve ---------------------------------------------------------------------------------

QUESTION = "When will we ship the release on Friday?"


def long_call(engine: Engine, index: InMemorySpaceIndex) -> str:
    """Thirty 400-token turns: the context holds 8 of them, so neighbours are left over."""
    source = add_source(engine, "Planning call", source_type="upload")
    specs = [turn(n, f"Filler item {n} about weather.", tokens=400) for n in range(30)]
    specs[14] = turn(14, "We agreed to ship the release on Friday.", tokens=400)
    add_rows(engine, index, source, specs)
    return source


def test_retrieve_returns_windows_and_never_changes_the_chunks(
    engine: Engine, index: InMemorySpaceIndex
) -> None:
    long_call(engine, index)
    on = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION)
    off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, parent_context=False
    )
    assert on.transcript_windows and off.transcript_windows == ()
    assert on.chunks == off.chunks and on.ranked == off.ranked
    outline_off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, outline=False
    )
    assert outline_off.transcript_windows == on.transcript_windows
    numbered = {c.chunk_id for c in on.chunks}
    for window in on.transcript_windows:
        assert not numbered & set(window.chunk_ids)  # a numbered chunk is never repeated


def test_a_window_error_gives_no_windows_and_a_log_line(
    engine: Engine,
    index: InMemorySpaceIndex,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    long_call(engine, index)
    off = retrieve(
        engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION, parent_context=False
    )

    def broken(*_args: object, **_kwargs: object) -> list[TranscriptWindow]:
        raise RuntimeError("boom")

    monkeypatch.setattr(parents, "transcript_windows", broken)
    with caplog.at_level(logging.WARNING, logger="trustbook.retrieval"):
        found = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), QUESTION)
    assert found == off and found.transcript_windows == ()
    assert "Expanding parent context failed" in caplog.text
    assert service.Retrieval(chunks=[]).transcript_windows == ()
