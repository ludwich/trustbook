from __future__ import annotations

from dataclasses import replace

import pytest

from app.capabilities.bundled import ProviderRegistry
from app.capabilities.manifest import load_manifests
from app.capabilities.registry import (
    CapabilityNotFoundError,
    CapabilityRegistry,
    ConfigError,
    locality_for_url,
    normalise_base_url,
)
from app.capabilities.schemas import CapabilityConfigUpdate as Update
from app.config import CAPABILITY_IDS, Paths, SettingsStore, TestResult
from tests.fakes import write_models_lock


def make_registry(paths: Paths) -> CapabilityRegistry:
    return CapabilityRegistry(paths, SettingsStore(paths), ProviderRegistry(paths.models_dir))


@pytest.fixture
def registry(paths: Paths) -> CapabilityRegistry:
    return make_registry(paths)


def test_manifests_load() -> None:
    manifests = load_manifests()
    assert set(manifests) == set(CAPABILITY_IDS)
    assert manifests["gate"].presets == manifests["chat"].presets
    assert manifests["tts"].hidden


def test_default_views(registry: CapabilityRegistry) -> None:
    caps = {c.id: c for c in registry.list_all()}
    assert list(caps) == list(CAPABILITY_IDS)
    chat = caps["chat"]
    assert (chat.status, chat.status_detail, chat.locality) == (
        "not_configured",
        "Choose a provider.",
        None,
    )
    assert [f.name for f in chat.fields] == [
        "preset",
        "base_url",
        "api_key",
        "model",
        "api_version",
    ]
    preset_field = chat.fields[0]
    assert preset_field.options is not None
    assert [o.value for o in preset_field.options] == [p.id for p in chat.presets]
    assert [f.name for f in caps["gate"].fields] == [
        "use_chat",
        "preset",
        "base_url",
        "api_key",
        "model",
        "api_version",
    ]
    assert [f.name for f in caps["stt"].fields] == ["mode", "base_url", "api_key", "model"]
    assert [f.name for f in caps["web_search"].fields] == ["provider", "base_url", "api_key"]
    assert [f.name for f in caps["grounding"].fields] == ["enabled"]
    assert caps["embeddings"].fields == []
    assert caps["gate"].status == "not_configured"
    assert caps["gate"].config.use_chat is True
    assert caps["embeddings"].status == "error"
    assert caps["embeddings"].status_detail == "Model files are missing from this install."
    assert caps["embeddings"].locality == "local"
    assert caps["embeddings"].bundled is not None
    assert caps["embeddings"].bundled.present is False
    assert caps["web_search"].status == "ready"
    assert caps["web_search"].locality == "local"
    assert caps["web_search"].config.provider == "searxng"
    assert caps["web_search"].bundled is None
    assert caps["tts"].status == "disabled"
    assert caps["tts"].hidden
    assert caps["stt"].config.mode == "bundled"
    assert caps["chat"].config.mode is None


def test_bundled_ready_when_present(paths: Paths) -> None:
    write_models_lock(paths.models_dir)
    registry = make_registry(paths)
    embeddings = registry.get("embeddings")
    assert embeddings.status == "ready"
    assert embeddings.status_detail == "Runs on this computer (google/embeddinggemma-2)."
    assert registry.get("stt").status == "ready"


def test_mock_preset_only_with_flag(paths: Paths) -> None:
    with_mock = [p.id for p in make_registry(paths).get("chat").presets]
    without = [p.id for p in make_registry(replace(paths, mock_llm=False)).get("chat").presets]
    assert "mock" in with_mock
    assert "mock" not in without
    assert without == [
        "openai",
        "azure_openai",
        "anthropic",
        "ollama",
        "llama_server",
        "lm_studio",
        "vllm",
        "custom",
    ]
    with pytest.raises(ConfigError):
        make_registry(replace(paths, mock_llm=False)).update("chat", Update(preset="mock"))


def test_mock_preset_ready(registry: CapabilityRegistry) -> None:
    chat = registry.update("chat", Update(preset="mock"))
    assert (chat.status, chat.locality) == ("ready", "local")
    assert chat.config.base_url is None and chat.config.model is None
    assert registry.endpoint("chat").adapter == "mock"


def test_preset_fills_base_url(registry: CapabilityRegistry) -> None:
    chat = registry.update("chat", Update(preset="llama_server"))
    assert chat.config.base_url == "http://host.docker.internal:8080/v1"
    assert chat.status == "not_configured"
    assert chat.status_detail == "Add the model name."
    chat = registry.update("chat", Update(model="qwen3"))
    assert (chat.status, chat.locality) == ("ready", "local")
    assert chat.status_detail == "Ready. Uses qwen3 at host.docker.internal."
    assert next(f for f in chat.fields if f.name == "api_key").required is False


