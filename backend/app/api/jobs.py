"""Global queue routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import ServicesDep
from app.db.models import Job

router = APIRouter()

JobStatus = Literal["queued", "running", "paused", "done", "failed", "cancelled"]


class JobOut(BaseModel):
    id: str
    kind: str
    title: str
    space_id: str | None
    space_name: str | None
    status: JobStatus
    progress: float | None
    position: int
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None

    @classmethod
    def build(cls, job: Job, names: dict[str, str]) -> JobOut:
        return cls(
            id=job.id,
            kind=job.kind,
            title=job.title,
            space_id=job.space_id,
            space_name=names.get(job.space_id) if job.space_id else None,
            status=job.status,
            progress=job.progress,
            position=job.position,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class QueueSummary(BaseModel):
    queued: int
    running: int
    paused: int
    eta_seconds: int | None
    workers: int


class JobsOut(BaseModel):
    jobs: list[JobOut]
    summary: QueueSummary


class ReorderBody(BaseModel):
    job_ids: list[str]


def job_out(services: ServicesDep, job: Job) -> JobOut:
    return JobOut.build(job, services.spaces.names() if job.space_id else {})


@router.get("/jobs")
def list_jobs(services: ServicesDep) -> JobsOut:
    names = services.spaces.names()
    counts = services.jobs.counts()
    return JobsOut(
        jobs=[JobOut.build(job, names) for job in services.jobs.list_for_view()],
        summary=QueueSummary(
            queued=counts.get("queued", 0),
            running=counts.get("running", 0),
            paused=counts.get("paused", 0),
            eta_seconds=None,
            workers=services.pool.workers,
        ),
    )


@router.post("/jobs/reorder", status_code=status.HTTP_204_NO_CONTENT)
def reorder_jobs(body: ReorderBody, services: ServicesDep) -> Response:
    services.jobs.reorder(body.job_ids)
    services.pool.notify()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, services: ServicesDep) -> JobOut:
    job = services.jobs.cancel(job_id)
    services.pool.request_cancel(job_id)
    return job_out(services, job)


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: str, services: ServicesDep) -> JobOut:
    return job_out(services, services.jobs.pause(job_id))


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: str, services: ServicesDep) -> JobOut:
    job = services.jobs.resume(job_id)
    services.pool.notify()
    return job_out(services, job)
