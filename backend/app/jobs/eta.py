"""Queue time left (SPEC §11 header indicator: "Processing: N jobs, about M min left").

Ingest jobs store their estimated seconds in the payload (``estimate_s``) when they are queued.
Overview and calibration jobs get small fixed estimates; other kinds have none and are left out.

ETA = remaining seconds of queued and running jobs, where a running job's remaining time is its
estimate × (1 − progress), divided by min(workers, number of those jobs). One job never runs on
two workers, so the ETA is never less than the longest single remaining job.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable

from app.db.models import Job

ESTIMATE_KEY = "estimate_s"
# Small fixed estimates for jobs without a stored one (seconds).
FIXED_ESTIMATES: dict[str, float] = {
    "overview": 30.0,  # one model answer on the chat endpoint
    "calibrate": 60.0,  # the speed test (DECISIONS D-030: "about a minute")
}


def job_estimate(job: Job) -> float | None:
    """The job's whole estimated duration in seconds, or ``None`` when unknown."""
    try:
        payload = json.loads(job.payload or "{}")
    except ValueError:
        payload = {}
    value = payload.get(ESTIMATE_KEY) if isinstance(payload, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return FIXED_ESTIMATES.get(job.kind)


def remaining_seconds(job: Job) -> float | None:
    """Seconds left for a queued or running job (``None``: unknown or not waiting/running)."""
    if job.status not in ("queued", "running"):
        return None
    estimate = job_estimate(job)
    if estimate is None:
        return None
    done = job.progress if job.status == "running" and job.progress is not None else 0.0
    return estimate * (1.0 - max(0.0, min(1.0, done)))


def queue_eta(jobs: Iterable[Job], workers: int) -> int | None:
    """Seconds until the queue is empty, or ``None`` when nothing has an estimate."""
    left = [r for r in (remaining_seconds(job) for job in jobs) if r is not None]
    if not left:
        return None
    parallel = max(1, min(workers, len(left)))
    return math.ceil(max(sum(left) / parallel, max(left)))
