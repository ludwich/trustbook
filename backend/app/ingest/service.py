"""Source lifecycle: create, refresh, delete, and the ``ingest`` job that indexes a source.

Status flow: queued → reading → indexing → ready | failed (SPEC §7.5). The job is space-scoped
(the active space's jobs run first), cancellable and reports progress: reading 0–40 %,
embedding 40–95 %, writing the index to 100 %.

Indexing (Gate 1 finding G2): the chunk rows go to ``space.db`` first, then parallel embedding
streams (:mod:`app.capabilities.streams`) feed a bounded queue that this job's thread drains,
appending vectors to the index every :data:`WRITE_BATCH` chunks; the full-text index is
refreshed once at the end. The source only becomes ``ready`` after that, so chat never sees a
half-indexed source; a failure removes the partial rows and vectors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.capabilities.bundled import Embedder, ProviderRegistry, ProviderUnavailableError
from app.capabilities.streams import EmbeddedBatch, embed_in_streams
from app.chunking.tokens import TokenCounter, load_token_counter
from app.db.models import Source
from app.index.base import IndexedChunk, IndexRegistry, SpaceIndex, search_text
from app.index.lance import IndexDimensionError
from app.ingest import errors
from app.ingest.browse_root import BrowsePathError, BrowseRootMissingError, resolve_in_root
from app.ingest.errors import IngestError, fail
from app.ingest.estimate import job_seconds
from app.ingest.git import AheadTracker
from app.ingest.incremental import ChunkPlan, load_previous, plan_chunks
from app.ingest.readers import IngestTools, read_source
from app.jobs.calibration import Calibration
from app.jobs.eta import ESTIMATE_KEY
from app.jobs.handlers import JobCancelledError, JobContext, JobStage
from app.jobs.pool import WorkerPool
from app.jobs.repository import JobNotFoundError, JobRepository
from app.parse.snapshot import Snapshot, write_snapshot
from app.spaces.service import SpaceService
from app.spaces.source_schemas import AheadOut, SourceOut
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
WRITE_BATCH = 512  # vectors per index append
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
        # This computer's measured speed, for job estimates (set by the app; tests: none).
        self.calibration: Callable[[], Calibration | None] = lambda: None
        self.ahead_tracker = AheadTracker()

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
        stage: JobStage | None = None
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
                stage = self.jobs.stage(job)
        warnings = errors.split_warnings(source.quality_warning)
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
            quality_warning=" ".join(warnings) or None,
            quality_warnings=warnings,
            enabled=source.enabled and status != "failed",  # G7: failed is never ticked
            snapshot_at=source.snapshot_at,
            commit_hash=source.commit_hash,
            stats=load_stats(source),
            created_at=source.created_at,
            job_id=source.job_id,
            ahead=self._ahead(source) if status == "ready" else None,
            stage=stage,
        )

    def _ahead(self, source: Source) -> AheadOut | None:
        """For a ready folder source: how far the folder's HEAD moved since indexing."""
        if source.type != "folder" or not source.commit_hash or not source.origin:
            return None
        try:
            root, _relative = resolve_in_root(self.tools.browse_root, source.origin)
            ahead = self.ahead_tracker.ahead(root, self.tools.browse_root, source.commit_hash)
        except (BrowsePathError, BrowseRootMissingError, OSError):
            return None
        if ahead is None:
            return None
        return AheadOut(
            indexed_commit=ahead.indexed_commit,
            head_commit=ahead.head_commit,
            commits_ahead=ahead.commits_ahead,
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

    def estimate_seconds(self, repo: SourceRepository, source: Source) -> int | None:
        """Estimated seconds for indexing ``source`` now (``None``: can't be told)."""
        try:
            calibration = self.calibration()
        except Exception:
            logger.warning("Could not read the speed test results", exc_info=True)
            calibration = None
        return job_seconds(source, repo.folder(source.id), self.tools.browse_root, calibration)

    def enqueue(self, repo: SourceRepository, source: Source) -> Source:
        payload: dict[str, Any] = {"source_id": source.id}
        seconds = self.estimate_seconds(repo, source)
        if seconds is not None:
            payload[ESTIMATE_KEY] = seconds
        job = self.jobs.enqueue(
            INGEST_KIND,
            _title_for_job(source.title),
            space_id=repo.record.id,
            payload=payload,
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
        if self.out(source).status == "failed":
            # G7: a failed source was unticked; refreshing it ticks it again like a new source
            # (a new failure unticks it once more, so only a successful refresh keeps it ticked).
            source = repo.update(source.id, enabled=True)
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
            enabled=False,  # G7: a failed source is unticked and not tickable
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
        finally:
            # Indexing replicas hold GBs; keep them only while more indexing is waiting (D-054).
            if self.jobs.others_unfinished(INGEST_KIND, ctx.job_id) == 0:
                self.providers.release_replicas()

    def _fail_quietly(self, repo: SourceRepository, source: Source, exc: IngestError) -> None:
        try:
            self._fail(repo, source, exc)
        except SourceNotFoundError:
            pass
        except Exception:
            logger.warning("Could not record the failure of source %s", source.id, exc_info=True)

    def _embed_into(
        self, ctx: JobContext, index: SpaceIndex, chunks: Sequence[IndexedChunk]
    ) -> None:
        """Embed ``chunks`` with the indexing streams and append their vectors to ``index``.

        Vectors are written every :data:`WRITE_BATCH` chunks while the streams keep embedding;
        the streams wait when the writer falls behind (bounded queue), so memory stays flat.
        """
        pending: list[tuple[int, list[float]]] = []
        done = 0

        def flush() -> None:
            pending.sort(key=lambda item: item[0])
            index.append([chunks[i] for i, _ in pending], [vector for _, vector in pending])
            pending.clear()

        def on_batch(batch: EmbeddedBatch) -> None:
            nonlocal done
            ctx.stage(None)  # a batch is done: the models are loaded and running
            pending.extend(zip(batch.indices, batch.vectors, strict=True))
            done += len(batch.indices)
            if len(pending) >= WRITE_BATCH:
                flush()
            ctx.progress(0.4 + 0.55 * done / len(chunks))

        def embedder_for(stream: int) -> Embedder:
            if not self.providers.ingest_embedder_loaded(stream):
                ctx.stage("loading_models")  # the first batch of a stream loads its embedder
            return self.providers.ingest_embedder(stream)

        try:
            streams = self.providers.ingest_streams()
            used = embed_in_streams(
                [c.text for c in chunks],
                embedder_for=embedder_for,
                streams=streams,
                on_batch=on_batch,
                guard=lambda: self._models(ctx),  # waits while calibration measures
                check_cancelled=ctx.check_cancelled,
            )
        except ProviderUnavailableError as exc:
            raise fail(errors.NO_EMBEDDER) from exc
        if pending:
            flush()
        logger.info("Embedded %d chunks with %d stream(s)", len(chunks), used)

    @contextmanager
    def _models(self, ctx: JobContext) -> Iterator[None]:
        """The shared model lock; the job's stage says so while calibration holds it (D-113)."""
        waited = False

        def on_wait() -> None:
            nonlocal waited
            waited = True
            ctx.stage("waiting_for_calibration")

        with self.providers.use.shared(on_wait=on_wait):
            if waited:
                ctx.stage(None)
            yield

    def _run(self, ctx: JobContext, repo: SourceRepository, source: Source) -> None:
        space_id = repo.record.id
        repo.update(source.id, status="reading", failure_reason=None, failure_next_step=None)
        ctx.progress(0.0)
        folder = repo.folder(source.id)
        folder.mkdir(parents=True, exist_ok=True)
        previous = load_previous(repo, source.id, folder) if source.type == "folder" else None
        old_ids = [c.id for c in repo.chunks(source.id)] if previous is not None else []
        # Trusted again only after this run succeeds (a crash mid-run means a full re-index).
        repo.replace_files(source.id, [])
        with self._models(ctx):  # parsing (Docling, STT) also uses the CPU models
            snapshot, snapshot_at = read_source(
                self.tools,
                source,
                folder,
                lambda f: ctx.progress(0.4 * max(0.0, min(1.0, f))),
                ctx.check_cancelled,
                previous=previous.snapshot if previous is not None else None,
            )
        ctx.check_cancelled()
        write_snapshot(folder, snapshot)
        repo.update(source.id, status="indexing")
        plan = plan_chunks(
            snapshot, self.token_counter(), source.id, old_ids=old_ids, previous=previous
        )
        if not plan.rows:
            raise fail(errors.NO_TEXT)
        ctx.check_cancelled()
        index = self.indexes.for_space(space_id)
        try:
            self._index(ctx, repo, index, source.id, plan)
        except BaseException as exc:
            self._discard_chunks(repo, index, source.id)
            if isinstance(exc, IndexDimensionError):
                raise fail(errors.EMBEDDING_CHANGED) from exc
            raise
        if plan.files:
            repo.replace_files(source.id, plan.files)
        self._mark_ready(repo, source, snapshot, snapshot_at, len(plan.rows))
        ctx.progress(1.0)
        self._notify_ready(space_id, source.id)

    def _index(
        self,
        ctx: JobContext,
        repo: SourceRepository,
        index: SpaceIndex,
        source_id: str,
        plan: ChunkPlan,
    ) -> None:
        """Store ``plan``'s rows, drop stale vectors and embed the new chunks."""
        # Read before the rows are stored: committing expires the row objects.
        indexed = [IndexedChunk(r.id, source_id, search_text(r.text)) for r in plan.new_rows]
        counts = (len(plan.new_rows), len(plan.rows) - len(plan.new_rows), len(plan.stale_ids))
        # Rows first: retrieval skips indexed ids without a row, and the source is not ready yet.
        repo.replace_chunks(source_id, plan.rows)
        if plan.incremental:
            index.delete_chunks(plan.stale_ids)
            logger.info("Refresh of %s: %d new chunks, %d kept, %d removed", source_id, *counts)
        else:
            index.delete_source(source_id)
        if indexed:
            self._embed_into(ctx, index, indexed)
        ctx.check_cancelled()
        index.refresh_text_index()

    def _discard_chunks(self, repo: SourceRepository, index: SpaceIndex, source_id: str) -> None:
        """Drop a half-indexed source's rows and vectors (it is marked failed next)."""
        try:
            index.delete_source(source_id)
            repo.replace_chunks(source_id, [])
            repo.replace_files(source_id, [])
        except Exception:
            logger.warning("Could not clean up source %s after a failure", source_id, exc_info=True)

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
            "quality_warning": errors.join_warnings(warnings),
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
