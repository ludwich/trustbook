"""Embedding replicas for indexing streams and the memory guard (Gate 1 G2)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.capabilities.bundled import (
    EMBEDDER_BYTES,
    MEMORY_RESERVE_BYTES,
    ModelInfo,
    ProviderRegistry,
    ProviderUnavailableError,
    RuntimeOptions,
    streams_that_fit,
)
from tests.fakes import FakeEmbedder, write_models_lock

GB = 1024**3


class Replica(FakeEmbedder):
    def __init__(self, number: int, options: RuntimeOptions) -> None:
        super().__init__()
        self.number = number
        self.options = options


def _registry(
    tmp_path: Path, memory: int | None = 64 * GB, **options: object
) -> tuple[ProviderRegistry, list[Replica]]:
    write_models_lock(tmp_path)
    loads: list[Replica] = []

    def factory(_info: ModelInfo, opts: RuntimeOptions) -> Replica:
        replica = Replica(len(loads), opts)
        loads.append(replica)
        return replica

    registry = ProviderRegistry(tmp_path, factories={"embeddings": factory}, memory=lambda: memory)
    registry.configure(RuntimeOptions(**options))  # type: ignore[arg-type]
    return registry, loads


def test_streams_that_fit() -> None:
    bf16 = EMBEDDER_BYTES["bfloat16"]
    assert streams_that_fit(4, "bfloat16", None) == 4  # unknown memory: trust calibration
    assert streams_that_fit(1, "float32", 1) == 1  # one stream needs no replica
    assert streams_that_fit(4, "bfloat16", MEMORY_RESERVE_BYTES + 4 * bf16) == 4
    assert streams_that_fit(4, "bfloat16", MEMORY_RESERVE_BYTES + 2 * bf16 + 1) == 2
    assert streams_that_fit(4, "float32", MEMORY_RESERVE_BYTES + 2 * bf16 + 1) == 1
    assert streams_that_fit(8, "bfloat16", 2 * GB) == 1  # below the reserve


def test_memory_guard_reduces_streams(tmp_path: Path) -> None:
    memory = MEMORY_RESERVE_BYTES + 2 * EMBEDDER_BYTES["float32"]
    registry, _ = _registry(tmp_path, memory, embedding_streams=8, stream_threads=2)
    assert registry.ingest_streams() == 2
    registry.configure(
        RuntimeOptions(embedding_streams=8, stream_threads=2, embedding_dtype="bfloat16")
    )
    assert registry.ingest_streams() == 4  # BF16 replicas are half the size


def test_replicas_are_separate_from_the_chat_embedder(tmp_path: Path) -> None:
    registry, loads = _registry(tmp_path, torch_threads=8, embedding_streams=3, stream_threads=2)
    shared = registry.embedder()
    replicas = [registry.ingest_embedder(i) for i in range(3)]
    assert len({id(r) for r in replicas}) == 3
    assert shared not in replicas
    assert [r.options.torch_threads for r in loads] == [8, 2, 2, 2]
    # Cached per slot; a stream number beyond the count wraps around.
    assert registry.ingest_embedder(1) is replicas[1]
    assert registry.ingest_embedder(4) is replicas[1]
    assert len(loads) == 4


def test_one_stream_uses_the_shared_embedder(tmp_path: Path) -> None:
    registry, loads = _registry(tmp_path, torch_threads=8)
    assert registry.ingest_streams() == 1
    assert registry.ingest_embedder(0) is registry.embedder()
    assert registry.ingest_embedder(5) is registry.embedder()
    assert len(loads) == 1
    assert registry.stream_threads() == 8


def test_injected_embedder_means_one_stream(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path, embedding_streams=4, stream_threads=2)
    fake = FakeEmbedder()
    registry.override(embedder=fake)
    assert registry.ingest_streams() == 1
    assert registry.ingest_embedder(3) is fake


def test_configure_drops_replicas(tmp_path: Path) -> None:
    registry, loads = _registry(tmp_path, embedding_streams=2, stream_threads=2)
    first = registry.ingest_embedder(0)
    registry.configure(RuntimeOptions(embedding_streams=2, stream_threads=3))
    second = registry.ingest_embedder(0)
    assert first is not second
    assert loads[-1].options.torch_threads == 3


def test_replicas_load_once_under_concurrency(tmp_path: Path) -> None:
    registry, loads = _registry(tmp_path, embedding_streams=2, stream_threads=2)
    barrier = threading.Barrier(6)
    got: list[object] = []

    def grab(stream: int) -> None:
        barrier.wait()
        got.append(registry.ingest_embedder(stream))

    threads = [threading.Thread(target=grab, args=(i % 2,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 2 and len({id(g) for g in got}) == 2


def test_missing_model_files_are_unavailable_for_replicas(tmp_path: Path) -> None:
    write_models_lock(tmp_path, present=False)

    def factory(_info: ModelInfo, _opts: RuntimeOptions) -> FakeEmbedder:
        return FakeEmbedder()

    registry = ProviderRegistry(tmp_path, factories={"embeddings": factory}, memory=lambda: None)
    registry.configure(RuntimeOptions(embedding_streams=2, stream_threads=2))
    with pytest.raises(ProviderUnavailableError):
        registry.ingest_embedder(1)


def test_runtime_options_validate_streams() -> None:
    assert RuntimeOptions().embedding_streams == 1
    assert RuntimeOptions.model_validate({"torch_threads": 4}).stream_threads is None  # v4 file
    with pytest.raises(ValueError):
        RuntimeOptions(embedding_streams=0)
    with pytest.raises(ValueError):
        RuntimeOptions(embedding_streams=99)
