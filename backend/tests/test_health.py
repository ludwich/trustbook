from __future__ import annotations

import httpx
import pytest

from app.capabilities.bundled import ProviderRegistry
from app.capabilities.health import HealthChecker
from app.capabilities.http import OutboundHttp, RequestLogRepository
from app.capabilities.registry import CapabilityRegistry, ConfigError
from app.capabilities.schemas import CapabilityConfigUpdate as Update
from app.config import Paths, SettingsStore
from tests.fakes import BrokenProvider, FakeTranscriber, Router, chat_reply

LLM = "http://llm.example:8080/v1"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.5
        return self.now


@pytest.fixture
def registry(paths: Paths, fake_providers: ProviderRegistry) -> CapabilityRegistry:
    return CapabilityRegistry(paths, SettingsStore(paths), fake_providers)


@pytest.fixture
def checker(
    registry: CapabilityRegistry, http: OutboundHttp, fake_providers: ProviderRegistry
) -> HealthChecker:
    return HealthChecker(
        registry, http, fake_providers, searxng_url="http://searxng:8080", clock=Clock()
    )


def _configure_chat(registry: CapabilityRegistry) -> None:
    registry.update("chat", Update(preset="custom", base_url=LLM, model="m", api_key="k" * 16))


def test_chat_ping_ok_and_saved(
    registry: CapabilityRegistry,
    checker: HealthChecker,
    router: Router,
    request_log: RequestLogRepository,
) -> None:
    _configure_chat(registry)
    router.json("POST", LLM, chat_reply("OK"))
    result = checker.test("chat")
    assert result.ok
    assert result.message == "Connected. The model replied in 0.5 s."
    assert result.latency_ms == 1500
    body = router.last_json()
    assert body["messages"] == [{"role": "user", "content": "Reply with OK"}]
    assert (body["temperature"], body["max_tokens"]) == (0, 256)
    assert registry.get("chat").last_test == result
    [row] = request_log.recent()
    assert row.capability == "chat"
    assert "Reply with OK" in row.excerpt


def test_reasoning_only_counts_as_ok(
    registry: CapabilityRegistry, checker: HealthChecker, router: Router
) -> None:
    _configure_chat(registry)
    router.json("POST", LLM, chat_reply("", reasoning_content="Let me think"))
    result = checker.test("chat")
    assert result.ok
    assert "reasoning only" in result.message


def test_empty_reply_fails(
    registry: CapabilityRegistry, checker: HealthChecker, router: Router
) -> None:
    _configure_chat(registry)
    router.json("POST", LLM, chat_reply(""))
    result = checker.test("chat")
    assert not result.ok
    assert result.message == "Connected, but the model sent an empty reply."
    assert registry.get("chat").status == "error"


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (
            lambda r: httpx.Response(401, json={"error": "bad key"}),
            "The API key was rejected (401).",
        ),
        (lambda r: httpx.Response(404, json={"error": "no model"}), "Not found (404)"),
    ],
)
def test_chat_errors(
    registry: CapabilityRegistry,
    checker: HealthChecker,
    router: Router,
    handler: object,
    message: str,
) -> None:
    _configure_chat(registry)
    router.add("POST", LLM, handler)  # type: ignore[arg-type]
    result = checker.test("chat")
    assert not result.ok
    assert result.message.startswith(message)


def test_chat_timeout(registry: CapabilityRegistry, checker: HealthChecker, router: Router) -> None:
    _configure_chat(registry)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    router.add("POST", LLM, slow)
    assert checker.test("chat").message == "No reply within 30 s."


def test_chat_unreachable(
    registry: CapabilityRegistry, checker: HealthChecker, router: Router
) -> None:
    _configure_chat(registry)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    router.add("POST", LLM, refused)
    message = checker.test("chat").message
    assert message.startswith("Cannot reach llm.example.")


def test_not_configured(checker: HealthChecker) -> None:
    result = checker.test("chat")
    assert (result.ok, result.message) == (False, "Choose a provider.")


def test_draft_is_not_saved_and_uses_saved_key(
    registry: CapabilityRegistry, checker: HealthChecker, router: Router
) -> None:
    _configure_chat(registry)
    router.json("POST", LLM, chat_reply("OK"))
    result = checker.test("chat", Update(model="draft-model"))
    assert result.ok
    assert router.last_json()["model"] == "draft-model"
    assert router.requests[-1].headers["authorization"] == "Bearer " + "k" * 16
    assert registry.get("chat").last_test is None
    assert registry.get("chat").config.model == "m"


