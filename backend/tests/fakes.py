"""Fake bundled providers and an httpx routing transport for tests (no network)."""

from __future__ import annotations

import json
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from app.capabilities.bundled import Transcript

Handler = Callable[[httpx.Request], httpx.Response]


class FakeEmbedder:
    dim = 4

    def __init__(self) -> None:
        self.calls = 0

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [[float(len(t) % 7), 0.5, 0.25, 1.0] for t in texts]


class FakeReranker:
    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        return [0.5 for _ in docs]


class FakeGrounding:
    """Support of ``(premise, hypothesis)``: 1.0 when the hypothesis is quoted from the premise
    (case and whitespace ignored, a trailing "…" allowed), else the share of its content words
    found in the premise. ``scores`` pins exact hypotheses to a score. Records every call."""

    def __init__(self, scores: dict[str, float] | None = None) -> None:
        self.scores = dict(scores or {})
        self.calls: list[list[tuple[str, str]]] = []

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.calls.append(list(pairs))
        return [self._one(p, h) for p, h in pairs]

    def _one(self, premise: str, hypothesis: str) -> float:
        from app.chat.text import content_terms

        if hypothesis in self.scores:
            return self.scores[hypothesis]
        quoted = " ".join(hypothesis.split()).casefold().rstrip("…").strip()
        if quoted and quoted in " ".join(premise.split()).casefold():
            return 1.0
        terms = content_terms(hypothesis)
        if not terms:
            return 0.0
        found = set(content_terms(premise))
        return sum(t in found for t in terms) / len(terms)


class FakeTranscriber:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def transcribe(self, wav_path: Path) -> Transcript:
        self.paths.append(wav_path)
        with wave.open(str(wav_path), "rb") as audio:
            duration = audio.getnframes() / float(audio.getframerate())
        return Transcript(text="", duration_s=duration)


class BrokenProvider:
    """Raises on every call (a model that fails at runtime)."""

    dim = 0

    def __getattr__(self, name: str) -> Any:
        def fail(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("boom")

        return fail


class Router:
    """A MockTransport that routes by method + URL prefix and records every request."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Handler]] = []
        self.requests: list[httpx.Request] = []

    def add(self, method: str, prefix: str, handler: Handler) -> Router:
        self.routes.append((method.upper(), prefix, handler))
        return self

    def json(self, method: str, prefix: str, data: Any, status: int = 200) -> Router:
        return self.add(method, prefix, lambda _r: httpx.Response(status, json=data))

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        url = str(request.url)
        for method, prefix, handler in self.routes:
            if request.method == method and url.startswith(prefix):
                return handler(request)
        return httpx.Response(404, json={"error": {"message": f"no route for {url}"}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def last_json(self) -> Any:
        return json.loads(self.requests[-1].content)


def chat_reply(text: str = "OK", **message: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": text, **message}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 1},
    }


def write_models_lock(models_dir: Path, *, present: bool = True) -> None:
    """A lock file with all four models; their folders exist when ``present``."""
    models: dict[str, Any] = {}
    for kind, model_id in (
        ("embeddings", "google/embeddinggemma-2"),
        ("reranker", "mixedbread-ai/mxbai-rerank-xsmall-v1"),
        ("grounding", "vectara/hallucination_evaluation_model"),
        ("stt", "Cactus-Compute/whistle"),
    ):
        models[kind] = {
            "model_id": model_id,
            "revision": f"rev-{kind}",
            "license": "Apache-2.0",
            "path": kind,
            "fallback": False,
        }
        if present:
            (models_dir / kind).mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "models.lock.json").write_text(json.dumps({"models": models}))
