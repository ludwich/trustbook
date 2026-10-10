"""Parallel embedding streams: the runner, the layout choice and the code workload (Gate 1 G2)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import pytest

from app.capabilities.streams import (
    MIN_STREAM_SPEEDUP,
    EmbeddedBatch,
    StreamLayout,
    choose_streams,
    embed_in_streams,
    length_batches,
    stream_candidates,
    synthetic_code_chunks,
)
from app.jobs.handlers import JobCancelledError


class RecordingEmbedder:
    """A replica that records which thread ran each batch; vectors encode the text."""

    dim = 2

    def __init__(self, replica: int, delay: float = 0.0) -> None:
        self.replica = replica
        self.delay = delay
        self.threads: set[str] = set()
        self.batches = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.threads.add(threading.current_thread().name)
            time.sleep(self.delay)
            self.batches += 1
            return [[float(int(t.split()[-1])), float(self.replica)] for t in texts]
        finally:
            with self._lock:
                self.active -= 1


def _texts(n: int) -> list[str]:
    # Varying lengths, so length batching reorders them; the last word is the index.
    return [("word " * ((i * 7) % 13)) + str(i) for i in range(n)]


def _collect(
    texts: list[str], replicas: list[RecordingEmbedder], **kwargs: object
) -> tuple[dict[int, list[float]], list[EmbeddedBatch], int]:
    vectors: dict[int, list[float]] = {}
    batches: list[EmbeddedBatch] = []

    def on_batch(batch: EmbeddedBatch) -> None:
        assert threading.current_thread() is threading.main_thread()
        batches.append(batch)
        for index, vector in zip(batch.indices, batch.vectors, strict=True):
            assert index not in vectors
            vectors[index] = vector

    used = embed_in_streams(
        texts,
        embedder_for=lambda stream: replicas[stream],
        streams=len(replicas),
        on_batch=on_batch,
        **kwargs,  # type: ignore[arg-type]
    )
    return vectors, batches, used


def test_every_text_gets_its_own_vector_across_replicas() -> None:
    texts = _texts(50)
    replicas = [RecordingEmbedder(i, delay=0.005) for i in range(3)]
    vectors, batches, used = _collect(texts, replicas, batch_size=4)
    assert used == 3
    assert sorted(vectors) == list(range(50))
    assert all(vectors[i][0] == float(i) for i in range(50))  # order preserved by index
    assert sum(len(b.indices) for b in batches) == 50
    # Each replica ran only in its own stream thread, and every stream did some work.
    for replica in replicas:
        assert replica.threads == {f"embed-stream-{replica.replica}"}
        assert replica.batches > 0
    assert {b.stream for b in batches} == {0, 1, 2}
    # The vectors say which replica made them.
    assert all(vectors[i][1] == float(b.stream) for b in batches for i in b.indices)


def test_streams_never_exceed_batches_and_empty_input() -> None:
    replicas = [RecordingEmbedder(i) for i in range(4)]
    _, batches, used = _collect(_texts(3), replicas, batch_size=8)
    assert used == 1 and len(batches) == 1
    assert _collect([], replicas)[2] == 0


def test_length_batches_longest_first_and_complete() -> None:
    texts = ["a", "ccc", "bb", "dddd", "e"]
    assert length_batches(texts, 2) == [[3, 1], [2, 0], [4]]


def test_stream_error_is_raised_and_streams_stop() -> None:
    class Failing(RecordingEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("model crashed")

    replicas = [RecordingEmbedder(0, delay=0.01), Failing(1)]
    with pytest.raises(RuntimeError, match="model crashed"):
        _collect(_texts(40), replicas, batch_size=2)
    assert replicas[0].batches < 20  # stopped early, not after all the work


def test_wrong_vector_count_is_an_error() -> None:
    class Short(RecordingEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return []

    with pytest.raises(RuntimeError, match="wrong number"):
        _collect(_texts(4), [Short(0)], batch_size=2)


def test_cancellation_mid_way_stops_streams() -> None:
    replicas = [RecordingEmbedder(i, delay=0.01) for i in range(2)]
    seen: list[int] = []

    def check() -> None:
        if len(seen) >= 3:
            raise JobCancelledError

    def on_batch(batch: EmbeddedBatch) -> None:
        seen.append(len(batch.indices))

    with pytest.raises(JobCancelledError):
        embed_in_streams(
            _texts(200),
            embedder_for=lambda s: replicas[s],
            streams=2,
            on_batch=on_batch,
            batch_size=2,
            check_cancelled=check,
        )
    done = sum(r.batches for r in replicas)
    assert 3 <= done < 20  # each stream finishes at most its current batch, then stops
    assert all(r.active == 0 for r in replicas)  # threads were joined


def test_slow_writer_holds_streams_back() -> None:
    """The queue is bounded: streams never run far ahead of the consumer."""
    replicas = [RecordingEmbedder(i) for i in range(2)]
    produced: list[int] = []
    lead: list[int] = []

    def counting(stream: int) -> RecordingEmbedder:
        produced.append(stream)
        return replicas[stream]

    def on_batch(_batch: EmbeddedBatch) -> None:
        lead.append(len(produced) - len(lead) - 1)
        time.sleep(0.005)

    embed_in_streams(
        _texts(60),
        embedder_for=counting,
        streams=2,
        on_batch=on_batch,
        batch_size=1,
        queue_size=2,
    )
    # At most queue (2) + one batch in flight per stream (2) ahead of the writer.
    assert max(lead) <= 4


def test_guard_wraps_every_model_call() -> None:
    inside: list[bool] = []
    state = {"held": 0}

    @contextmanager
    def guard() -> Iterator[None]:
        state["held"] += 1
        try:
            yield
        finally:
            state["held"] -= 1

    class Checking(RecordingEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            inside.append(state["held"] > 0)
            return super().embed_documents(texts)

    _collect(_texts(10), [Checking(0), Checking(1)], batch_size=2, guard=guard)
    assert inside and all(inside)


# --- choosing the layout ---------------------------------------------------------------------


def test_stream_candidates() -> None:
    def fits(_n: int) -> bool:
        return True

    assert stream_candidates(8, 7, fits) == [
        StreamLayout(1, 7),
        StreamLayout(2, 4),
        StreamLayout(4, 2),
    ]
    assert [c.label for c in stream_candidates(32, 16, fits)] == ["1×16", "2×16", "4×8", "8×4"]
    assert stream_candidates(3, 3, fits) == [StreamLayout(1, 3)]  # 2 streams × 1 thread: no
    assert stream_candidates(32, 16, lambda n: n <= 2) == [StreamLayout(1, 16), StreamLayout(2, 16)]


def test_choose_streams_picks_the_fastest_measured() -> None:
    layouts = stream_candidates(32, 16, lambda _n: True)
    rates = {"1×16": 4.0, "2×16": 6.5, "4×8": 9.0, "8×4": 8.0}
    measured: list[str] = []

    def measure(layout: StreamLayout) -> float:
        measured.append(layout.label)
        return rates[layout.label]

    choice = choose_streams(layouts, measure)
    assert measured == ["1×16", "2×16", "4×8", "8×4"]
    assert choice.layout == StreamLayout(4, 8)
    assert choice.note == (
        "Indexing uses 4 parallel streams × 8 threads, fastest of streams×threads "
        "1×16: 4.0, 2×16: 6.5, 4×8: 9.0, 8×4: 8.0 chunks/s."
    )


def test_choose_streams_needs_a_clear_win_for_replicas() -> None:
    layouts = [StreamLayout(1, 8), StreamLayout(2, 4)]
    choice = choose_streams(layouts, lambda lay: 10.0 if lay.streams == 1 else 10.5)
    assert MIN_STREAM_SPEEDUP * 10.0 > 10.5
    assert choice.layout == StreamLayout(1, 8)
    assert choice.note.startswith("Indexing uses 1 stream × 8 threads")


def test_synthetic_code_chunks_look_like_code() -> None:
    chunks = synthetic_code_chunks(6)
    assert len(chunks) == 6 and len(set(chunks)) == 6
    assert chunks == synthetic_code_chunks(6)  # deterministic
    for chunk in chunks:
        assert chunk.startswith("File: src/pkg/")
        assert "def merge_" in chunk and "return merged" in chunk
        # ~700 tokens of code is ~2,600 characters (EmbeddingGemma: ~3.8 characters per token).
        assert 2_200 <= len(chunk) <= 3_000
