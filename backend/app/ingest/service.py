"""Source lifecycle: create, refresh, delete, and the ``ingest`` job that indexes a source.

Status flow: queued → reading → indexing → ready | failed (SPEC §7.5). The job is space-scoped
(the active space's jobs run first), cancellable and reports progress: reading 0–40 %,
embedding 40–95 %, writing the index to 100 %.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.capabilities.bundled import Embedder, ProviderRegistry, ProviderUnavailableError
from app.chunking.dispatch import chunk_snapshot
from app.chunking.tokens import TokenCounter, load_token_counter
from app.db.models import Chunk, Source
from app.index.base import IndexedChunk, IndexRegistry
from app.index.lance import IndexDimensionError
from app.ingest import errors
from app.ingest.errors import IngestError, fail
from app.ingest.readers import IngestTools, read_source
from app.jobs.handlers import JobCancelledError, JobContext
from app.jobs.pool import WorkerPool
from app.jobs.repository import JobNotFoundError, JobRepository
from app.parse.snapshot import Snapshot, write_snapshot
from app.spaces.service import SpaceService
from app.spaces.source_schemas import SourceOut
from app.spaces.sources import (
    FINAL_STATUSES,
    SourceNotFoundError,
    SourceRepository,
    load_stats,
    new_id,
)
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.ingest")

INGEST_KIND = "ingest"
EMBED_BATCH = 16
REFRESHABLE = frozenset({"url", "youtube", "folder", "search"})
SourceReadyHook = Callable[[str, str], None]


class RefreshNotAllowedError(ValueError):
    """Pasted text and uploads are fixed snapshots. Message is plain English."""


def _title_for_job(title: str) -> str:
    short = title if len(title) <= 60 else title[:57] + "…"
    return f"Add “{short}”"


class IngestService:
    """Creates sources, runs ingest jobs and builds API views of sources."""

    def __init__(
        self,
        *,
        spaces: SpaceService,
        jobs: JobRepository,
        pool: WorkerPool,
        indexes: IndexRegistry,
        providers: ProviderRegistry,
        tools: IngestTools,
        on_ready: list[SourceReadyHook],
    ) -> None:
        self.spaces = spaces
        self.jobs = jobs
        self.pool = pool
        self.indexes = indexes
        self.providers = providers
        self.tools = tools
        self.on_ready = on_ready

    # --- helpers ---------------------------------------------------------------------------

    def repo(self, space_id: str) -> SourceRepository:
        return SourceRepository(self.spaces.get(space_id))

    def token_counter(self) -> TokenCounter:
        info = self.providers.info("embeddings")
        return load_token_counter(info.path / "tokenizer.json")

    def out(self, source: Source) -> SourceOut:
        """API view; a source whose job ended without finishing shows as failed."""
        status = source.status
        reason, step = source.failure_reason, source.failure_next_step
        progress: float | None = None
        if status not in FINAL_STATUSES:
            job = None
            if source.job_id:
                try:
                    job = self.jobs.get(source.job_id)
                except JobNotFoundError:
                    job = None
            if job is None or job.status in ("done", "failed", "cancelled"):
                status = "failed"
                if job is not None and job.status == "cancelled":
                    reason, step = self._cancelled_pair(source)
                else:
                    reason = job.error if job is not None and job.error else errors.INTERNAL[0]
                    step = self._next_step(source, errors.REFRESH)
            elif job.status == "running":
                progress = job.progress
        return SourceOut(
            id=source.id,
            type=source.type,
            title=source.title,
            origin=source.origin,
            group=source.group,
            status=status,
            progress=progress,
            failure_reason=reason if status == "failed" else None,
            failure_next_step=step if status == "failed" else None,
            quality_warning=source.quality_warning,
            enabled=source.enabled,
            snapshot_at=source.snapshot_at,
            commit_hash=source.commit_hash,
            stats=load_stats(source),
            created_at=source.created_at,
            job_id=source.job_id,
        )

    def _cancelled_pair(self, source: Source) -> tuple[str, str]:
        return errors.CANCELLED_REFRESHABLE if source.type in REFRESHABLE else errors.CANCELLED

    def _next_step(self, source: Source, step: str | None) -> str | None:
        if step == errors.REFRESH and source.type not in REFRESHABLE:
            return "Remove this source and add it again."
        return step

    # --- commands --------------------------------------------------------------------------

    def create(
        self,
        space_id: str,
        *,
        type: str,
        title: str,
        origin: str | None,
        group: str,
        enqueue: bool = True,
        prepare: Callable[[Path], None] | None = None,
        snapshot_at: str | None = None,
    ) -> Source:
        """Insert a source; ``prepare`` writes files into its folder before the job is queued."""
        repo = self.repo(space_id)
        now = utc_now_iso()
        source = repo.add(
            Source(
                id=new_id(),
                type=type,
                title=title,
                origin=origin,
                group=group,
                status="queued",
                snapshot_at=snapshot_at,
                created_at=now,
                updated_at=now,
            )
        )
        if prepare is not None:
            folder = repo.folder(source.id)
            folder.mkdir(parents=True, exist_ok=True)
            try:
                prepare(folder)
            except IngestError as exc:
                return self._fail(repo, source, exc)
        return self.enqueue(repo, source) if enqueue else source

    def enqueue(self, repo: SourceRepository, source: Source) -> Source:
        job = self.jobs.enqueue(
            INGEST_KIND,
            _title_for_job(source.title),
            space_id=repo.record.id,
            payload={"source_id": source.id},
        )
        updated = repo.update(
            source.id,
            job_id=job.id,
            status="queued",
            failure_reason=None,
            failure_next_step=None,
        )
        self.pool.notify()
        return updated

    def refresh(self, space_id: str, source_id: str) -> Source:
        repo = self.repo(space_id)
        source = repo.get(source_id)
        if source.type not in REFRESHABLE:
            raise RefreshNotAllowedError(
                "Pasted text and uploaded files are fixed snapshots and can't be refreshed. "
                "Remove the source and add it again instead."
            )
        self._cancel_job(source)
        return self.enqueue(repo, source)

    def delete(self, space_id: str, source_id: str) -> None:
        repo = self.repo(space_id)
        source = repo.get(source_id)
        self._cancel_job(source)
        self.indexes.for_space(space_id).delete_source(source_id)
        repo.delete(source_id)

    def _cancel_job(self, source: Source) -> None:
        if not source.job_id:
            return
        try:
            job = self.jobs.get(source.job_id)
        except JobNotFoundError:
            return
        if job.status in ("queued", "running", "paused"):
            self.jobs.cancel(job.id)
            self.pool.request_cancel(job.id)

    def _fail(self, repo: SourceRepository, source: Source, exc: IngestError) -> Source:
        return repo.update(
            source.id,
            status="failed",
            failure_reason=exc.reason,
            failure_next_step=self._next_step(source, exc.next_step),
        )

    # --- the job ---------------------------------------------------------------------------

    def handler(self, ctx: JobContext) -> None:
        """Job handler for ``ingest`` (payload ``{"source_id": str}``)."""
        record = self.spaces.find(ctx.space_id) if ctx.space_id else None
        if record is None:
            return
        repo = SourceRepository(record)
        source = repo.find(str(ctx.payload.get("source_id") or ""))
        if source is None or source.job_id not in (None, ctx.job_id):
            return  # deleted, or superseded by a newer job
        try:
            self._run(ctx, repo, source)
        except JobCancelledError:
            self._fail_quietly(repo, source, IngestError(*self._cancelled_pair(source)))
            raise
        except IngestError as exc:
            self._fail_quietly(repo, source, exc)
            raise
        except Exception:
            logger.exception("Ingest of source %s failed", source.id)
            self._fail_quietly(repo, source, fail(errors.INTERNAL))
            raise

    def _fail_quietly(self, repo: SourceRepository, source: Source, exc: IngestError) -> None:
        try:
            self._fail(repo, source, exc)
        except SourceNotFoundError:
            pass
        except Exception:
            logger.warning("Could not record the failure of source %s", source.id, exc_info=True)

    def _embedder(self) -> Embedder:
        try:
            return self.providers.embedder()
        except ProviderUnavailableError as exc:
            raise fail(errors.NO_EMBEDDER) from exc

    def _embed(self, ctx: JobContext, texts: Sequence[str]) -> list[list[float]]:
        embedder = self._embedder()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            ctx.check_cancelled()
            batch = list(texts[i : i + EMBED_BATCH])
            vectors.extend(embedder.embed_documents(batch))
            ctx.progress(0.4 + 0.55 * min(1.0, (i + len(batch)) / len(texts)))
        return vectors

    def _run(self, ctx: JobContext, repo: SourceRepository, source: Source) -> None:
        space_id = repo.record.id
        repo.update(source.id, status="reading", failure_reason=None, failure_next_step=None)
        ctx.progress(0.0)
        folder = repo.folder(source.id)
        folder.mkdir(parents=True, exist_ok=True)
        snapshot, snapshot_at = read_source(
            self.tools,
            source,
            folder,
            lambda f: ctx.progress(0.4 * max(0.0, min(1.0, f))),
            ctx.check_cancelled,
        )
        ctx.check_cancelled()
        write_snapshot(folder, snapshot)
        repo.update(source.id, status="indexing")
        drafts = chunk_snapshot(snapshot, self.token_counter())
        if not drafts:
            raise fail(errors.NO_TEXT)
        vectors = self._embed(ctx, [d.text for d in drafts])
        ctx.check_cancelled()

        rows = [
            Chunk(
                id=new_id(),
                source_id=source.id,
                ordinal=i,
                text=d.text,
                start_offset=d.start,
                end_offset=d.end,
                locator=json.dumps(d.locator),
                token_count=d.token_count,
            )
            for i, d in enumerate(drafts)
        ]
        indexed = [IndexedChunk(r.id, source.id, r.text) for r in rows]
        index = self.indexes.for_space(space_id)
        repo.replace_chunks(source.id, rows)
        index.delete_source(source.id)
        try:
            index.add(indexed, vectors)
        except IndexDimensionError as exc:
            raise fail(errors.EMBEDDING_CHANGED) from exc
        self._mark_ready(repo, source, snapshot, snapshot_at, len(rows))
        ctx.progress(1.0)
        self._notify_ready(space_id, source.id)

    def _mark_ready(
        self,
        repo: SourceRepository,
        source: Source,
        snapshot: Snapshot,
        snapshot_at: str,
        chunk_count: int,
    ) -> None:
        stats: dict[str, Any] = {
            **snapshot.stats,
            "chunks": chunk_count,
            "chars": len(snapshot.markdown),
        }
        warnings = list(dict.fromkeys(snapshot.warnings))
        if len(snapshot.markdown.strip()) < errors.SHORT_TEXT_CHARS:
            warnings.append(errors.WARN_SHORT)
        current = repo.get(source.id)
        # Replace a placeholder title (the address itself) with the real one; keep user renames.
        title = current.title
        if snapshot.title and (not title.strip() or title == (source.origin or "")):
            title = snapshot.title[:200]
        values: dict[str, Any] = {
            "status": "ready",
            "stats": stats,
            "quality_warning": " ".join(warnings) or None,
            "snapshot_at": snapshot_at,
            "commit_hash": snapshot.commit_hash,
            "title": title,
            "failure_reason": None,
            "failure_next_step": None,
        }
        if snapshot.group:
            values["group"] = snapshot.group
        repo.update(source.id, **values)

    def _notify_ready(self, space_id: str, source_id: str) -> None:
        for hook in list(self.on_ready):
            try:
                hook(space_id, source_id)
            except Exception:
                logger.exception("A source-ready hook failed")


__all__ = [
    "INGEST_KIND",
    "REFRESHABLE",
    "IngestService",
    "RefreshNotAllowedError",
]
