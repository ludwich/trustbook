"""Builds and holds the application's services (one set per app instance)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from app.capabilities.adapters.base import ChatAdapter
from app.capabilities.adapters.factory import build_chat_adapter
from app.capabilities.bundled import ProviderRegistry, set_providers
from app.capabilities.health import HealthChecker
from app.capabilities.http import OutboundHttp, RequestLogRepository
from app.capabilities.registry import CapabilityRegistry
from app.chat.overview import OVERVIEW_KIND, ChatState, OverviewService
from app.config import Paths, SettingsStore, ensure_layout
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.grounding.verify import GroundingSettings
from app.index.base import IndexRegistry
from app.index.lance import LanceIndexRegistry
from app.ingest.factory import build_ingest_tools
from app.ingest.service import INGEST_KIND, IngestService
from app.jobs.calibration import CALIBRATE_KIND, CalibrationService
from app.jobs.handlers import HandlerRegistry, default_handlers
from app.jobs.pool import WorkerPool, workers_for_power
from app.jobs.repository import JobRepository
from app.spaces.service import SpaceService

# Answers and overviews may take a while on local models that reason before replying.
CHAT_TIMEOUT_S = 240.0


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
    indexes: IndexRegistry
    overview: OverviewService
    ingest: IngestService
    # Called by ingestion as (space_id, source_id) when a source becomes ready.
    on_source_ready: list[Callable[[str, str], None]] = field(default_factory=list)

    def chat_adapter(self) -> ChatAdapter:
        """The configured chat adapter (raises ConfigError when chat is not set up)."""
        return build_chat_adapter(
            self.capabilities.endpoint("chat"), self.http, timeout=CHAT_TIMEOUT_S
        )

    def chat_state(self) -> ChatState:
        chat = self.capabilities.get("chat")
        return ChatState(ready=chat.status == "ready", remote=chat.locality == "remote")

    def grounding_settings(self) -> GroundingSettings:
        """Grounding checker on/off (the ``grounding`` capability) and the verify threshold."""
        settings = self.settings.load()
        grounding = settings.capability("grounding")
        return GroundingSettings(
            enabled=grounding.enabled,
            verify_threshold=settings.thresholds.verify,
            code_judge=grounding.code_judge is not False,
        )

    def cancel_space_jobs(self, space_id: str) -> None:
        """Cancel a space's jobs; running ones are asked to stop."""
        for job_id in self.jobs.cancel_for_space(space_id):
            self.pool.request_cancel(job_id)
        self.indexes.drop(space_id)  # the space folder (and its vectors) is about to go


def build_services(
    paths: Paths,
    *,
    providers: ProviderRegistry | None = None,
    transport: httpx.BaseTransport | None = None,
    handlers: HandlerRegistry | None = None,
    indexes: IndexRegistry | None = None,
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

    indexes = indexes or LanceIndexRegistry(lambda sid: spaces.get(sid).folder / "vectors")
    on_source_ready: list[Callable[[str, str], None]] = []
    ingest = IngestService(
        spaces=spaces,
        jobs=jobs,
        pool=pool,
        indexes=indexes,
        providers=providers,
        tools=build_ingest_tools(paths, providers, capabilities, http, transport),
        on_ready=on_source_ready,
    )
    ingest.calibration = calibration.read  # job estimates for the queue ETA
    handlers.register(INGEST_KIND, ingest.handler)
    documents = ingest.tools.documents
    calibration.parse_pdf = lambda path: documents.parse(path, "pdf")
    calibration.parser_name = lambda: "Docling" if documents.docling else "the basic PDF reader"
    overview = OverviewService(
        spaces=spaces,
        jobs=jobs,
        notify=pool.notify,
        indexes=indexes,
        providers=providers,
        chat_adapter=lambda: services.chat_adapter(),
        chat_state=lambda: services.chat_state(),
        grounding=lambda: services.grounding_settings(),
    )
    handlers.register(OVERVIEW_KIND, overview.handler)

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
        indexes=indexes,
        overview=overview,
        ingest=ingest,
        on_source_ready=on_source_ready,
    )
    services.on_source_ready.append(overview.on_source_ready)
    spaces.set_cancel_jobs(services.cancel_space_jobs)
    return services
