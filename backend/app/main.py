"""FastAPI app factory, lifespan and static frontend."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.api import (
    browse,
    calibration,
    capabilities,
    chat,
    gate_log,
    jobs,
    overview,
    request_log,
    sources,
    spaces,
    system,
)
from app.api.errors import install_error_handlers
from app.capabilities.bundled import ProviderRegistry
from app.config import Paths
from app.db.engine import dispose_all
from app.index.base import IndexRegistry
from app.jobs.handlers import HandlerRegistry
from app.services import build_services

logger = logging.getLogger("trustbook")


def _disable_third_party_telemetry() -> None:
    """Trustbook sends no telemetry; switch off any a dependency might send."""
    os.environ["NEEDLE_TELEMETRY"] = "0"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _api_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    for module in (
        system,
        capabilities,
        spaces,
        jobs,
        request_log,
        calibration,
        sources,
        browse,
        chat,
        overview,
        gate_log,
    ):
        router.include_router(module.router)
    return router


def _install_static(app: FastAPI, static_dir: Path) -> None:
    """Serve the built UI with an SPA fallback (any non-/api GET → index.html)."""
    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if not index.is_file():
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index)


def create_app(
    *,
    paths: Paths | None = None,
    providers: ProviderRegistry | None = None,
    transport: httpx.BaseTransport | None = None,
    handlers: HandlerRegistry | None = None,
    start_workers: bool = True,
    calibrate_on_start: bool = True,
    probe_search_on_start: bool = True,
    indexes: IndexRegistry | None = None,
) -> FastAPI:
    """Create the app. All arguments exist so tests can inject fakes and a tmp data folder."""
    _disable_third_party_telemetry()
    resolved = paths or Paths.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _configure_logging()
        services = build_services(
            resolved, providers=providers, transport=transport, handlers=handlers, indexes=indexes
        )
        requeued = services.jobs.requeue_interrupted()
        if requeued:
            logger.info("Re-queued %d interrupted job(s)", requeued)
        services.spaces.migrate_all()
        if calibrate_on_start:
            services.calibration.ensure_first_run()
        if start_workers:
            services.pool.start()
        if probe_search_on_start:
            threading.Thread(
                target=services.health.probe_searxng, name="searxng-probe", daemon=True
            ).start()
        app.state.services = services
        logger.info("Trustbook %s ready; data folder %s", resolved.version, resolved.data)
        try:
            yield
        finally:
            services.pool.stop()
            dispose_all()

    app = FastAPI(
        title="Trustbook",
        version=resolved.version,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.include_router(_api_router())
    if resolved.static_dir.is_dir():
        _install_static(app, resolved.static_dir)
    return app


app = create_app()
