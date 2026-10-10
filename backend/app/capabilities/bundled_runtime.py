"""MINIMAL real implementations of the bundled providers.

These classes are placeholders to be finalised after the model-verification task. They import
torch / sentence-transformers / transformers / needle lazily (the ``ml`` extra is not installed in
CI) and are excluded from unit tests. Keep them small and isolated.
"""

from __future__ import annotations

import math
import os
import threading
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.capabilities.bundled import ModelInfo, ProviderUnavailableError, Transcript


def _floats(values: Any) -> list[float]:
    return [float(v) for v in values]


def _unit_interval(values: list[float]) -> list[float]:
    """Map raw logits to 0..1 when a model returns values outside that range."""
    if all(0.0 <= v <= 1.0 for v in values):
        return values
    return [1.0 / (1.0 + math.exp(-v)) for v in values]


class SentenceTransformerEmbedder:
    """Embeddings via sentence-transformers on CPU in FP32 (never FP16, SPEC §2)."""

    def __init__(self, info: ModelInfo) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(
            str(info.path), device="cpu", model_kwargs={"torch_dtype": torch.float32}
        )
        self._lock = threading.Lock()

    @property
    def dim(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            vectors = self._model.encode_query(list(texts), normalize_embeddings=True)
        return [_floats(v) for v in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            vectors = self._model.encode_document(list(texts), normalize_embeddings=True)
        return [_floats(v) for v in vectors]


class CrossEncoderReranker:
    """Reranker via sentence-transformers ``CrossEncoder``; scores mapped to 0..1."""

    def __init__(self, info: ModelInfo) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(str(info.path), device="cpu")
        self._lock = threading.Lock()

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        if not docs:
            return []
        with self._lock:
            raw = self._model.predict([(query, doc) for doc in docs])
        return _unit_interval(_floats(raw))


class HHEMGroundingChecker:
    """HHEM-2.1-Open (``trust_remote_code``) or an NLI fallback (entailment probability)."""

    def __init__(self, info: ModelInfo) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._fallback = info.fallback
        self._model = AutoModelForSequenceClassification.from_pretrained(
            str(info.path), trust_remote_code=not info.fallback
        )
        self._model.eval()
        self._tokenizer = (
            None if not info.fallback else AutoTokenizer.from_pretrained(str(info.path))
        )
        self._lock = threading.Lock()

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        import torch

        with self._lock, torch.no_grad():
            if not self._fallback:
                return _unit_interval(_floats(self._model.predict(list(pairs))))
            assert self._tokenizer is not None
            premises = [p for p, _ in pairs]
            hypotheses = [h for _, h in pairs]
            batch = self._tokenizer(
                premises, hypotheses, return_tensors="pt", padding=True, truncation=True
            )
            probs = torch.softmax(self._model(**batch).logits, dim=-1)
            label_ids = {v.lower(): k for k, v in self._model.config.id2label.items()}
            entail = int(label_ids.get("entailment", 0))
            return _floats(probs[:, entail].tolist())


class WhistleTranscriber:
    """Whistle speech-to-text via ``cactus-needle`` with its telemetry switched off."""

    def __init__(self, info: ModelInfo) -> None:
        # cactus-needle sends anonymous usage events unless told not to. Trustbook has no
        # telemetry, so force it off before the package is imported.
        os.environ["NEEDLE_TELEMETRY"] = "0"
        os.environ["DO_NOT_TRACK"] = "1"
        import needle

        weights = info.path
        if weights.is_dir():
            candidates = sorted(weights.glob("*.cact"))
            if not candidates:
                raise ProviderUnavailableError("Speech-to-text model files are missing.")
            weights = candidates[0]
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
