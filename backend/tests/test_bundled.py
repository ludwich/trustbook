from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.bundled import (
    ModelInfo,
    ProviderRegistry,
    ProviderUnavailableError,
    get_providers,
    read_models_lock,
    set_providers,
)
from tests.fakes import FakeEmbedder, write_models_lock


def test_defaults_without_lock(tmp_path: Path) -> None:
    info = read_models_lock(tmp_path)
    assert info["embeddings"].model_id == "google/embeddinggemma-2"
    assert info["embeddings"].revision == "914f7f89142e33e77833254d9c9b90c3cef7303b"
    assert info["reranker"].model_id == "mixedbread-ai/mxbai-rerank-xsmall-v1"
    assert info["grounding"].model_id == "vectara/hallucination_evaluation_model"
    assert info["stt"].model_id == "Cactus-Compute/whistle"
    assert not any(i.present for i in info.values())


def test_lock_entries(tmp_path: Path) -> None:
    write_models_lock(tmp_path)
    info = read_models_lock(tmp_path)
    assert info["reranker"].revision == "rev-reranker"
    assert info["reranker"].path == tmp_path / "reranker"
    assert info["reranker"].present


def test_absolute_path_and_fallback(tmp_path: Path) -> None:
    model_dir = tmp_path / "elsewhere"
    model_dir.mkdir()
    lock = {
        "models": {
            "reranker": {
                "model_id": "BAAI/bge-reranker-base",
                "revision": "abc",
                "license": "MIT",
                "path": str(model_dir),
                "fallback": True,
            },
            "grounding": "garbage",
        }
    }
    (tmp_path / "models.lock.json").write_text(json.dumps(lock))
    info = read_models_lock(tmp_path)
    assert info["reranker"] == ModelInfo(
        "reranker", "BAAI/bge-reranker-base", "abc", "MIT", model_dir, True
    )
    assert info["grounding"].model_id == "vectara/hallucination_evaluation_model"


def test_broken_lock_uses_defaults(tmp_path: Path) -> None:
    (tmp_path / "models.lock.json").write_text("{")
    assert read_models_lock(tmp_path)["stt"].model_id == "Cactus-Compute/whistle"


def test_override_wins(tmp_path: Path) -> None:
    registry = ProviderRegistry(tmp_path, factories={})
    fake = FakeEmbedder()
    registry.override(embedder=fake)
    assert registry.embedder() is fake


def test_missing_files_unavailable(tmp_path: Path) -> None:
    registry = ProviderRegistry(tmp_path, factories={})
    with pytest.raises(ProviderUnavailableError, match="missing"):
        registry.reranker()


def test_factory_used_once(tmp_path: Path) -> None:
    write_models_lock(tmp_path)
    calls: list[ModelInfo] = []

    def factory(info: ModelInfo) -> Any:
        calls.append(info)
        return FakeEmbedder()

    registry = ProviderRegistry(tmp_path, factories={"embeddings": factory})
    first = registry.embedder()
    assert registry.embedder() is first
    assert len(calls) == 1
    assert calls[0].model_id == "google/embeddinggemma-2"


def test_import_error_unavailable(tmp_path: Path) -> None:
    write_models_lock(tmp_path)

    def factory(_info: ModelInfo) -> Any:
        raise ImportError("no torch")

    registry = ProviderRegistry(tmp_path, factories={"grounding": factory})
    with pytest.raises(ProviderUnavailableError, match="not installed"):
        registry.grounding()


def test_set_and_get_providers(tmp_path: Path) -> None:
    registry = ProviderRegistry(tmp_path)
    set_providers(registry)
    try:
        assert get_providers() is registry
    finally:
        set_providers(None)
    with pytest.raises(RuntimeError):
        get_providers()
