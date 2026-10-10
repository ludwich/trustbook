"""Overview routes (docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import ServicesDep
from app.api.jobs import JobOut, job_out
from app.chat.overview import OverviewError
from app.chat.schemas import OverviewOut, OverviewUpdate

router = APIRouter()


@router.get("/spaces/{space_id}/overview")
def get_overview(space_id: str, services: ServicesDep) -> OverviewOut:
    return services.overview.get(services.spaces.get(space_id))


@router.put("/spaces/{space_id}/overview")
def update_overview(space_id: str, body: OverviewUpdate, services: ServicesDep) -> OverviewOut:
    return services.overview.set_enabled(services.spaces.get(space_id), body.enabled)


@router.post("/spaces/{space_id}/overview/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_overview(space_id: str, services: ServicesDep) -> JobOut:
    space = services.spaces.get(space_id)
    try:
        job = services.overview.refresh(space)
    except OverviewError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return job_out(services, job)
