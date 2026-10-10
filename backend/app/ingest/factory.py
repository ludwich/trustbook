"""Build the real ingestion tools (tests replace single tools on ``services.ingest.tools``)."""

from __future__ import annotations

import httpx

from app.capabilities.bundled import ProviderRegistry, default_threads
from app.capabilities.http import OutboundHttp
from app.capabilities.registry import CapabilityRegistry
from app.config import Paths
from app.ingest.readers import IngestTools
from app.ingest.stt import resolve_stt
from app.parse.browser import PlaywrightRenderer
from app.parse.documents import DocumentParser, docling_artifacts
from app.parse.web import WebFetcher
from app.parse.youtube import YtDlpClient
from app.security.gitleaks import scan_folder


def build_ingest_tools(
    paths: Paths,
    providers: ProviderRegistry,
    capabilities: CapabilityRegistry,
    http: OutboundHttp,
    transport: httpx.BaseTransport | None = None,
) -> IngestTools:
    """Docling/pypdf, web fetcher + headless browser, yt-dlp, gitleaks and speech-to-text."""
    web = WebFetcher(transport=transport)
    # The browser checks addresses with whatever resolver the fetcher uses.
    web.renderer = PlaywrightRenderer(resolver=lambda host: web.resolver(host))
    documents = DocumentParser(
        docling_artifacts(paths.models_dir),
        threads=lambda: providers.options.torch_threads or default_threads(),
    )
    return IngestTools(
        browse_root=paths.browse_root,
        documents=documents,
        web=web,
        youtube=YtDlpClient(),
        scanner=scan_folder,
        stt=lambda: resolve_stt(capabilities, providers, http),
    )
