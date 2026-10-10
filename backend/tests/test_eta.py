"""Queue time left: stored job estimates, progress, workers (SPEC §11)."""

from __future__ import annotations

import json
from typing import Any

from app.chat.overview import OVERVIEW_KIND
from app.db.models import Job
from app.jobs.calibration import CALIBRATE_KIND
from app.jobs.eta import FIXED_ESTIMATES, job_estimate, queue_eta, remaining_seconds
from tests.conftest import AppFactory


def job(
    status: str = "queued",
    estimate: Any = None,
    *,
    kind: str = "ingest",
    progress: float | None = None,
    payload: str | None = None,
) -> Job:
    data = {"source_id": "s"} if estimate is None else {"source_id": "s", "estimate_s": estimate}
    return Job(
        id="j",
        kind=kind,
        title="t",
        payload=payload if payload is not None else json.dumps(data),
        status=status,
        progress=progress,
        position=1,
        created_at="2026-10-10T00:00:00Z",
    )


def test_fixed_estimates_match_the_job_kinds() -> None:
    assert set(FIXED_ESTIMATES) == {OVERVIEW_KIND, CALIBRATE_KIND}
    assert job_estimate(job(kind=OVERVIEW_KIND)) == FIXED_ESTIMATES[OVERVIEW_KIND]
    assert job_estimate(job(kind=CALIBRATE_KIND)) == FIXED_ESTIMATES[CALIBRATE_KIND]


def test_job_estimate_reads_the_payload() -> None:
    assert job_estimate(job(estimate=90)) == 90.0
    assert job_estimate(job(estimate=12.5)) == 12.5
    assert job_estimate(job()) is None  # an ingest job queued without an estimate
    assert job_estimate(job(estimate="soon")) is None
    assert job_estimate(job(estimate=True)) is None
    assert job_estimate(job(estimate=-1)) is None
    assert job_estimate(job(payload="not json")) is None
    assert job_estimate(job(payload="[1]")) is None
    assert job_estimate(job(kind="other")) is None


def test_remaining_uses_progress_of_running_jobs_only() -> None:
    assert remaining_seconds(job("queued", 100)) == 100.0
    assert remaining_seconds(job("queued", 100, progress=0.5)) == 100.0
    assert remaining_seconds(job("running", 100, progress=0.25)) == 75.0
    assert remaining_seconds(job("running", 100)) == 100.0
    assert remaining_seconds(job("running", 100, progress=1.5)) == 0.0
    for status in ("paused", "done", "failed", "cancelled"):
        assert remaining_seconds(job(status, 100)) is None


def test_queue_eta() -> None:
    assert queue_eta([], 2) is None
    assert queue_eta([job("paused", 100), job("done", 50), job()], 2) is None
    assert queue_eta([job("queued", 100)], 2) == 100  # one job uses one worker
    # 100 + 60 + 40 = 200 s over two workers.
    jobs = [job("running", 120, progress=1 / 6), job("queued", 60), job("queued", 40)]
    assert queue_eta(jobs, 2) == 100
    assert queue_eta(jobs, 1) == 200
    assert queue_eta(jobs, 8) == 100  # never less than the longest job: it runs on one worker
    assert queue_eta([job("queued", 10.2)], 1) == 11  # rounded up
    assert queue_eta([job("queued", 30), job(kind=OVERVIEW_KIND)], 1) == 60


def test_jobs_api_reports_remaining_per_job(make_client: AppFactory) -> None:
    client = make_client()
    services = client.app.state.services  # type: ignore[attr-defined]
    job = services.jobs.enqueue("ingest", "Read a folder", payload={"estimate_s": 100})
    listed = {j["id"]: j for j in client.get("/api/jobs").json()["jobs"]}
    assert listed[job.id]["eta_seconds"] == 100
