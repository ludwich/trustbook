from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.audio import wav_bytes
from app.capabilities.bundled import ModelInfo, ProviderRegistry, RuntimeOptions, TuneResult
from app.capabilities.cpu import CpuInfo
from app.capabilities.streams import StreamLayout, choose_streams, stream_candidates
from app.chunking.dispatch import CHUNKER_VERSION
from app.config import Paths, ensure_layout
from app.jobs import calibration
from app.jobs.calibration import (
    CALIBRATE_KIND,
    CALIBRATION_VERSION,
    SPEECH_SAMPLE,
    CalibrationService,
    pipeline_key,
    synthetic_chunks,
)
from app.jobs.handlers import JobCancelledError, JobContext
from app.jobs.repository import JobRepository
from tests.fakes import (
    BrokenProvider,
    FakeEmbedder,
    FakeReranker,
    FakeTranscriber,
    write_models_lock,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


def _ctx(cancelled: bool = False) -> tuple[JobContext, list[float]]:
    progress: list[float] = []
    ctx = JobContext(
        job_id="j",
        kind=CALIBRATE_KIND,
        space_id=None,
        payload={},
        _progress=progress.append,
        _cancelled=lambda: cancelled,
    )
    return ctx, progress


@pytest.fixture
def repo(global_engine: Any) -> JobRepository:
    return JobRepository(global_engine)


def test_synthetic_chunks() -> None:
    chunks = synthetic_chunks(64)
    assert len(chunks) == 64
    assert len(set(chunks)) > 1
    assert all(len(c.split()) == 350 for c in chunks)


def test_run_with_fakes(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    service = CalibrationService(paths, fake_providers, repo, clock=Clock())
    ctx, progress = _ctx()
    result = service.run(ctx)
    assert result.version == CALIBRATION_VERSION
    assert result.embedding_chunks_per_s == 256.0  # 64 chunks / 0.25 s
    assert result.embedding_chunks_per_s_code == 128.0  # 32 code chunks / 0.25 s
    assert result.rerank_pairs_per_s == 128.0
    assert result.stt_realtime_factor == 0.025  # 0.25 s / 10 s of synthetic audio
    assert result.parse_pages_per_s is None
    assert result.notes == [
        "Speech-to-text speed measured on synthetic audio; it may be too low.",
        "Document reading speed not measured: the document parser is not set up.",
    ]
    assert progress[0] == 0.0 and progress[-1] == 1.0
    saved = json.loads(paths.calibration_file.read_text())
    assert saved["cpu_count"] >= 1
    assert service.read() == result


def test_stt_uses_baked_speech_sample(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    sample = paths.models_dir / "stt" / SPEECH_SAMPLE
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(wav_bytes(5.0))
    result = CalibrationService(paths, fake_providers, repo, clock=Clock()).run(_ctx()[0])
    assert result.stt_realtime_factor == 0.05  # 0.25 s / 5 s of real speech
    assert result.notes == [
        "Document reading speed not measured: the document parser is not set up."
    ]


def test_old_calibration_is_redone(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    service = CalibrationService(paths, fake_providers, repo, clock=Clock())
    current = service.run(_ctx()[0])
    assert service.ensure_first_run() is None
    paths.calibration_file.write_text(json.dumps({**current.model_dump(), "version": 1}))
    assert service.ensure_first_run() is not None


def test_chunker_or_embedding_change_redoes_calibration(
    paths: Paths,
    fake_providers: ProviderRegistry,
    repo: JobRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item C (D-057): the saved rates name the chunker and embedding model they were measured
    with; changing either re-runs calibration without anyone bumping CALIBRATION_VERSION."""
    ensure_layout(paths)
    write_models_lock(paths.models_dir)  # embeddings at revision "rev-embeddings"
    service = CalibrationService(paths, fake_providers, repo, clock=Clock())
    current = service.run(_ctx()[0])
    assert current.pipeline == pipeline_key("google/embeddinggemma-2", "rev-embeddings")
    assert f"chunker-{CHUNKER_VERSION}" in current.pipeline
    assert f"calibration-{CALIBRATION_VERSION}" in current.pipeline
    assert not service.outdated(current)
    assert service.ensure_first_run() is None

    monkeypatch.setattr(calibration, "CHUNKER_VERSION", "next-chunker")
    assert service.outdated(current)
    monkeypatch.undo()
    assert not service.outdated(current)

    lock = paths.models_dir / "models.lock.json"
    original = lock.read_text("utf-8")
    lock.write_text(original.replace("rev-embeddings", "rev-embeddings-2"))
    assert service.outdated(current)
    lock.write_text(original)

    # Saved before v6: no pipeline recorded, so it is outdated even at the current version.
    legacy = current.model_dump()
    legacy.pop("pipeline")
    paths.calibration_file.write_text(json.dumps(legacy))
    saved = service.read()
    assert saved is not None and saved.pipeline is None and service.outdated(saved)
    job = service.ensure_first_run()
    assert job is not None and job.kind == CALIBRATE_KIND


def test_run_without_models(paths: Paths, repo: JobRepository) -> None:
    ensure_layout(paths)
    providers = ProviderRegistry(paths.models_dir, factories={})
    providers.override(reranker=BrokenProvider())  # type: ignore[arg-type]
    result = CalibrationService(paths, providers, repo).run(_ctx()[0])
    assert result.embedding_chunks_per_s is None
    assert result.rerank_pairs_per_s is None
    assert result.stt_realtime_factor is None
    assert result.notes == [
        "Embedding speed not measured: model files are missing from this install.",
        "Reranking speed not measured: it failed (RuntimeError).",
        "Speech-to-text speed not measured: model files are missing from this install.",
        "Document reading speed not measured: the document parser is not set up.",
    ]


def test_run_cancelled(paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository) -> None:
    ensure_layout(paths)
    with pytest.raises(JobCancelledError):
        CalibrationService(paths, fake_providers, repo).run(_ctx(cancelled=True)[0])
    assert not paths.calibration_file.exists()


def test_state_and_first_run(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    service = CalibrationService(paths, fake_providers, repo)
    assert service.state().status == "pending"
    job = service.ensure_first_run()
    assert job is not None and job.kind == CALIBRATE_KIND
    assert service.enqueue().id == job.id  # no duplicate while one is waiting
    repo.claim_next(None)
    assert service.state().status == "running"
    repo.finish(job.id, "failed", "x")
    assert service.state().status == "failed"
    service.handler(_ctx()[0])
    state = service.state()
    assert state.status == "done" and state.results is not None
    assert service.ensure_first_run() is None


def test_unreadable_file(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    paths.calibration_file.write_text("{}")
    assert CalibrationService(paths, fake_providers, repo).read() is None


def test_tuning_result_is_saved_and_applied(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    seen: list[tuple[CpuInfo, int]] = []

    def tuner(_registry: ProviderRegistry, cpu: CpuInfo, texts: Sequence[str]) -> TuneResult:
        seen.append((cpu, len(texts)))
        return TuneResult(
            RuntimeOptions(torch_threads=6, embedding_dtype="bfloat16"),
            ["Uses 6 model threads (fastest of 4, 6, 8)."],
        )

    providers = ProviderRegistry(paths.models_dir, factories={}, tuner=tuner)
    providers.override(
        embedder=FakeEmbedder(), reranker=FakeReranker(), transcriber=FakeTranscriber()
    )
    result = CalibrationService(paths, providers, repo, clock=Clock()).run(_ctx()[0])
    assert seen and seen[0][1] == 12
    assert result.runtime == RuntimeOptions(torch_threads=6, embedding_dtype="bfloat16")
    assert providers.options == result.runtime
    assert result.cpu is not None and result.cpu.usable >= 1
    assert result.notes[0] == "Uses 6 model threads (fastest of 4, 6, 8)."
    saved = json.loads(paths.calibration_file.read_text())
    assert saved["runtime"]["embedding_dtype"] == "bfloat16"


def test_tuning_failure_falls_back_to_defaults(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)

    def tuner(_registry: ProviderRegistry, _cpu: CpuInfo, _texts: Sequence[str]) -> TuneResult:
        raise RuntimeError("boom")

    providers = ProviderRegistry(paths.models_dir, factories={}, tuner=tuner)
    providers.override(
        embedder=FakeEmbedder(), reranker=FakeReranker(), transcriber=FakeTranscriber()
    )
    result = CalibrationService(paths, providers, repo, clock=Clock()).run(_ctx()[0])
    assert result.runtime is not None and result.runtime.embedding_dtype == "float32"
    assert result.notes[0] == "Model settings not tuned: it failed (RuntimeError)."


def test_parse_speed_uses_the_document_parser(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    service = CalibrationService(paths, fake_providers, repo, clock=Clock())
    parsed: list[int] = []

    def parse(path: Path) -> None:
        from pypdf import PdfReader

        parsed.append(len(PdfReader(path).pages))

    service.parse_pdf = parse
    service.parser_name = lambda: "the basic PDF reader"
    result = service.run(_ctx()[0])
    assert parsed == [1, 6]  # warm-up page, then the timed document
    assert result.parse_pages_per_s == 24.0  # 6 pages / 0.25 s
    assert "Document reading speed measured with the basic PDF reader." in result.notes


def test_calibration_picks_streams_and_measures_code_with_them(
    paths: Paths, repo: JobRepository
) -> None:
    """The tuner measures stream layouts; the code speed is then measured the way indexing runs."""
    ensure_layout(paths)
    write_models_lock(paths.models_dir)
    measured: list[str] = []
    rates = {"1×8": 3.3, "2×4": 3.9, "4×2": 4.4}

    def tuner(_registry: ProviderRegistry, cpu: CpuInfo, _texts: Sequence[str]) -> TuneResult:
        layouts = stream_candidates(8, 8, lambda _n: True)

        def measure(layout: StreamLayout) -> float:
            measured.append(layout.label)
            return rates[layout.label]

        choice = choose_streams(layouts, measure)
        options = RuntimeOptions(
            torch_threads=8,
            embedding_streams=choice.layout.streams,
            stream_threads=choice.layout.threads,
        )
        return TuneResult(options, [choice.note])

    threads: dict[int, set[str]] = {}

    class Replica(FakeEmbedder):
        def __init__(self, number: int, options: RuntimeOptions) -> None:
            super().__init__()
            self.number = number
            self.threads_option = options.torch_threads

        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            threads.setdefault(self.number, set()).add(threading.current_thread().name)
            time.sleep(0.002)
            return super().embed_documents(texts)

    loads: list[Replica] = []

    def factory(_info: ModelInfo, options: RuntimeOptions) -> Replica:
        loads.append(Replica(len(loads), options))
        return loads[-1]

    providers = ProviderRegistry(
        paths.models_dir,
        factories={"embeddings": factory},
        tuner=tuner,
        memory=lambda: 64 * 1024**3,
    )
    providers.override(reranker=FakeReranker(), transcriber=FakeTranscriber())
    result = CalibrationService(paths, providers, repo, clock=Clock()).run(_ctx()[0])

    assert measured == ["1×8", "2×4", "4×2"]
    assert result.runtime is not None
    assert (result.runtime.embedding_streams, result.runtime.stream_threads) == (4, 2)
    assert result.notes[0].startswith("Indexing uses 4 parallel streams × 2 threads")
    assert result.embedding_chunks_per_s_code == 128.0  # 32 chunks / 0.25 s (fake clock)
    # The shared embedder (prose speed) plus up to 4 replicas with 2 threads each, one thread
    # apiece. Streams load their replica lazily and pull batches from a shared list, so a stream
    # that gets no batch never loads one: assert the invariants, not an exact count (it raced).
    assert loads[0].threads_option == 8
    replicas = loads[1:]
    assert 1 <= len(replicas) <= 4
    assert all(r.threads_option == 2 for r in replicas)
    stream_threads = {name for number, names in threads.items() if number > 0 for name in names}
    assert stream_threads and stream_threads <= {f"embed-stream-{i}" for i in range(4)}
    assert all(len(names) == 1 for number, names in threads.items() if number > 0)
    saved = json.loads(paths.calibration_file.read_text())
    assert saved["runtime"]["embedding_streams"] == 4
    assert saved["embedding_chunks_per_s_code"] == 128.0


def test_version_4_calibration_still_reads(
    paths: Paths, fake_providers: ProviderRegistry, repo: JobRepository
) -> None:
    ensure_layout(paths)
    service = CalibrationService(paths, fake_providers, repo, clock=Clock())
    current = service.run(_ctx()[0]).model_dump()
    current.pop("embedding_chunks_per_s_code")
    current["version"] = 4
    current["runtime"] = {"torch_threads": 7, "embedding_dtype": "bfloat16"}
    paths.calibration_file.write_text(json.dumps(current))
    old = service.read()
    assert old is not None and old.embedding_chunks_per_s_code is None
    assert old.runtime is not None and old.runtime.embedding_streams == 1
    assert service.ensure_first_run() is not None  # recalibrated for streams
