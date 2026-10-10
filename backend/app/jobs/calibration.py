"""First-start calibration: measure this computer's speed for later estimates (SPEC §11)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.capabilities.audio import wav_bytes
from app.capabilities.bundled import ProviderRegistry, ProviderUnavailableError
from app.config import Paths
from app.db.models import Job
from app.fsutil import atomic_write_json
from app.jobs.handlers import JobContext
from app.jobs.repository import JobRepository
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.calibration")

CALIBRATION_VERSION = 1
CALIBRATE_KIND = "calibrate"
CALIBRATE_TITLE = "Measure this computer's speed"
EMBED_CHUNKS = 64
RERANK_PAIRS = 32
STT_SECONDS = 10.0
PARSE_NOTE = "Document parser arrives in Phase 1."

_WORDS: tuple[str, ...] = tuple(
    "audit contract customer delivery evidence finding invoice ledger meeting migration "
    "policy process project quarter report requirement review risk scope service supplier "
    "system team test timeline update vendor workshop analysis budget control decision".split()
)

CalibrationStatus = Literal["pending", "running", "done", "failed"]


class Calibration(BaseModel):
    version: int
    measured_at: str
    cpu_count: int
    embedding_chunks_per_s: float | None
    rerank_pairs_per_s: float | None
    parse_pages_per_s: float | None
    stt_realtime_factor: float | None
    notes: list[str]


class CalibrationState(BaseModel):
    status: CalibrationStatus
    results: Calibration | None


def synthetic_chunks(count: int, words_per_chunk: int = 350) -> list[str]:
    """Deterministic, roughly chunk-sized texts (~500 tokens each)."""
    chunks: list[str] = []
    n = len(_WORDS)
    for i in range(count):
        words = [_WORDS[(i * 7 + j * 3 + j // n) % n] for j in range(words_per_chunk)]
        chunks.append(" ".join(words).capitalize() + ".")
    return chunks


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
        if latest is not None and latest.status == "running":
            return CalibrationState(status="running", results=results)
        if results is not None:
            return CalibrationState(status="done", results=results)
        if latest is not None and latest.status == "failed":
            return CalibrationState(status="failed", results=None)
        return CalibrationState(status="pending", results=None)

    def enqueue(self) -> Job:
        """Queue a calibration run (or return the one already waiting/running)."""
        existing = self._jobs.find_unfinished(CALIBRATE_KIND)
        if existing is not None:
            return existing
        return self._jobs.enqueue(CALIBRATE_KIND, CALIBRATE_TITLE)

    def ensure_first_run(self) -> Job | None:
        """On first start (no calibration.json), queue a calibration run."""
        if self._paths.calibration_file.exists():
            return None
        return self.enqueue()

    def run(self, ctx: JobContext) -> Calibration:
        """Job handler: measure each part, guarded, and save the results."""
        notes: list[str] = []
        ctx.progress(0.0)
        embed = self._guard("Embedding speed", self._measure_embeddings, notes)
        ctx.check_cancelled()
        ctx.progress(0.4)
        rerank = self._guard("Reranking speed", self._measure_rerank, notes)
        ctx.check_cancelled()
        ctx.progress(0.6)
        stt = self._guard("Speech-to-text speed", self._measure_stt, notes)
        ctx.check_cancelled()
        notes.append(PARSE_NOTE)
        result = Calibration(
            version=CALIBRATION_VERSION,
            measured_at=utc_now_iso(),
            cpu_count=os.cpu_count() or 1,
            embedding_chunks_per_s=embed,
            rerank_pairs_per_s=rerank,
            parse_pages_per_s=None,
            stt_realtime_factor=stt,
            notes=notes,
        )
        atomic_write_json(self._paths.calibration_file, result.model_dump())
        ctx.progress(1.0)
        return result

    def handler(self, ctx: JobContext) -> None:
        self.run(ctx)

    # --- measurements ----------------------------------------------------------------------

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

    def _measure_rerank(self) -> float:
        reranker = self._providers.reranker()
        reranker.score("warm up", ["warm up"])
        docs = synthetic_chunks(RERANK_PAIRS)
        query = "Which supplier risks were found in the audit?"
        return RERANK_PAIRS / self._timed(lambda: reranker.score(query, docs))

    def _measure_stt(self) -> float:
        transcriber = self._providers.transcriber()
        with tempfile.TemporaryDirectory(prefix="trustbook-calibrate-") as folder:
            path = Path(folder) / "sample.wav"
            path.write_bytes(wav_bytes(1.0))
            transcriber.transcribe(path)  # warm up
            path.write_bytes(wav_bytes(STT_SECONDS, noise=True))
            elapsed = self._timed(lambda: transcriber.transcribe(path))
        return elapsed / STT_SECONDS
