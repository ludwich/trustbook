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

import gc
import math
import os
import tempfile
import threading
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.capabilities.bundled import (
    ModelInfo,
    ProviderRegistry,
    ProviderUnavailableError,
    RuntimeOptions,
    Transcript,
    TuneResult,
    default_threads,
    streams_that_fit,
)
from app.capabilities.cpu import CpuInfo
from app.capabilities.streams import (
    EMBED_BATCH,
    StreamLayout,
    choose_streams,
    embed_in_streams,
    stream_candidates,
    synthetic_code_chunks,
)

# EmbeddingGemma 2 prompt names (model card); the bge fallback uses its own query instruction.
_GEMMA_QUERY_PROMPT = "QuestionAnswering"
_GEMMA_DOCUMENT_PROMPT = "Document"
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_EMBED_MAX_TOKENS = 2048
# HHEM does not truncate; cited context is capped at ~6,000 tokens (SPEC §8), so this is a guard.
_GROUNDING_MAX_PREMISE_CHARS = 32_000
# Building a model is not thread-safe: transformers switches torch's process-wide default dtype
# while it creates the weights, so two loads at once (e.g. indexing-stream replicas, or a BF16
# reranker next to an FP32 checker) can end up with mixed BF16/FP32 layers. Loads take turns.
_LOAD_LOCK = threading.Lock()


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


def _torch_dtype(name: str) -> Any:
    import torch

    return torch.bfloat16 if name == "bfloat16" else torch.float32


class _Threads:
    """Applies the model thread count in whichever thread calls the model.

    Jobs call models from worker threads. OpenMP and MKL keep their thread settings per calling
    thread, so a count set once at import does not reliably reach them (DECISIONS D-020).
    """

    def __init__(self, options: RuntimeOptions) -> None:
        self.count = options.torch_threads or default_threads()

    def apply(self) -> None:
        import torch

        if torch.get_num_threads() != self.count:
            torch.set_num_threads(self.count)


