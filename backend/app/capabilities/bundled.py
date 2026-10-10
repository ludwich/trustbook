"""Bundled CPU model providers (embeddings, reranker, grounding checker, speech-to-text).

Availability comes from ``<models_dir>/models.lock.json`` written at image build time::

    {"models": {"embeddings": {"model_id": ..., "revision": ..., "license": ...,
                               "path": ..., "fallback": false}, ...}}

The real implementations live in :mod:`app.capabilities.bundled_runtime` and import torch and
friends lazily. Tests inject fakes with :meth:`ProviderRegistry.override` or :func:`set_providers`.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

logger = logging.getLogger("trustbook.bundled")

BundledKind = Literal["embeddings", "reranker", "grounding", "stt"]
BUNDLED_KINDS: tuple[BundledKind, ...] = ("embeddings", "reranker", "grounding", "stt")


class ProviderUnavailableError(RuntimeError):
    """A bundled model cannot be used (files missing or runtime not installed)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    duration_s: float


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        """Relevance of each doc to ``query``, each in 0..1."""
        ...


class GroundingChecker(Protocol):
    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Support of each ``(premise, hypothesis)`` pair, each in 0..1."""
        ...


class Transcriber(Protocol):
    def transcribe(self, wav_path: Path) -> Transcript: ...


@dataclass(frozen=True)
class ModelInfo:
    """One bundled model as described by the lock file."""

    kind: BundledKind
    model_id: str
    revision: str
    license: str
    path: Path
    fallback: bool

    @property
    def present(self) -> bool:
        return self.path.exists()


_DEFAULTS: dict[BundledKind, tuple[str, str]] = {
    "embeddings": ("google/embeddinggemma-2", "914f7f89142e33e77833254d9c9b90c3cef7303b"),
    "reranker": (
        "mixedbread-ai/mxbai-rerank-xsmall-v1",
        "b5c6e9da73abc3711f593f705371cdbe9e0fe422",
    ),
    "grounding": (
        "vectara/hallucination_evaluation_model",
        "de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8",
    ),
    "stt": ("Cactus-Compute/whistle", "ca5287601bef25af26dcf2e1b2bdc0843a7c19e5"),
}


def default_model_info(kind: BundledKind, models_dir: Path) -> ModelInfo:
    """The SPEC default for ``kind``, used when the lock file has no entry."""
    model_id, revision = _DEFAULTS[kind]
    return ModelInfo(kind, model_id, revision, "Apache-2.0", models_dir / kind, False)


def read_models_lock(models_dir: Path) -> dict[BundledKind, ModelInfo]:
    """Read ``models.lock.json``; missing or broken entries fall back to SPEC defaults."""
    result = {kind: default_model_info(kind, models_dir) for kind in BUNDLED_KINDS}
    lock_path = models_dir / "models.lock.json"
    if not lock_path.is_file():
        return result
    try:
        raw: Any = json.loads(lock_path.read_text("utf-8"))
        models = raw.get("models", {}) if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        logger.warning("models.lock.json is unreadable; using defaults")
        return result
    for kind in BUNDLED_KINDS:
        entry = models.get(kind)
        if not isinstance(entry, dict):
            continue
        default = result[kind]
        path = Path(str(entry.get("path") or default.path))
        if not path.is_absolute():
            path = models_dir / path
        result[kind] = ModelInfo(
            kind=kind,
            model_id=str(entry.get("model_id") or default.model_id),
            revision=str(entry.get("revision") or default.revision),
            license=str(entry.get("license") or default.license),
            path=path,
            fallback=bool(entry.get("fallback", False)),
        )
    return result


ProviderFactory = Callable[[ModelInfo], Any]


def _default_factories() -> dict[BundledKind, ProviderFactory]:
    from app.capabilities import bundled_runtime

    return {
        "embeddings": bundled_runtime.load_embedder,
        "reranker": bundled_runtime.load_reranker,
        "grounding": bundled_runtime.load_grounding_checker,
        "stt": bundled_runtime.load_transcriber,
    }


# Python modules each bundled provider imports (the ``ml`` extra; absent in CI and dev installs).
_RUNTIME_MODULES: dict[BundledKind, tuple[str, ...]] = {
    "embeddings": ("torch", "sentence_transformers"),
    "reranker": ("torch", "sentence_transformers"),
    "grounding": ("torch", "transformers"),
    "stt": ("needle",),
}


def runtime_installed(kind: BundledKind) -> bool:
    """True when the Python packages ``kind`` needs are importable (without importing them)."""
    return all(importlib.util.find_spec(name) is not None for name in _RUNTIME_MODULES[kind])


class ProviderRegistry:
    """Lazily loads and caches bundled providers; supports injection for tests."""

    def __init__(
        self,
        models_dir: Path,
        factories: dict[BundledKind, ProviderFactory] | None = None,
        runtime_check: Callable[[BundledKind], bool] | None = None,
    ) -> None:
        self._models_dir = models_dir
        self._factories = factories
        # Injected factories bring their own runtime; otherwise look for the ml packages.
        self._runtime_check = runtime_check or (
            (lambda _kind: True) if factories is not None else runtime_installed
        )
        self._instances: dict[BundledKind, Any] = {}
        self._overrides: dict[BundledKind, Any] = {}
        self._lock = threading.Lock()

    @property
    def models_dir(self) -> Path:
        return self._models_dir

    def info(self, kind: BundledKind) -> ModelInfo:
        """Lock-file information for ``kind`` (re-read each call; the file is tiny)."""
        return read_models_lock(self._models_dir)[kind]

    def runtime_available(self, kind: BundledKind) -> bool:
        """Whether ``kind`` can run in this build (an injected provider always can)."""
        with self._lock:
            if kind in self._overrides or kind in self._instances:
                return True
        return self._runtime_check(kind)

    def override(
        self,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        grounding: GroundingChecker | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        """Inject ready-made providers (used by tests and calibration fakes)."""
        pairs: tuple[tuple[BundledKind, Any], ...] = (
            ("embeddings", embedder),
            ("reranker", reranker),
            ("grounding", grounding),
            ("stt", transcriber),
        )
        with self._lock:
            for kind, provider in pairs:
                if provider is not None:
                    self._overrides[kind] = provider

    def embedder(self) -> Embedder:
        return cast(Embedder, self._get("embeddings"))

    def reranker(self) -> Reranker:
        return cast(Reranker, self._get("reranker"))

    def grounding(self) -> GroundingChecker:
        return cast(GroundingChecker, self._get("grounding"))

    def transcriber(self) -> Transcriber:
        return cast(Transcriber, self._get("stt"))

    def _get(self, kind: BundledKind) -> Any:
        with self._lock:
            if kind in self._overrides:
                return self._overrides[kind]
            if kind in self._instances:
                return self._instances[kind]
        info = self.info(kind)
        if not info.present:
            raise ProviderUnavailableError("Model files are missing from this install.")
        factories = self._factories if self._factories is not None else _default_factories()
        try:
            instance = factories[kind](info)
        except ProviderUnavailableError:
            raise
        except ImportError as exc:
            raise ProviderUnavailableError(
                "The model runtime is not installed in this build."
            ) from exc
        with self._lock:
            return self._instances.setdefault(kind, instance)


_registry: ProviderRegistry | None = None


def set_providers(registry: ProviderRegistry | None) -> None:
    """Install the process-wide provider registry (``None`` resets it)."""
    global _registry
    _registry = registry


def get_providers() -> ProviderRegistry:
    """Return the process-wide provider registry."""
    if _registry is None:
        raise RuntimeError("Bundled providers are not initialised")
    return _registry
