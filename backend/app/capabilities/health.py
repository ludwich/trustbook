"""Capability health checks (the Settings / wizard "Test" button) and model listing."""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from app.capabilities.adapters.base import AdapterError
from app.capabilities.adapters.factory import (
    build_chat_adapter,
    build_search_adapter,
    build_stt_adapter,
)
from app.capabilities.audio import wav_bytes
from app.capabilities.bundled import ProviderRegistry, ProviderUnavailableError
from app.capabilities.http import OutboundHttp
from app.capabilities.registry import CapabilityRegistry, ConfigError
from app.capabilities.schemas import CapabilityConfigUpdate
from app.config import TestResult

logger = logging.getLogger("trustbook.health")

PING_PROMPT = "Reply with OK"
PING_TIMEOUT_S = 30.0
PING_MAX_TOKENS = 256  # reasoning models may spend tokens thinking before they answer


class HealthChecker:
    """Runs the Test for each capability against saved config merged with an optional draft."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        http: OutboundHttp,
        providers: ProviderRegistry,
        *,
        searxng_url: str,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._registry = registry
        self._http = http
        self._providers = providers
        self._searxng_url = searxng_url
        self._clock = clock

    def test(self, cap_id: str, draft: CapabilityConfigUpdate | None = None) -> TestResult:
        """Run the Test. Without a draft the result is saved as ``last_test``.

        Raises :class:`ConfigError` when the draft itself is invalid (e.g. a malformed address).
        """
        is_draft = draft is not None and not draft.is_empty()
        if is_draft:
            assert draft is not None
            self._registry.merge(cap_id, draft)  # validate the draft up front
        started = self._clock()
        try:
            message = self._run(cap_id, draft if is_draft else None)
            ok = True
        except (AdapterError, ConfigError, ProviderUnavailableError) as exc:
            ok, message = False, str(exc)
        except Exception as exc:
            logger.exception("Test of %s failed unexpectedly", cap_id)
            ok, message = False, f"The test failed unexpectedly ({type(exc).__name__})."
        latency_ms = int((self._clock() - started) * 1000)
        result = TestResult(ok=ok, message=message, latency_ms=latency_ms)
        if not is_draft:
            self._registry.save_test(cap_id, result)
        return result

    def list_models(self, cap_id: str, draft: CapabilityConfigUpdate | None = None) -> list[str]:
        """Model ids offered by the endpoint (raises ConfigError / AdapterError)."""
        manifest = self._registry.manifest(cap_id)
        settings = self._registry.effective(cap_id, draft)
        if cap_id == "stt" and settings.mode != "endpoint":
            raise ConfigError("Switch speech-to-text to an endpoint to list its models.")
        if manifest.contract not in ("openai_chat", "openai_transcriptions", "openai_speech"):
            raise ConfigError(f"{manifest.label} has no model list.")
        endpoint = self._registry.endpoint(cap_id, draft)
        if cap_id == "stt":
            return build_stt_adapter(endpoint, self._http).list_models()
        return build_chat_adapter(endpoint, self._http, timeout=PING_TIMEOUT_S).list_models()

    # --- per capability --------------------------------------------------------------------

    def _run(self, cap_id: str, draft: CapabilityConfigUpdate | None) -> str:
        if cap_id in ("chat", "gate"):
            return self._chat_ping(cap_id, draft)
        if cap_id == "embeddings":
            return self._embed()
        if cap_id == "reranker":
            return self._rerank()
        if cap_id == "grounding":
            return self._grounding()
        if cap_id == "stt":
            return self._stt(draft)
        if cap_id == "web_search":
            return self._search(draft)
        if cap_id == "tts":
            raise ConfigError("Text-to-speech is not used yet, so there is nothing to test.")
        raise ConfigError(f"There is no test for {cap_id}.")

    def _chat_ping(self, cap_id: str, draft: CapabilityConfigUpdate | None) -> str:
        endpoint = self._registry.endpoint(cap_id, draft)
        adapter = build_chat_adapter(endpoint, self._http, timeout=PING_TIMEOUT_S)
        started = self._clock()
        result = adapter.chat(
            [{"role": "user", "content": PING_PROMPT}],
            temperature=0,
            max_tokens=PING_MAX_TOKENS,
        )
        seconds = self._clock() - started
        if result.text.strip():
            return f"Connected. The model replied in {seconds:.1f} s."
        if result.reasoning and result.reasoning.strip():
            return f"Connected. The model replied in {seconds:.1f} s (reasoning only)."
        raise AdapterError("bad_response", "Connected, but the model sent an empty reply.")

    def _embed(self) -> str:
        embedder = self._providers.embedder()
        vectors = embedder.embed_documents(["Trustbook checks this sentence."])
        if len(vectors) != 1 or not vectors[0]:
            raise AdapterError("bad_response", "The embedding model returned no vector.")
        return f"Model works ({len(vectors[0])} dimensions)."

    def _rerank(self) -> str:
        scores = self._providers.reranker().score(
            "What is Trustbook?", ["Trustbook is a local research notebook."]
        )
        if len(scores) != 1 or not 0.0 <= scores[0] <= 1.0:
            raise AdapterError("bad_response", "The reranker returned an invalid score.")
        return "Model works."

    def _grounding(self) -> str:
        scores = self._providers.grounding().score([("The sky is blue.", "The sky is blue.")])
        if len(scores) != 1 or not 0.0 <= scores[0] <= 1.0:
            raise AdapterError("bad_response", "The grounding checker returned an invalid score.")
        return "Model works."

    def _stt(self, draft: CapabilityConfigUpdate | None) -> str:
        settings = self._registry.effective("stt", draft)
        audio = wav_bytes(1.0)
        if settings.mode == "endpoint":
            endpoint = self._registry.endpoint("stt", draft)
            build_stt_adapter(endpoint, self._http, timeout=PING_TIMEOUT_S).transcribe(
                audio, "silence.wav"
            )
            return "Endpoint works."
        transcriber = self._providers.transcriber()
        with tempfile.TemporaryDirectory(prefix="trustbook-stt-") as folder:
            path = Path(folder) / "silence.wav"
            path.write_bytes(audio)
            transcriber.transcribe(path)
        return "Model works."

    def _search(self, draft: CapabilityConfigUpdate | None) -> str:
        settings = self._registry.effective("web_search", draft)
        provider = settings.provider or "searxng"
        if provider != "searxng" and not settings.api_key:
            raise ConfigError("Add the API key first.")
        adapter = build_search_adapter(
            provider,
            settings.api_key,
            self._http,
            searxng_url=settings.base_url or self._searxng_url,
        )
        results = adapter.search("trustbook", 5)
        count = len(results)
        return f"Search works ({count} result{'' if count == 1 else 's'})."