class SentenceTransformerEmbedder:
    """Embeddings via sentence-transformers on CPU in FP32 (never FP16, SPEC §2)."""

    def __init__(self, info: ModelInfo, options: RuntimeOptions, dtype: str | None = None) -> None:
        _offline_env()
        from sentence_transformers import SentenceTransformer

        self._threads = _Threads(options)
        self._threads.apply()
        self.dtype = dtype or options.embedding_dtype
        self._gemma = not info.fallback
        config_kwargs = {"vision_config": None, "audio_config": None} if self._gemma else {}
        with _LOAD_LOCK:
            self._model = SentenceTransformer(
                str(info.path),
                device="cpu",
                local_files_only=True,
                model_kwargs={"dtype": _torch_dtype(self.dtype)},
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
            self._threads.apply()
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
            self._threads.apply()
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

    def __init__(self, info: ModelInfo, options: RuntimeOptions, dtype: str | None = None) -> None:
        _offline_env()
        from sentence_transformers import CrossEncoder

        self._threads = _Threads(options)
        self._threads.apply()
        self.dtype = dtype or options.reranker_dtype
        with _LOAD_LOCK:
            self._model = CrossEncoder(
                str(info.path),
                device="cpu",
                local_files_only=True,
                model_kwargs={"dtype": _torch_dtype(self.dtype)},
            )
        self._lock = threading.Lock()

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        if not docs:
            return []
        with self._lock:
            self._threads.apply()
            raw = self._model.predict([(query, doc) for doc in docs], batch_size=16)
        return _unit_interval(_floats(raw))


class HHEMGroundingChecker:
    """HHEM-2.1-Open (``trust_remote_code`` from the pinned local copy) or the NLI fallback."""

    def __init__(self, info: ModelInfo, options: RuntimeOptions) -> None:
        _offline_env()
        self._threads = _Threads(options)
        self._threads.apply()
        self._fallback = info.fallback
        self._tokenizer: Any = None
        with _LOAD_LOCK:
            self._model: Any = self._load(info)
        self._model.eval()
        self._lock = threading.Lock()

    def _load(self, info: ModelInfo) -> Any:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if self._fallback:
            self._tokenizer = AutoTokenizer.from_pretrained(str(info.path), local_files_only=True)
            return AutoModelForSequenceClassification.from_pretrained(
                str(info.path), local_files_only=True, dtype=torch.float32
            )
        foundation = info.path / "flan-t5-base"
        if not foundation.is_dir():
            raise ProviderUnavailableError("The answer checker's tokenizer files are missing.")
        # Without ``foundation`` the remote code would fetch flan-t5-base unpinned from the hub.
        return AutoModelForSequenceClassification.from_pretrained(
            str(info.path),
            local_files_only=True,
            trust_remote_code=True,
            dtype=torch.float32,
            foundation=str(foundation),
        )

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        import torch

        capped = [(p[:_GROUNDING_MAX_PREMISE_CHARS], h) for p, h in pairs]
        with self._lock, torch.no_grad():
            self._threads.apply()
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
    """Whistle speech-to-text via the pinned Needle engine, offline, telemetry off (D-008).

    The engine picks its own thread count (D-019); ``options`` is accepted for a uniform API.
    """

    def __init__(self, info: ModelInfo, options: RuntimeOptions) -> None:
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


def load_embedder(info: ModelInfo, options: RuntimeOptions) -> SentenceTransformerEmbedder:
    return SentenceTransformerEmbedder(info, options)


def load_reranker(info: ModelInfo, options: RuntimeOptions) -> CrossEncoderReranker:
    return CrossEncoderReranker(info, options)


def load_grounding_checker(info: ModelInfo, options: RuntimeOptions) -> HHEMGroundingChecker:
    return HHEMGroundingChecker(info, options)


def load_transcriber(info: ModelInfo, options: RuntimeOptions) -> WhistleTranscriber:
    return WhistleTranscriber(info, options)


# --- calibration: choose threads and dtypes by measurement (DECISIONS D-020) ------------------

# BF16 is used only when it is clearly faster and gives the same results as FP32.
BF16_MIN_SPEEDUP = 1.25
BF16_MIN_COSINE = 0.99  # every embedding vs its FP32 twin
BF16_MAX_SCORE_DIFF = 0.03  # every reranker score vs FP32, on the 0..1 scale
STREAM_TUNE_CHUNKS = 32  # code chunks per stream layout (more when there are many streams)


def _rate(action: Any, items: int, repeats: int = 2) -> float:
    """Items per second, best of ``repeats`` runs (the first run also warms caches)."""
    best = math.inf
    for _ in range(repeats):
        started = time.perf_counter()
        action()
        best = min(best, time.perf_counter() - started)
    return items / max(best, 1e-6)


def thread_candidates(cpu: CpuInfo) -> list[int]:
    """Physical cores, all usable CPUs and half of them (deduplicated, ascending)."""
    options = {cpu.usable, max(1, cpu.usable // 2)}
    if cpu.physical:
        options.add(cpu.physical)
    return sorted(n for n in options if 1 <= n <= cpu.usable)


def _measure_embedder(
    info: ModelInfo, options: RuntimeOptions, dtype: str, texts: list[str]
) -> tuple[float, list[list[float]]]:
    """Load, warm up and time one embedder variant; it is freed when this returns."""
    model = SentenceTransformerEmbedder(info, options, dtype)
    model.embed_documents(texts[:2])
    rate = _rate(lambda: model.embed_documents(texts), len(texts))
    return rate, model.embed_documents(texts)


def _measure_reranker(
    info: ModelInfo, options: RuntimeOptions, dtype: str, query: str, texts: list[str]
) -> tuple[float, list[float]]:
    model = CrossEncoderReranker(info, options, dtype)
    model.score(query, texts[:2])
    rate = _rate(lambda: model.score(query, texts), len(texts))
    return rate, model.score(query, texts)


def _thread_rates(info: ModelInfo, cpu: CpuInfo, texts: list[str]) -> dict[int, float]:
    model = SentenceTransformerEmbedder(info, RuntimeOptions(), "float32")
    model.embed_documents(texts[:2])
    rates: dict[int, float] = {}
    for threads in thread_candidates(cpu):
        model._threads.count = threads
        rates[threads] = _rate(lambda: model.embed_documents(texts), len(texts))
    return rates


def _stream_rate(
    info: ModelInfo,
    options: RuntimeOptions,
    replicas: list[SentenceTransformerEmbedder],
    layout: StreamLayout,
) -> float:
    """Chunks/s of indexing ~700-token code chunks with ``layout`` (replicas are reused)."""
    while len(replicas) < layout.streams:
        replicas.append(SentenceTransformerEmbedder(info, options, options.embedding_dtype))
    for replica in replicas:
        replica._threads.count = layout.threads
    # Equal-length chunks: one batch per stream already keeps every stream busy to the end.
    texts = synthetic_code_chunks(max(STREAM_TUNE_CHUNKS, layout.streams * EMBED_BATCH))

    def run(sample: list[str], batch: int) -> None:
        embed_in_streams(
            sample,
            embedder_for=lambda i: replicas[i],
            streams=layout.streams,
            on_batch=lambda _batch: None,
            batch_size=batch,
        )

    run(texts[: layout.streams], 1)  # warm up every replica in its own thread
    started = time.perf_counter()
    run(texts, EMBED_BATCH)
    return len(texts) / max(time.perf_counter() - started, 1e-6)


def _tune_streams(
    info: ModelInfo, options: RuntimeOptions, cpu: CpuInfo, memory: int | None
) -> tuple[RuntimeOptions, str]:
    """Pick parallel indexing streams × threads per stream by measurement (Gate 1 G2)."""
    single = options.torch_threads or cpu.usable
    layouts = stream_candidates(
        cpu.usable,
        single,
        lambda n: streams_that_fit(n, options.embedding_dtype, memory) >= n,
    )
    if len(layouts) == 1:
        return options, f"Indexing uses 1 stream × {single} threads."
    replicas: list[SentenceTransformerEmbedder] = []
    try:
        choice = choose_streams(layouts, lambda lay: _stream_rate(info, options, replicas, lay))
    finally:
        replicas.clear()
        gc.collect()
    best = choice.layout
    tuned = options.model_copy(
        update={"embedding_streams": best.streams, "stream_threads": best.threads}
    )
    return tuned, choice.note


def tune_runtime(
    registry: ProviderRegistry, cpu: CpuInfo, sample_texts: Sequence[str]
) -> TuneResult:
    """Measure thread counts, BF16 against FP32 (embeddings, reranker), then indexing streams."""
    import torch

    texts = list(sample_texts)
    embed_info = registry.info("embeddings")
    notes: list[str] = []

    rates = _thread_rates(embed_info, cpu, texts)
    gc.collect()
    best = max(rates, key=lambda n: rates[n])
    notes.append(f"Uses {best} model threads (fastest of {', '.join(map(str, rates))}).")
    options = RuntimeOptions(torch_threads=best)
    if not cpu.bf16:
        notes.append("This processor has no native BF16, so models run in FP32.")
    else:
        options = _tune_bf16(registry, options, texts, notes)
    options, note = _tune_streams(embed_info, options, cpu, registry.memory())
    notes.append(note)
    torch.set_num_threads(best)
    return TuneResult(options, notes)


def _tune_bf16(
    registry: ProviderRegistry, options: RuntimeOptions, texts: list[str], notes: list[str]
) -> RuntimeOptions:
    """Keep BF16 for embeddings and the reranker only where it is faster with the same results."""
    embed_info = registry.info("embeddings")
    rerank_info = registry.info("reranker")
    query = "Which supplier risks were found in the audit?"
    fp32_rate, reference = _measure_embedder(embed_info, options, "float32", texts)
    gc.collect()
    bf16_rate, vectors = _measure_embedder(embed_info, options, "bfloat16", texts)
    gc.collect()
    cosine = min(
        sum(a * b for a, b in zip(x, y, strict=True))
        for x, y in zip(reference, vectors, strict=True)
    )
    speedup = bf16_rate / fp32_rate
    if speedup >= BF16_MIN_SPEEDUP and cosine >= BF16_MIN_COSINE:
        options = options.model_copy(update={"embedding_dtype": "bfloat16"})
        notes.append(
            f"Indexing uses BF16: {speedup:.1f}× faster, same results (similarity {cosine:.3f})."
        )
    else:
        notes.append(f"Indexing stays FP32 (BF16 {speedup:.1f}× speed, similarity {cosine:.3f}).")

    fp32_rate, reference_scores = _measure_reranker(rerank_info, options, "float32", query, texts)
    gc.collect()
    bf16_rate, scores = _measure_reranker(rerank_info, options, "bfloat16", query, texts)
    gc.collect()
    diff = max(abs(a - b) for a, b in zip(reference_scores, scores, strict=True))
    speedup = bf16_rate / fp32_rate
    if speedup >= BF16_MIN_SPEEDUP and diff <= BF16_MAX_SCORE_DIFF:
        options = options.model_copy(update={"reranker_dtype": "bfloat16"})
        notes.append(f"Ranking uses BF16: {speedup:.1f}× faster, scores within {diff:.3f}.")
    else:
        notes.append(
            f"Ranking stays FP32 (BF16 {speedup:.1f}× speed, scores differ by {diff:.3f})."
        )
    return options
