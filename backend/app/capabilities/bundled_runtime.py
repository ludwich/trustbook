"""Real implementations of the bundled CPU providers.

Imports torch / sentence-transformers / transformers / needle lazily (the ``ml`` extra is not
installed in CI), so these classes are excluded from unit tests. They are exercised by
``python -m app.capabilities.bundled_check`` at image build time instead.

Model folders are written by ``docker/download_models.py`` (see docs/DECISIONS.md D-008, D-010):

    <models_dir>/embeddings/             google/embeddinggemma-2 (or the bge-small fallback)
    <models_dir>/reranker/               mxbai-rerank-xsmall-v1 (or bge-reranker-base)
    <models_dir>/grounding/              HHEM-2.1-Open, with flan-t5-base/ (tokenizer + config only)
    <models_dir>/stt/                    whistle.cact + libneedle3.so (the Needle engine)
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.capabilities.bundled import ModelInfo, ProviderUnavailableError, Transcript

# EmbeddingGemma 2 prompt names (model card); the bge fallback uses its own query instruction.
_GEMMA_QUERY_PROMPT = "QuestionAnswering"
_GEMMA_DOCUMENT_PROMPT = "Document"
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_EMBED_MAX_TOKENS = 2048
# HHEM does not truncate; cited context is capped at ~6,000 tokens (SPEC §8), so this is a guard.
_GROUNDING_MAX_PREMISE_CHARS = 32_000


def _offline_env() -> None:
    """Never reach the network and never send telemetry from bundled models (SPEC §11)."""
    for key, value in {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "NEEDLE_TELEMETRY": "0",
        "DO_NOT_TRACK": "1",
    }.items():
        os.environ[key] = value
    # trust_remote_code copies HHEM's model code into a modules cache; the models dir is read-only.
    os.environ.setdefault(
        "HF_MODULES_CACHE", str(Path(tempfile.gettempdir()) / "trustbook-hf-modules")
    )


def _floats(values: Any) -> list[float]:
    return [float(v) for v in values]


def _unit_interval(values: list[float]) -> list[float]:
    """Guard: map raw logits to 0..1 if a model ever returns values outside that range."""
    if all(0.0 <= v <= 1.0 for v in values):
        return values
    return [1.0 / (1.0 + math.exp(-v)) for v in values]


class SentenceTransformerEmbedder:
    """Embeddings via sentence-transformers on CPU in FP32 (never FP16, SPEC §2)."""

    def __init__(self, info: ModelInfo) -> None:
        _offline_env()
        import torch
        from sentence_transformers import SentenceTransformer

        self._gemma = not info.fallback
        config_kwargs = {"vision_config": None, "audio_config": None} if self._gemma else {}
        self._model = SentenceTransformer(
            str(info.path),
            device="cpu",
            local_files_only=True,
            model_kwargs={"dtype": torch.float32},
            config_kwargs=config_kwargs,
        )
        self._model.max_seq_length = _EMBED_MAX_TOKENS
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        dim = self._model.get_embedding_dimension()
        if dim is None:
            raise ProviderUnavailableError("The embedding model does not report its size.")
        return int(dim)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            if self._gemma:
                vectors = self._model.encode(
                    list(texts), prompt_name=_GEMMA_QUERY_PROMPT, normalize_embeddings=True
                )
            else:
                vectors = self._model.encode(
                    [_BGE_QUERY_PREFIX + t for t in texts], normalize_embeddings=True
                )
        return [_floats(v) for v in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            if self._gemma:
                vectors = self._model.encode(
                    list(texts),
                    prompt_name=_GEMMA_DOCUMENT_PROMPT,
                    batch_size=8,
                    normalize_embeddings=True,
                )
            else:
                vectors = self._model.encode(list(texts), batch_size=8, normalize_embeddings=True)
        return [_floats(v) for v in vectors]


class CrossEncoderReranker:
    """Reranker via sentence-transformers ``CrossEncoder``; it already applies a sigmoid (0..1)."""

    def __init__(self, info: ModelInfo) -> None:
        _offline_env()
        import torch
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(
            str(info.path),
            device="cpu",
            local_files_only=True,
            model_kwargs={"dtype": torch.float32},
        )
        self._lock = threading.Lock()

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        if not docs:
            return []
        with self._lock:
            raw = self._model.predict([(query, doc) for doc in docs], batch_size=16)
        return _unit_interval(_floats(raw))


class HHEMGroundingChecker:
    """HHEM-2.1-Open (``trust_remote_code`` from the pinned local copy) or the NLI fallback."""

    def __init__(self, info: ModelInfo) -> None:
        _offline_env()
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._fallback = info.fallback
        self._tokenizer: Any = None
        if self._fallback:
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(info.path), local_files_only=True, dtype=torch.float32
            )
            self._tokenizer = AutoTokenizer.from_pretrained(str(info.path), local_files_only=True)
        else:
            foundation = info.path / "flan-t5-base"
            if not foundation.is_dir():
                raise ProviderUnavailableError("The answer checker's tokenizer files are missing.")
            # Without ``foundation`` the remote code would fetch flan-t5-base unpinned from the hub.
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(info.path),
                local_files_only=True,
                trust_remote_code=True,
                dtype=torch.float32,
                foundation=str(foundation),
            )
        self._model.eval()
        self._lock = threading.Lock()

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        import torch

        capped = [(p[:_GROUNDING_MAX_PREMISE_CHARS], h) for p, h in pairs]
        with self._lock, torch.no_grad():
            if not self._fallback:
                # predict() = softmax(logits)[:, 1]; label 1 means "consistent".
                return _unit_interval(_floats(self._model.predict(capped)))
            premises = [p for p, _ in capped]
            hypotheses = [h for _, h in capped]
            batch = self._tokenizer(
                premises, hypotheses, return_tensors="pt", padding=True, truncation=True
            )
            probs = torch.softmax(self._model(**batch).logits, dim=-1)
            label_ids = {str(v).lower(): int(k) for k, v in self._model.config.id2label.items()}
            entail = label_ids.get("entailment", 1)
            return _floats(probs[:, entail].tolist())


class WhistleTranscriber:
    """Whistle speech-to-text via the pinned Needle engine, offline, telemetry off (D-008)."""

    def __init__(self, info: ModelInfo) -> None:
        _offline_env()
        folder = info.path if info.path.is_dir() else info.path.parent
        weights = folder / "whistle.cact"
        engine = folder / "libneedle3.so"
        if not weights.is_file() or not engine.is_file():
            raise ProviderUnavailableError("Speech-to-text model files are missing.")
        # Without an explicit engine path, cactus-needle downloads one from the hub.
        os.environ["NEEDLE3_LIB_PATH"] = str(engine)
        import needle

        # The class API never calls needle's telemetry; the module-level helpers do.
        self._model = needle.Whistle(weights=str(weights))
        self._lock = threading.Lock()  # the engine is not thread-safe

    def transcribe(self, wav_path: Path) -> Transcript:
        with wave.open(str(wav_path), "rb") as source:
            duration = source.getnframes() / float(source.getframerate() or 1)
        with self._lock:
            result = self._model.transcribe(str(wav_path), language="en")
        return Transcript(text=str(result.get("text") or "").strip(), duration_s=duration)


def load_embedder(info: ModelInfo) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(info)


def load_reranker(info: ModelInfo) -> CrossEncoderReranker:
    return CrossEncoderReranker(info)


def load_grounding_checker(info: ModelInfo) -> HHEMGroundingChecker:
    return HHEMGroundingChecker(info)


def load_transcriber(info: ModelInfo) -> WhistleTranscriber:
    return WhistleTranscriber(info)