def test_invalid_draft_raises(checker: HealthChecker) -> None:
    with pytest.raises(ConfigError):
        checker.test("chat", Update(preset="custom", base_url="nope"))


def test_mock_preset(registry: CapabilityRegistry, checker: HealthChecker, router: Router) -> None:
    result = checker.test("chat", Update(preset="mock"))
    assert result.ok
    assert router.requests == []


def test_gate_uses_chat(
    registry: CapabilityRegistry, checker: HealthChecker, router: Router
) -> None:
    _configure_chat(registry)
    router.json("POST", LLM, chat_reply("OK"))
    assert checker.test("gate").ok
    assert registry.get("gate").last_test is not None


def test_bundled_tests(checker: HealthChecker) -> None:
    assert checker.test("embeddings").message == "Model works (4 dimensions)."
    assert checker.test("reranker").message == "Model works."
    assert checker.test("grounding").message == "Model works."
    assert checker.test("stt").message == "Model works."


def test_bundled_missing(registry: CapabilityRegistry, http: OutboundHttp, paths: Paths) -> None:
    providers = ProviderRegistry(paths.models_dir, factories={})
    checker = HealthChecker(registry, http, providers, searxng_url="http://s")
    result = checker.test("embeddings")
    assert (result.ok, result.message) == (False, "Model files are missing from this install.")


def test_bundled_broken(registry: CapabilityRegistry, http: OutboundHttp, paths: Paths) -> None:
    providers = ProviderRegistry(paths.models_dir, factories={})
    providers.override(reranker=BrokenProvider())  # type: ignore[arg-type]
    checker = HealthChecker(registry, http, providers, searxng_url="http://s")
    assert checker.test("reranker").message == "The test failed unexpectedly (RuntimeError)."


def test_stt_bundled_uses_wav(checker: HealthChecker, fake_providers: ProviderRegistry) -> None:
    transcriber = fake_providers.transcriber()
    assert isinstance(transcriber, FakeTranscriber)
    checker.test("stt")
    assert transcriber.paths[-1].name == "silence.wav"


def test_stt_endpoint(registry: CapabilityRegistry, checker: HealthChecker, router: Router) -> None:
    router.json("POST", "http://localhost:9000/v1/audio/transcriptions", {"text": ""})
    result = checker.test(
        "stt",
        Update(mode="endpoint", preset="custom", base_url="http://localhost:9000", model="w"),
    )
    assert (result.ok, result.message) == (True, "Endpoint works.")
    content = router.requests[0].content
    assert b"RIFF" in content
    assert len(content) > 32_000  # 1 s of 16 kHz 16-bit audio


def test_search_tests(registry: CapabilityRegistry, checker: HealthChecker, router: Router) -> None:
    router.json("GET", "http://searxng:8080/search", {"results": [{"url": "https://a"}]})
    assert checker.test("web_search").message == "Search works (1 result)."
    assert router.requests[0].url.params["q"] == "trustbook"
    result = checker.test("web_search", Update(provider="brave"))
    assert (result.ok, result.message) == (False, "Add the API key first.")


def test_search_not_json(checker: HealthChecker, router: Router) -> None:
    router.add("GET", "http://searxng:8080/", lambda _r: httpx.Response(200, text="<html>"))
    assert not checker.test("web_search").ok


def test_tts(checker: HealthChecker) -> None:
    assert not checker.test("tts").ok


def test_list_models(registry: CapabilityRegistry, checker: HealthChecker, router: Router) -> None:
    router.json("GET", f"{LLM}/models", {"data": [{"id": "m2"}, {"id": "m1"}]})
    _configure_chat(registry)
    assert checker.list_models("chat") == ["m1", "m2"]
    assert checker.list_models("chat", Update(preset="mock")) == ["mock"]
    with pytest.raises(ConfigError, match="no model list"):
        checker.list_models("embeddings")
    with pytest.raises(ConfigError, match="Switch speech-to-text"):
        checker.list_models("stt")
    router.json("GET", "http://localhost:9000/v1/models", {"data": [{"id": "whisper"}]})
    draft = Update(mode="endpoint", preset="custom", base_url="http://localhost:9000", model="w")
    assert checker.list_models("stt", draft) == ["whisper"]
