"""First-start calibration: measure this computer's speed for later estimates (SPEC §11)."""

from __future__ import annotations

import json
import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.capabilities.audio import wav_bytes
from app.capabilities.bundled import (
    ProviderRegistry,
    ProviderUnavailableError,
    RuntimeOptions,
    TuneResult,
    default_threads,
)
from app.capabilities.cpu import CpuInfo, cpu_info
from app.capabilities.streams import EMBED_BATCH, embed_in_streams, synthetic_code_chunks
from app.chunking.dispatch import CHUNKER_VERSION
from app.config import Paths
from app.db.models import Job
from app.fsutil import atomic_write_json
from app.jobs.handlers import JobContext
from app.jobs.repository import JobRepository
from app.parse.sample_pdf import sample_pdf
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.calibration")

# 2: real speech STT, usable CPUs; 3: tuned threads + BF16; 4: parsing; 5: indexing streams, code;
# 6: Phase 3 chunks, and the saved results name the pipeline they were measured with (D-057);
# 7: documents and web pages split at every heading (chunker p5-headings-7, D-104, D-107)
CALIBRATION_VERSION = 7
CALIBRATE_KIND = "calibrate"
CALIBRATE_TITLE = "Measure this computer's speed"
# The queue's title for a run that replaces results measured for an older pipeline (D-107).
RECALIBRATE_TITLE = "Measure this computer's speed again (indexing has changed)"
EMBED_CHUNKS = 64
EMBED_CODE_CHUNKS = 32
SPEECH_SAMPLE = "calibration.wav"  # public-domain speech, see docker/download_models.py
RERANK_PAIRS = 32
TUNE_CHUNKS = 12
STT_SECONDS = 10.0
PARSE_PAGES = 6

_WORDS: tuple[str, ...] = tuple(
    "audit contract customer delivery evidence finding invoice ledger meeting migration "
    "policy process project quarter report requirement review risk scope service supplier "
    "system team test timeline update vendor workshop analysis budget control decision".split()
)

CalibrationStatus = Literal["pending", "running", "done", "failed"]


class Calibration(BaseModel):
    version: int
    # What the rates depend on besides this computer: calibration, chunker and embedding model
    # (``pipeline_key``). Any change re-runs calibration on the next start. Absent before v6.
    pipeline: str | None = None
    measured_at: str
    cpu_count: int
    embedding_chunks_per_s: float | None  # ~350-token prose, one model call at a time
    # ~700-token code chunks, indexed the way ingestion does it (parallel streams); version 5+
    embedding_chunks_per_s_code: float | None = None
    rerank_pairs_per_s: float | None
    parse_pages_per_s: float | None
    stt_realtime_factor: float | None
    notes: list[str]
    cpu: CpuInfo | None = None
    runtime: RuntimeOptions | None = None


class CalibrationState(BaseModel):
    status: CalibrationStatus
    results: Calibration | None
    # The saved results were measured with older calibration code, another chunker or another
    # embedding model; a new run is queued at start (D-057, D-107).
    outdated: bool = False


