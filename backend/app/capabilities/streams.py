"""Parallel embedding streams for indexing (Gate 1 finding G2).

One encode call keeps only about 4–5 cores busy however many threads it gets (intra-op scaling
plateaus), so indexing runs several calls at once: ``streams`` workers, each with its own model
replica and thread count, pull batches from a shared list. Results go through a bounded queue
to the caller's thread (the index writer), so writing overlaps embedding and vectors never pile
up. Calibration picks the stream layout by measurement (:func:`choose_streams`).

No ML imports: the real replicas come from :class:`app.capabilities.bundled.ProviderRegistry`.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from app.capabilities.bundled import MAX_EMBEDDING_STREAMS, Embedder

EMBED_BATCH = 8  # texts per model call (one forward pass); batches are formed by length
# Multiple streams cost a replica each, so they must clearly beat one stream.
MIN_STREAM_SPEEDUP = 1.1
_POLL_S = 0.2


@dataclass(frozen=True)
class EmbeddedBatch:
    """``vectors[k]`` belongs to ``texts[indices[k]]``; made by stream ``stream``."""

    indices: list[int]
    vectors: list[list[float]]
    stream: int


def length_batches(texts: Sequence[str], batch_size: int) -> list[list[int]]:
    """Text indices grouped into batches of similar length, longest first.

    Similar lengths waste little padding inside a model call, and longest-first keeps the last
    batches short so no stream is left finishing a long one alone.
    """
    order = sorted(range(len(texts)), key=lambda i: (-len(texts[i]), i))
    size = max(1, batch_size)
    return [order[i : i + size] for i in range(0, len(order), size)]


class _Done:
    """Sentinel: one stream has no more work."""


_DONE = _Done()


def embed_in_streams(
    texts: Sequence[str],
    *,
    embedder_for: Callable[[int], Embedder],
    streams: int,
    on_batch: Callable[[EmbeddedBatch], None],
    batch_size: int = EMBED_BATCH,
    guard: Callable[[], AbstractContextManager[object]] = nullcontext,
    check_cancelled: Callable[[], None] = lambda: None,
    queue_size: int | None = None,
) -> int:
    """Embed ``texts`` as documents with up to ``streams`` concurrent workers.

    ``embedder_for(stream)`` is asked before every batch, so new settings (recalibration) are
    picked up between batches. ``guard`` wraps each model call (the shared model-use lock).
    ``on_batch`` runs in the caller's thread; batches are formed by text length
    (:func:`length_batches`), may arrive in any order and carry their text indices. The result
    queue holds at most ``queue_size`` batches (default 2 per stream): when the caller is slow,
    the streams wait. Cancellation (``check_cancelled`` raises) and
    errors stop the streams after their current batch and are re-raised here. Returns the
    number of streams used.
    """
    batches = length_batches(texts, batch_size)
    if not batches:
        return 0
    used = max(1, min(streams, len(batches)))
    results: queue.Queue[EmbeddedBatch | BaseException | _Done] = queue.Queue(
        maxsize=queue_size or 2 * used
    )
    stop = threading.Event()
    claim_lock = threading.Lock()
    claimed = iter(batches)

    def put(item: EmbeddedBatch | BaseException | _Done) -> None:
        while not stop.is_set():
            try:
                results.put(item, timeout=_POLL_S)
                return
            except queue.Full:
                continue

    def worker(stream: int) -> None:
        try:
            while not stop.is_set():
                with claim_lock:
                    indices = next(claimed, None)
                if indices is None:
                    break
                batch = [texts[i] for i in indices]
                with guard():
                    vectors = embedder_for(stream).embed_documents(batch)
                if len(vectors) != len(batch):
                    raise RuntimeError("The embedding model returned the wrong number of vectors.")
                put(EmbeddedBatch(indices, vectors, stream))
        except BaseException as exc:  # handed to the caller's thread
            put(exc)
        finally:
            put(_DONE)

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"embed-stream-{i}", daemon=True)
        for i in range(used)
    ]
    for thread in threads:
        thread.start()
    finished = 0
    try:
        while finished < used:
            check_cancelled()
            try:
                item = results.get(timeout=_POLL_S)
            except queue.Empty:
                continue
            if isinstance(item, _Done):
                finished += 1
            elif isinstance(item, BaseException):
                raise item
            else:
                on_batch(item)
    finally:
        stop.set()
        for thread in threads:
            thread.join()
    return used


# --- choosing the layout (calibration) --------------------------------------------------------


@dataclass(frozen=True)
class StreamLayout:
    streams: int
    threads: int

    @property
    def label(self) -> str:
        return f"{self.streams}×{self.threads}"


def stream_candidates(
    usable: int, single_threads: int, fits: Callable[[int], bool]
) -> list[StreamLayout]:
    """One stream at the tuned thread count, then 2, 4 and 8 streams sharing the usable CPUs.

    A multi-stream layout needs at least 2 threads per stream and must fit in memory (``fits``).
    """
    layouts = [StreamLayout(1, max(1, single_threads))]
    for streams in (2, 4, 8):
        threads = usable // streams
        if streams <= MAX_EMBEDDING_STREAMS and threads >= 2 and fits(streams):
            layouts.append(StreamLayout(streams, threads))
    return layouts


@dataclass(frozen=True)
class StreamChoice:
    layout: StreamLayout
    rates: dict[StreamLayout, float]  # chunks per second, in measuring order
    note: str


def choose_streams(
    layouts: Sequence[StreamLayout], measure: Callable[[StreamLayout], float]
) -> StreamChoice:
    """Measure each layout (chunks/s) and keep the fastest.

    Several streams cost a model replica each, so they are kept only when at least
    :data:`MIN_STREAM_SPEEDUP` times faster than one stream.
    """
    rates = {layout: measure(layout) for layout in layouts}
    single = layouts[0]
    best = max(rates, key=lambda layout: rates[layout])
    if best.streams > 1 and rates[best] < MIN_STREAM_SPEEDUP * rates[single]:
        best = single
    tried = ", ".join(f"{layout.label}: {rate:.1f}" for layout, rate in rates.items())
    if best.streams == 1:
        note = f"Indexing uses 1 stream × {best.threads} threads (chunks/s {tried})."
    else:
        note = (
            f"Indexing uses {best.streams} parallel streams × {best.threads} threads, "
            f"fastest of streams×threads {tried} chunks/s."
        )
    return StreamChoice(best, rates, note)


# --- a realistic code workload ----------------------------------------------------------------

_NAMES = (
    "session request response adapter headers cookies params timeout proxies verify cert "
    "stream encoding history url method body hooks auth retries pool connection chunk"
).split()


def synthetic_code_chunks(count: int, lines_per_chunk: int = 50) -> list[str]:
    """Deterministic Python-like code chunks of about 700 tokens each (a typical code chunk)."""
    chunks: list[str] = []
    n = len(_NAMES)
    for i in range(count):
        header = [f"File: src/pkg/module_{i}.py", f"Symbol: {_NAMES[i % n].title()}Manager", ""]
        lines: list[str] = []
        for f in range(lines_per_chunk // 15 + 1):
            a, b, c = (_NAMES[(i * 5 + f * 3 + k) % n] for k in (0, 1, 2))
            lines += [
                f"    def merge_{a}_{b}(self, {a}, {b}=None, *, {c}_timeout: float = {f + 1}.5):",
                f'        """Merge the {a} with the {b}, honouring the {c} settings."""',
                f"        if {b} is None:",
                f"            return self.{a}.copy()",
                f"        merged = dict(self.{a} or {{}})",
                f"        for key, value in {b}.items():",
                f"            if value is not None and key not in self._{c}:",
                "                merged[key.lower()] = value",
                "            elif isinstance(value, (list, tuple)):",
                '                merged[key] = ", ".join(str(v) for v in value)',
                f"        if len(merged) > {(f + 2) * 8}:",
                f'            raise ValueError(f"Too many {a} entries: {{len(merged)}}")',
                f"        self._log.debug('merged %d {a} items for %s', len(merged), {c}_timeout)",
                "        return merged",
                "",
            ]
        chunks.append("\n".join(header + lines[:lines_per_chunk]))
    return chunks
