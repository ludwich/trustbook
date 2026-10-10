"""Choose speech-to-text: the bundled model (Whistle) or the configured STT endpoint."""

from __future__ import annotations

from pathlib import Path

from app.capabilities.adapters.base import AdapterError, TranscriptionAdapter
from app.capabilities.adapters.factory import build_stt_adapter
from app.capabilities.bundled import ProviderRegistry, ProviderUnavailableError, Transcriber
from app.capabilities.http import OutboundHttp
from app.capabilities.registry import CapabilityRegistry, ConfigError
from app.ingest import errors
from app.ingest.errors import IngestError
from app.parse.audio import SpeechToText


class BundledStt:
    """The bundled transcriber; it runs locally, nothing leaves the machine."""

    def __init__(self, transcriber: Transcriber, warning: str) -> None:
        self._transcriber = transcriber
        self.warning = warning

    def transcribe_wav(self, wav_path: Path) -> str:
        return self._transcriber.transcribe(wav_path).text


class EndpointStt:
    """The STT endpoint; every request is written to the request log by the outbound client."""

    warning = errors.WARN_STT_ENDPOINT

    def __init__(self, adapter: TranscriptionAdapter) -> None:
        self._adapter = adapter

    def transcribe_wav(self, wav_path: Path) -> str:
        try:
            return self._adapter.transcribe(wav_path.read_bytes(), "audio.wav").text
        except AdapterError as exc:
            raise IngestError(
                f"Speech recognition failed: {exc.message}",
                "Check the speech-to-text settings, then try again.",
            ) from exc


def resolve_stt(
    capabilities: CapabilityRegistry, providers: ProviderRegistry, http: OutboundHttp
) -> SpeechToText | None:
    """The speech-to-text to use now, or ``None`` when none is available."""
    settings = capabilities.stored("stt")
    if not settings.enabled:
        return None
    if settings.mode == "endpoint":
        try:
            endpoint = capabilities.endpoint("stt")
        except ConfigError:
            return None
        return EndpointStt(build_stt_adapter(endpoint, http))
    if not providers.runtime_available("stt"):
        return None
    try:
        transcriber = providers.transcriber()
    except ProviderUnavailableError:
        return None
    fallback = providers.info("stt").fallback
    return BundledStt(transcriber, errors.WARN_STT_ENDPOINT if fallback else errors.WARN_WHISTLE)