def test_openai_needs_key(registry: CapabilityRegistry) -> None:
    chat = registry.update("chat", Update(preset="openai", model="gpt"))
    assert chat.status_detail == "Add the API key."
    chat = registry.update("chat", Update(api_key="sk-abcdefgh1234"))
    assert chat.status == "ready"
    assert chat.locality == "remote"
    assert chat.config.api_key_set
    assert chat.config.api_key_hint == "…1234"
    assert "sk-abcdefgh1234" not in chat.model_dump_json()
    chat = registry.update("chat", Update(api_key=""))
    assert not chat.config.api_key_set
    assert chat.status == "not_configured"


def test_azure_fields_and_missing(registry: CapabilityRegistry) -> None:
    chat = registry.update("chat", Update(preset="azure_openai"))
    assert next(f for f in chat.fields if f.name == "api_key").required is True
    assert chat.status_detail == "Add the Azure endpoint, API key, deployment name and API version."
    chat = registry.update(
        "chat",
        Update(
            base_url="https://res.openai.azure.com/openai/deployments/x/chat?api-version=1",
            api_key="azure-key-1234567",
            model="dep",
            api_version="2024-10-21",
        ),
    )
    assert chat.config.base_url == "https://res.openai.azure.com"
    assert chat.status == "ready"


@pytest.mark.parametrize(
    ("url", "adapter", "expected"),
    [
        ("http://host:8080", "openai_compatible", "http://host:8080/v1"),
        ("http://host:8080/", "openai_compatible", "http://host:8080/v1"),
        ("http://host:8080/v1/", "openai_compatible", "http://host:8080/v1"),
        ("https://proxy/openai/v1", "openai_compatible", "https://proxy/openai/v1"),
        ("https://api.anthropic.com/v1/", "anthropic", "https://api.anthropic.com"),
        ("https://r.openai.azure.com/", "azure_openai", "https://r.openai.azure.com"),
        ("  http://h/v1?x=1#f ", "openai_compatible", "http://h/v1"),
    ],
)
def test_normalise_base_url(url: str, adapter: str, expected: str) -> None:
    assert normalise_base_url(url, adapter) == expected


@pytest.mark.parametrize("url", ["host:8080", "ftp://h", "http://", "nonsense"])
def test_normalise_rejects(url: str) -> None:
    with pytest.raises(ConfigError, match="http://"):
        normalise_base_url(url, "openai_compatible")


@pytest.mark.parametrize(
    ("url", "locality"),
    [
        ("http://localhost:1/v1", "local"),
        ("http://127.0.0.1/v1", "local"),
        ("http://[::1]:8080/v1", "local"),
        ("http://host.docker.internal:8080/v1", "local"),
        ("http://192.168.1.5:8080/v1", "remote"),
        ("https://api.openai.com/v1", "remote"),
        (None, None),
        ("not a url", None),
    ],
)
def test_locality(url: str | None, locality: str | None) -> None:
    assert locality_for_url(url) == locality


def test_missing_key_keeps_saved_key(registry: CapabilityRegistry) -> None:
    registry.update("chat", Update(preset="custom", base_url="http://a.example/v1", model="m"))
    registry.update("chat", Update(api_key="secret-key-123456"))
    chat = registry.update("chat", Update(base_url="http://b.example/v1"))
    assert chat.config.api_key_hint == "…3456"
    chat = registry.update("chat", Update(api_key=None, model="m2"))
    assert chat.config.api_key_set
    chat = registry.update("chat", Update(api_key="other-key-1234"))
    assert chat.config.api_key_hint == "…1234"
    assert not registry.update("chat", Update(api_key="")).config.api_key_set


def test_web_search_providers(registry: CapabilityRegistry) -> None:
    search = registry.update("web_search", Update(provider="brave"))
    assert (search.status, search.status_detail) == (
        "not_configured",
        "Add your Brave Search API key.",
    )
    search = registry.update("web_search", Update(api_key="brave-key-123456"))
    assert (search.status, search.locality) == ("ready", "remote")
    with pytest.raises(ConfigError):
        registry.update("web_search", Update(provider="google"))
    search = registry.update(
        "web_search", Update(provider="searxng", base_url="http://localhost:8888/")
    )
    assert search.config.base_url == "http://localhost:8888"
    assert (search.status, search.locality) == ("ready", "local")
    assert search.status_detail.startswith("Uses SearXNG at localhost.")
    base_field = next(f for f in search.fields if f.name == "base_url")
    assert base_field.required is False
    assert base_field.placeholder == "http://searxng:8080"


