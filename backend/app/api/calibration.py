"""Calibration routes."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import ServicesDep
from app.api.jobs import JobOut, job_out
from app.jobs.calibration import CalibrationState

router = APIRouter()


@router.get("/calibration")
def get_calibration(services: ServicesDep) -> CalibrationState:
    return services.calibration.state()


@router.post("/calibration/run", status_code=status.HTTP_202_ACCEPTED)
def run_calibration(services: ServicesDep) -> JobOut:
    job = services.calibration.enqueue()
    services.pool.notify()
    return job_out(services, job)
