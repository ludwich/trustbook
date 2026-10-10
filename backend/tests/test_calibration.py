from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.audio import wav_bytes
from app.capabilities.bundled import ProviderRegistry, RuntimeOptions, TuneResult
from app.capabilities.cpu import CpuInfo
from app.config import Paths, ensure_layout
from app.jobs.calibration import (
    CALIBRATE_KIND,
    CALIBRATION_VERSION,
    SPEECH_SAMPLE,
    CalibrationService,
    synthetic_chunks,
)
from app.jobs.handlers import JobCancelledError, JobContext
from app.jobs.repository import JobRepository
from tests.fakes import BrokenProvider, FakeEmbedder, FakeReranker, FakeTranscriber


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