def test_irrelevant_fields_rejected(registry: CapabilityRegistry) -> None:
    with pytest.raises(ConfigError, match="does not apply"):
        registry.update("chat", Update(provider="brave"))
    with pytest.raises(ConfigError, match="does not apply"):
        registry.update("embeddings", Update(enabled=False))
    # Irrelevant nulls are ignored.
    registry.update("chat", Update(provider=None, preset="mock"))
    with pytest.raises(ConfigError):
        registry.update("chat", Update(preset="nope"))
    with pytest.raises(ConfigError):
        registry.update("stt", Update(mode="cloud"))


def test_gate_mirrors_chat(registry: CapabilityRegistry) -> None:
    gate = registry.get("gate")
    assert gate.status_detail == "Uses the chat model, which is not set up yet."
    registry.update("chat", Update(preset="ollama", model="llama3"))
    gate = registry.get("gate")
    assert (gate.status, gate.locality) == ("ready", "local")
    assert gate.status_detail.startswith("Uses the chat model. Ready.")
    endpoint = registry.endpoint("gate")
    assert (endpoint.capability, endpoint.model) == ("gate", "llama3")
    gate = registry.update("gate", Update(use_chat=False))
    assert (gate.status, gate.status_detail) == ("not_configured", "Choose a provider.")


def test_grounding_toggle(registry: CapabilityRegistry) -> None:
    grounding = registry.get("grounding")
    assert [f.name for f in grounding.fields] == ["enabled"]
    assert grounding.fields[0].label == "Check every sentence against its sources"
    grounding = registry.update("grounding", Update(enabled=False))
    assert grounding.status == "disabled"
    assert grounding.status_detail == "Off. Answers are marked Unverified."


def test_stt_endpoint_mode(registry: CapabilityRegistry) -> None:
    stt = registry.update("stt", Update(mode="endpoint"))
    assert (stt.status, stt.status_detail) == ("not_configured", "Add the address and model name.")
    assert [p.id for p in stt.presets] == ["openai", "custom"]
    stt = registry.update("stt", Update(base_url="http://localhost:9000", model="w"))
    assert stt.config.base_url == "http://localhost:9000/v1"
    assert (stt.status, stt.locality) == ("ready", "local")
    assert registry.endpoint("stt").adapter == "openai_compatible"
    model_field = next(f for f in stt.fields if f.name == "model")
    assert model_field.placeholder == "whisper-1"
    # An explicit preset is still accepted and can require a key.
    stt = registry.update("stt", Update(preset="openai"))
    assert stt.status_detail == "Add the API key."


def test_failed_test_sets_error_and_update_clears_it(registry: CapabilityRegistry) -> None:
    registry.update("chat", Update(preset="mock"))
    registry.save_test("chat", TestResult(ok=False, message="Cannot reach x."))
    chat = registry.get("chat")
    assert (chat.status, chat.status_detail) == ("error", "Last test failed: Cannot reach x.")
    assert chat.last_test is not None
    # Saving the same config keeps the result; changing it clears it.
    assert registry.update("chat", Update(preset="mock")).last_test is not None
    chat = registry.update("chat", Update(preset="ollama", model="m"))
    assert chat.last_test is None
    assert chat.status == "ready"


def test_endpoint_resolution_with_draft(registry: CapabilityRegistry) -> None:
    registry.update(
        "chat", Update(preset="custom", base_url="http://a.example/v1", model="m", api_key="k" * 16)
    )
    endpoint = registry.endpoint("chat", Update(model="other"))
    assert (endpoint.model, endpoint.api_key) == ("other", "k" * 16)
    moved = registry.endpoint("chat", Update(base_url="http://b.example"))
    assert moved.api_key == "k" * 16  # missing api_key means "use the saved key"
    assert "k" * 16 not in repr(endpoint)
    with pytest.raises(ConfigError, match="model name"):
        registry.endpoint("chat", Update(model=""))
    with pytest.raises(ConfigError, match="does not use an endpoint"):
        registry.endpoint("embeddings")


def test_unknown_capability(registry: CapabilityRegistry) -> None:
    with pytest.raises(CapabilityNotFoundError):
        registry.get("nope")
