"""In-process worker pool over the global SQLite job queue."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable

from app.capabilities.http import space_context
from app.db.models import Job
from app.jobs.handlers import HandlerRegistry, JobCancelledError, JobContext
from app.jobs.repository import JobRepository

logger = logging.getLogger("trustbook.jobs")


def workers_for_power(power: str, cpu_count: int | None = None) -> int:
    """Worker count for a power setting: low 1, normal 2, high max(2, cores - 2)."""
    if power == "low":
        return 1
    if power == "high":
        cores = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
        return max(2, cores - 2)
    return 2


def error_message(exc: BaseException) -> str:
    """A short, plain message for a failed job."""
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message[:500]
    text = str(exc).strip()
    return (f"{type(exc).__name__}: {text}" if text else type(exc).__name__)[:500]


class WorkerPool:
    """Runs queued jobs on ``workers`` threads; the active space's jobs go first."""

    def __init__(
        self,
        repo: JobRepository,
        handlers: HandlerRegistry,
        *,
        workers: int = 2,
        space_name: Callable[[str], str | None] | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self._repo = repo
        self._handlers = handlers
        self._target = max(1, workers)
        self._space_name = space_name or (lambda _space_id: None)
        self._poll = poll_interval
        self._cond = threading.Condition()
        self._threads: list[threading.Thread] = []
        self._alive = 0
        self._wakeups = 0
        self._started = False
        self._stopping = False
        self._cancelled: set[str] = set()
        self._active_space_id: str | None = None
        self._serial = 0

    # --- configuration ---------------------------------------------------------------------

    @property
    def workers(self) -> int:
        return self._target

    @property
    def active_space_id(self) -> str | None:
        return self._active_space_id

    def set_active_space(self, space_id: str | None) -> None:
        self._active_space_id = space_id
        self.notify()

    def resize(self, workers: int) -> None:
        """Change the worker count; extra workers stop after their current job."""
        with self._cond:
            self._target = max(1, workers)
            if self._started and not self._stopping:
                while self._alive < self._target:
                    self._spawn()
            self._cond.notify_all()

    # --- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        with self._cond:
            if self._started:
                return
            self._started = True
            self._stopping = False
            while self._alive < self._target:
                self._spawn()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout)
        with self._cond:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._started = False

    def notify(self) -> None:
        """Wake idle workers (call after enqueueing or resuming a job)."""
        with self._cond:
            self._wakeups += 1
            self._cond.notify_all()

    # --- cancellation ----------------------------------------------------------------------

    def request_cancel(self, job_id: str) -> None:
        """Ask a running job to stop at its next cancellation check."""
        with self._cond:
            self._cancelled.add(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        with self._cond:
            if job_id in self._cancelled:
                return True
        return self._repo.status(job_id) == "cancelled"

    # --- workers ---------------------------------------------------------------------------

    def _spawn(self) -> None:
        self._serial += 1
        thread = threading.Thread(
            target=self._loop, name=f"trustbook-worker-{self._serial}", daemon=True
        )
        self._alive += 1
        self._threads = [t for t in self._threads if t.is_alive()]
        self._threads.append(thread)
        thread.start()

    def _should_exit(self) -> bool:
        if self._stopping or self._alive > self._target:
            self._alive -= 1
            self._cond.notify_all()
            return True
        return False

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._should_exit():
                    return
                seen = self._wakeups
            try:
                job = self._repo.claim_next(self._active_space_id)
            except Exception:
                logger.exception("Could not read the job queue")
                job = None
            if job is not None:
                self._run(job)
                continue
            with self._cond:
                if self._wakeups == seen and not self._stopping:
                    self._cond.wait(self._poll)

    def _run(self, job: Job) -> None:
        handler = self._handlers.get(job.kind)
        if handler is None:
            self._repo.finish(job.id, "failed", f"No handler for job type “{job.kind}”.")
            return
        try:
            payload = json.loads(job.payload or "{}")
        except ValueError:
            payload = {}
        ctx = JobContext(
            job_id=job.id,
            kind=job.kind,
            space_id=job.space_id,
            payload=payload if isinstance(payload, dict) else {},
            _progress=lambda value: self._repo.set_progress(job.id, value),
            _cancelled=lambda: self.is_cancelled(job.id),
        )
        space_name = self._space_name(job.space_id) if job.space_id else None
        try:
            with space_context(job.space_id, space_name):
                handler(ctx)
        except JobCancelledError:
            self._repo.finish(job.id, "cancelled")
        except Exception as exc:
            logger.exception("Job %s (%s) failed", job.id, job.kind)
            self._repo.finish(job.id, "failed", error_message(exc))
        else:
            self._repo.finish(job.id, "done")
        finally:
            with self._cond:
                self._cancelled.discard(job.id)