def synthetic_chunks(count: int, words_per_chunk: int = 350) -> list[str]:
    """Deterministic, roughly chunk-sized texts (~500 tokens each)."""
    chunks: list[str] = []
    n = len(_WORDS)
    for i in range(count):
        words = [_WORDS[(i * 7 + j * 3 + j // n) % n] for j in range(words_per_chunk)]
        chunks.append(" ".join(words).capitalize() + ".")
    return chunks


def pipeline_key(embedding_model: str, embedding_revision: str) -> str:
    """The calibration version, ``CHUNKER_VERSION`` and the embedding model at its revision.

    Indexing speed is chunks per second through the embedding model, so a new chunker (other
    chunk sizes) or another embedding model makes saved rates stale, whether or not anyone
    remembered to bump :data:`CALIBRATION_VERSION`.
    """
    return (
        f"calibration-{CALIBRATION_VERSION}|chunker-{CHUNKER_VERSION}"
        f"|embeddings-{embedding_model}@{embedding_revision}"
    )


def _reason(exc: BaseException) -> str:
    text = str(exc) if isinstance(exc, ProviderUnavailableError) else ""
    text = text.strip().rstrip(".") or f"it failed ({type(exc).__name__})"
    return text[:1].lower() + text[1:]


class CalibrationService:
    """Runs calibration and reads/writes ``config/calibration.json``."""

    def __init__(
        self,
        paths: Paths,
        providers: ProviderRegistry,
        jobs: JobRepository,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._paths = paths
        self._providers = providers
        self._jobs = jobs
        self._clock = clock
        # Set once the ingestion tools exist: parses a PDF the way uploads are parsed.
        self.parse_pdf: Callable[[Path], Any] | None = None
        self.parser_name: Callable[[], str] = lambda: "Docling"

    def read(self) -> Calibration | None:
        path = self._paths.calibration_file
        if not path.is_file():
            return None
        try:
            return Calibration.model_validate(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError, ValidationError):
            logger.warning("calibration.json is unreadable")
            return None

    def state(self) -> CalibrationState:
        latest = self._jobs.latest(CALIBRATE_KIND)
        results = self.read()
        outdated = results is not None and self.outdated(results)
        if latest is not None and latest.status == "running":
            return CalibrationState(status="running", results=results, outdated=outdated)
        if results is not None:
            return CalibrationState(status="done", results=results, outdated=outdated)
        if latest is not None and latest.status == "failed":
            return CalibrationState(status="failed", results=None)
        return CalibrationState(status="pending", results=None)

    def enqueue(self, title: str = CALIBRATE_TITLE) -> Job:
        """Queue a calibration run (or return the one already waiting/running)."""
        existing = self._jobs.find_unfinished(CALIBRATE_KIND)
        if existing is not None:
            return existing
        return self._jobs.enqueue(CALIBRATE_KIND, title)

    def pipeline(self) -> str:
        """The :func:`pipeline_key` of this installation."""
        info = self._providers.info("embeddings")
        return pipeline_key(info.model_id, info.revision)

    def outdated(self, saved: Calibration) -> bool:
        """Measured by older calibration code, or for another chunker or embedding model."""
        return saved.version < CALIBRATION_VERSION or saved.pipeline != self.pipeline()

    def ensure_first_run(self) -> Job | None:
        """Queue a calibration run on first start or when the saved one is outdated; the queue
        then says why it runs again (D-107)."""
        saved = self.read()
        if saved is None:
            return self.enqueue()
        if not self.outdated(saved):
            return None
        logger.info("Speed test results are for an older pipeline; measuring again")
        return self.enqueue(RECALIBRATE_TITLE)

    def run(self, ctx: JobContext) -> Calibration:
        """Job handler: measure each part, guarded, and save the results."""
        notes: list[str] = []
        ctx.progress(0.0)
        cpu = cpu_info()
        runtime = self._tune(cpu, notes)
        ctx.check_cancelled()
        ctx.progress(0.3)
        embed = self._guard("Embedding speed", self._measure_embeddings, notes)
        ctx.check_cancelled()
        ctx.progress(0.35)
        embed_code = (  # one note is enough when the embedding model cannot run at all
            self._guard("Code indexing speed", self._measure_code_embeddings, notes)
            if embed is not None
            else None
        )
        ctx.check_cancelled()
        ctx.progress(0.4)
        rerank = self._guard("Reranking speed", self._measure_rerank, notes)
        ctx.check_cancelled()
        ctx.progress(0.6)
        stt = self._guard("Speech-to-text speed", lambda: self._measure_stt(notes), notes)
        ctx.check_cancelled()
        ctx.progress(0.9)
        parse = self._guard("Document reading speed", lambda: self._measure_parse(notes), notes)
        result = Calibration(
            version=CALIBRATION_VERSION,
            pipeline=self.pipeline(),
            measured_at=utc_now_iso(),
            cpu_count=cpu.usable,
            embedding_chunks_per_s=embed,
            embedding_chunks_per_s_code=embed_code,
            rerank_pairs_per_s=rerank,
            parse_pages_per_s=parse,
            stt_realtime_factor=stt,
            notes=notes,
            cpu=cpu,
            runtime=runtime,
        )
        atomic_write_json(self._paths.calibration_file, result.model_dump())
        ctx.progress(1.0)
        return result

    def handler(self, ctx: JobContext) -> None:
        with self._providers.use.exclusive():  # measure on an otherwise idle CPU
            self.run(ctx)

    # --- measurements ----------------------------------------------------------------------

    def _tune(self, cpu: CpuInfo, notes: list[str]) -> RuntimeOptions:
        """Pick model threads and BF16/FP32 by measurement, and use them from now on."""
        try:
            result = self._providers.tune(cpu, synthetic_chunks(TUNE_CHUNKS))
        except Exception as exc:
            if not isinstance(exc, ProviderUnavailableError):
                logger.warning("Model tuning failed", exc_info=True)
            notes.append(f"Model settings not tuned: {_reason(exc)}.")
            result = TuneResult(RuntimeOptions(torch_threads=default_threads(cpu)), [])
        notes.extend(result.notes)
        self._providers.configure(result.options)
        return result.options

    def _guard(self, label: str, measure: Callable[[], float], notes: list[str]) -> float | None:
        try:
            return round(measure(), 3)
        except Exception as exc:
            if not isinstance(exc, ProviderUnavailableError):
                logger.warning("%s calibration failed", label, exc_info=True)
            notes.append(f"{label} not measured: {_reason(exc)}.")
            return None

    def _timed(self, action: Callable[[], Any]) -> float:
        started = self._clock()
        action()
        return max(self._clock() - started, 1e-6)

    def _measure_embeddings(self) -> float:
        embedder = self._providers.embedder()
        embedder.embed_documents(["warm up"])  # model load is not part of the speed
        chunks = synthetic_chunks(EMBED_CHUNKS)
        return EMBED_CHUNKS / self._timed(lambda: embedder.embed_documents(chunks))

    def _measure_code_embeddings(self) -> float:
        """Code chunks/s through the indexing streams (no model lock: calibration holds it)."""
        providers = self._providers
        streams = providers.ingest_streams()
        chunks = synthetic_code_chunks(max(EMBED_CODE_CHUNKS, streams * EMBED_BATCH))

        def run(texts: list[str], batch_size: int = EMBED_BATCH) -> None:
            embed_in_streams(
                texts,
                embedder_for=providers.ingest_embedder,
                streams=streams,
                on_batch=lambda _batch: None,
                batch_size=batch_size,
            )

        run(chunks[:streams], 1)  # loads and warms every stream's model
        return len(chunks) / self._timed(lambda: run(chunks))

    def _measure_rerank(self) -> float:
        reranker = self._providers.reranker()
        reranker.score("warm up", ["warm up"])
        docs = synthetic_chunks(RERANK_PAIRS)
        query = "Which supplier risks were found in the audit?"
        return RERANK_PAIRS / self._timed(lambda: reranker.score(query, docs))

    def _measure_parse(self, notes: list[str]) -> float:
        if self.parse_pdf is None:
            raise ProviderUnavailableError("The document parser is not set up.")
        parse = self.parse_pdf
        with tempfile.TemporaryDirectory(prefix="trustbook-calibrate-") as folder:
            warm = Path(folder) / "warm.pdf"
            warm.write_bytes(sample_pdf(1))
            parse(warm)  # loading the layout and table models is not part of the speed
            sample = Path(folder) / "sample.pdf"
            sample.write_bytes(sample_pdf(PARSE_PAGES))
            elapsed = self._timed(lambda: parse(sample))
        name = self.parser_name()
        if name != "Docling":
            notes.append(f"Document reading speed measured with {name}.")
        return PARSE_PAGES / elapsed

    def _measure_stt(self, notes: list[str]) -> float:
        transcriber = self._providers.transcriber()
        # Whistle short-circuits on non-speech, so time a real speech clip baked into the image
        # (docker/download_models.py); synthetic audio is only a last resort.
        sample = self._providers.models_dir / "stt" / SPEECH_SAMPLE
        with tempfile.TemporaryDirectory(prefix="trustbook-calibrate-") as folder:
            warm = Path(folder) / "warm.wav"
            warm.write_bytes(wav_bytes(1.0))
            transcriber.transcribe(warm)  # model load is not part of the speed
            if not sample.is_file():
                notes.append("Speech-to-text speed measured on synthetic audio; it may be too low.")
                sample = Path(folder) / "sample.wav"
                sample.write_bytes(wav_bytes(STT_SECONDS, noise=True))
            durations: list[float] = []
            elapsed = self._timed(
                lambda: durations.append(transcriber.transcribe(sample).duration_s)
            )
        return elapsed / max(durations[0], 1e-6)
