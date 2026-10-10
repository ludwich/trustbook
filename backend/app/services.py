"""Builds and holds the application's services (one set per app instance)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.capabilities.bundled import ProviderRegistry, set_providers
from app.capabilities.health import HealthChecker
from app.capabilities.http import OutboundHttp, RequestLogRepository
from app.capabilities.registry import CapabilityRegistry
from app.config import Paths, SettingsStore, ensure_layout
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.jobs.calibration import CALIBRATE_KIND, CalibrationService
from app.jobs.handlers import HandlerRegistry, default_handlers
from app.jobs.pool import WorkerPool, workers_for_power
from app.jobs.repository import JobRepository
from app.spaces.service import SpaceService


@dataclass
class Services:
    paths: Paths
    settings: SettingsStore
    request_log: RequestLogRepository
    http: OutboundHttp
    providers: ProviderRegistry
    capabilities: CapabilityRegistry
    health: HealthChecker
    spaces: SpaceService
    jobs: JobRepository
    handlers: HandlerRegistry
    pool: WorkerPool
    calibration: CalibrationService

    def cancel_space_jobs(self, space_id: str) -> None:
        """Cancel a space's jobs; running ones are asked to stop."""
        for job_id in self.jobs.cancel_for_space(space_id):
            self.pool.request_cancel(job_id)


def build_services(
    paths: Paths,
    *,
    providers: ProviderRegistry | None = None,
    transport: httpx.BaseTransport | None = None,
    handlers: HandlerRegistry | None = None,
) -> Services:
    """Create the data layout, migrate global.db and wire every service together."""
    ensure_layout(paths)
    migrate(paths.global_db, "global")
    engine = get_engine(paths.global_db)

    store = SettingsStore(paths)
    settings = store.load()
    if not paths.settings_file.exists():
        store.save(settings)

    request_log = RequestLogRepository(engine)
    http = OutboundHttp(request_log, transport=transport)
    providers = providers or ProviderRegistry(paths.models_dir)
    set_providers(providers)
    capabilities = CapabilityRegistry(paths, store, providers)
    health = HealthChecker(capabilities, http, providers, searxng_url=paths.searxng_url)
    jobs = JobRepository(engine)
    spaces = SpaceService(paths)
    handlers = handlers or default_handlers
    calibration = CalibrationService(paths, providers, jobs)
    saved = calibration.read()
    if saved is not None and saved.runtime is not None:
        providers.configure(saved.runtime)  # threads and dtypes measured on this computer
    handlers.register(CALIBRATE_KIND, calibration.handler)

    def space_name(space_id: str) -> str | None:
        record = spaces.find(space_id)
        return record.name if record else None

    pool = WorkerPool(
        jobs, handlers, workers=workers_for_power(settings.power), space_name=space_name
    )

    services = Services(
        paths=paths,
        settings=store,
        request_log=request_log,
        http=http,
        providers=providers,
        capabilities=capabilities,
        health=health,
        spaces=spaces,
        jobs=jobs,
        handlers=handlers,
        pool=pool,
        calibration=calibration,
    )
    spaces.set_cancel_jobs(services.cancel_space_jobs)
    return services
