"""Map domain errors to HTTP responses (``{"detail": "..."}``)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.capabilities.adapters.base import AdapterError
from app.capabilities.registry import CapabilityNotFoundError, ConfigError
from app.jobs.repository import JobNotFoundError, JobStateError
from app.spaces.service import SpaceNotFoundError, UnsafeSpacePathError


def _detail(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers for the app's domain exceptions."""

    @app.exception_handler(CapabilityNotFoundError)
    async def _capability_missing(_r: Request, _e: CapabilityNotFoundError) -> JSONResponse:
        return _detail(404, "Unknown capability.")

    @app.exception_handler(SpaceNotFoundError)
    async def _space_missing(_r: Request, _e: SpaceNotFoundError) -> JSONResponse:
        return _detail(404, "Space not found.")

    @app.exception_handler(JobNotFoundError)
    async def _job_missing(_r: Request, _e: JobNotFoundError) -> JSONResponse:
        return _detail(404, "Job not found.")

    @app.exception_handler(JobStateError)
    async def _job_state(_r: Request, exc: JobStateError) -> JSONResponse:
        return _detail(409, str(exc))

    @app.exception_handler(ConfigError)
    async def _config(_r: Request, exc: ConfigError) -> JSONResponse:
        return _detail(422, str(exc))

    @app.exception_handler(AdapterError)
    async def _adapter(_r: Request, exc: AdapterError) -> JSONResponse:
        return _detail(502, exc.message)

    @app.exception_handler(UnsafeSpacePathError)
    async def _unsafe(_r: Request, _e: UnsafeSpacePathError) -> JSONResponse:
        return _detail(
            409,
            "This space's folder is a link or lies outside the spaces folder, "
            "so it was not deleted.",
        )
