"""Job queue persistence in global.db."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from sqlalchemy import Engine, case, func, update
from sqlmodel import Session, col, select

from app.db.models import Job
from app.timeutil import utc_now_iso

UNFINISHED = ("queued", "running", "paused")
FINISHED = ("done", "failed", "cancelled")


class JobNotFoundError(LookupError):
    """No job with this id."""


class JobStateError(ValueError):
    """The requested change does not fit the job's status. Message is plain English."""


class JobRepository:
    """CRUD and state transitions for jobs. Thread-safe."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._lock = threading.RLock()

    # --- queries ---------------------------------------------------------------------------

    def get(self, job_id: str) -> Job:
        with Session(self._engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return job

    def list_for_view(self, finished_limit: int = 50) -> list[Job]:
        """Unfinished jobs in queue order, then the newest ``finished_limit`` finished jobs."""
        with Session(self._engine) as session:
            unfinished = session.exec(
                select(Job)
                .where(col(Job.status).in_(UNFINISHED))
                .order_by(col(Job.position), col(Job.created_at))
            ).all()
            finished = session.exec(
                select(Job)
                .where(col(Job.status).in_(FINISHED))
                .order_by(col(Job.finished_at).desc(), col(Job.created_at).desc())
                .limit(finished_limit)
            ).all()
            return [*unfinished, *finished]

    def counts(self) -> dict[str, int]:
        with Session(self._engine) as session:
            rows = session.exec(select(Job.status, func.count()).group_by(Job.status)).all()
            return {str(status): int(count) for status, count in rows}

    def latest(self, kind: str) -> Job | None:
        with Session(self._engine) as session:
            return session.exec(
                select(Job).where(Job.kind == kind).order_by(col(Job.created_at).desc()).limit(1)
            ).first()

    def find_unfinished(self, kind: str) -> Job | None:
        with Session(self._engine) as session:
            return session.exec(
                select(Job)
                .where(Job.kind == kind, col(Job.status).in_(UNFINISHED))
                .order_by(col(Job.position))
                .limit(1)
            ).first()

    def others_unfinished(self, kind: str, job_id: str) -> int:
        """Unfinished jobs of ``kind`` other than ``job_id``."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(Job.id).where(
                    Job.kind == kind, col(Job.status).in_(UNFINISHED), Job.id != job_id
                )
            ).all()
            return len(rows)

    def status(self, job_id: str) -> str | None:
        with Session(self._engine) as session:
            job = session.get(Job, job_id)
            return job.status if job else None

    # --- commands --------------------------------------------------------------------------

    def enqueue(
        self,
        kind: str,
        title: str,
        *,
        space_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Job:
        """Add a job at the end of the queue."""
        with self._lock, Session(self._engine) as session:
            last = session.exec(select(func.max(Job.position))).one()
            job = Job(
                id=uuid.uuid4().hex,
                kind=kind,
                title=title,
                space_id=space_id,
                payload=json.dumps(payload or {}),
                status="queued",
                position=int(last or 0) + 1,
                created_at=utc_now_iso(),
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def claim_next(self, active_space_id: str | None) -> Job | None:
        """Atomically mark the next runnable job as running and return it."""
        with self._lock, Session(self._engine) as session:
            priority = case((col(Job.space_id) == active_space_id, 0), else_=1)
            query = select(Job).where(Job.status == "queued")
            if active_space_id is not None:
                query = query.order_by(priority, col(Job.position))
            else:
                query = query.order_by(col(Job.position))
            job = session.exec(query.limit(1)).first()
            if job is None:
                return None
            job.status = "running"
            job.started_at = utc_now_iso()
            job.progress = 0.0
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def set_progress(self, job_id: str, progress: float) -> None:
        with Session(self._engine) as session:
            session.exec(
                update(Job)
                .where(col(Job.id) == job_id, col(Job.status) == "running")
                .values(progress=max(0.0, min(1.0, progress)))
            )
            session.commit()

    def finish(self, job_id: str, status: str, error: str | None = None) -> None:
        """Mark a running job done/failed (no-op if it was cancelled meanwhile)."""
        values: dict[str, Any] = {"status": status, "finished_at": utc_now_iso(), "error": error}
        if status == "done":
            values["progress"] = 1.0
        with self._lock, Session(self._engine) as session:
            session.exec(
                update(Job)
                .where(col(Job.id) == job_id, col(Job.status) == "running")
                .values(**values)
            )
            session.commit()

    def cancel(self, job_id: str) -> Job:
        return self._transition(job_id, ("queued", "running", "paused"), "cancelled", "cancel")

    def pause(self, job_id: str) -> Job:
        return self._transition(job_id, ("queued",), "paused", "pause")

    def resume(self, job_id: str) -> Job:
        return self._transition(job_id, ("paused",), "queued", "resume")

    def cancel_for_space(self, space_id: str) -> list[str]:
        """Cancel every unfinished job of a space; return the ids that were running."""
        with self._lock, Session(self._engine) as session:
            jobs = session.exec(
                select(Job).where(Job.space_id == space_id, col(Job.status).in_(UNFINISHED))
            ).all()
            running = [job.id for job in jobs if job.status == "running"]
            now = utc_now_iso()
            for job in jobs:
                job.status = "cancelled"
                job.finished_at = now
                session.add(job)
            session.commit()
            return running

    def reorder(self, job_ids: list[str]) -> None:
        """Give the listed waiting jobs the queue slots they already hold, in the new order."""
        if len(set(job_ids)) != len(job_ids):
            raise JobStateError("Each job may appear only once.")
        with self._lock, Session(self._engine) as session:
            jobs = {
                job.id: job for job in session.exec(select(Job).where(col(Job.id).in_(job_ids)))
            }
            for job_id in job_ids:
                if job_id not in jobs:
                    raise JobNotFoundError(job_id)
                if jobs[job_id].status not in ("queued", "paused"):
                    raise JobStateError("Only waiting jobs can be reordered.")
            slots = sorted(job.position for job in jobs.values())
            for job_id, slot in zip(job_ids, slots, strict=True):
                jobs[job_id].position = slot
                session.add(jobs[job_id])
            session.commit()

    def requeue_interrupted(self) -> int:
        """Jobs left ``running`` by a previous process go back to ``queued``."""
        with self._lock, Session(self._engine) as session:
            result = session.exec(
                update(Job)
                .where(col(Job.status) == "running")
                .values(status="queued", started_at=None, progress=None)
            )
            session.commit()
            return int(result.rowcount or 0)

    def _transition(self, job_id: str, allowed: tuple[str, ...], target: str, verb: str) -> Job:
        with self._lock, Session(self._engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            if job.status not in allowed:
                raise JobStateError(_transition_message(verb, job.status))
            job.status = target
            if target == "cancelled":
                job.finished_at = utc_now_iso()
            session.add(job)
            session.commit()
            session.refresh(job)
            return job


def _transition_message(verb: str, status: str) -> str:
    if verb == "pause":
        return "Only waiting jobs can be paused."
    if verb == "resume":
        return "Only paused jobs can be resumed."
    return f"This job is already {status}."
