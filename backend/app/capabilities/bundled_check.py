"""Build-time self-check for the bundled models: ``python -m app.capabilities.bundled_check``.

Loads every model from ``TRUSTBOOK_MODELS_DIR`` offline and checks that scores make sense, so an
image with a broken or mismatched model fails to build instead of failing on a user's laptop.
"""

from __future__ import annotations

import math
import sys
import tempfile
import wave
from pathlib import Path

from app.capabilities import bundled_runtime as rt
from app.capabilities.bundled import RuntimeOptions, read_models_lock
from app.config import Paths

QUESTION = "How does the checker verify sentences against cited passages?"
RELEVANT = (
    "Each sentence in the answer is scored against its cited passages by the checker; it passes "
    "if the best score is at least the verify threshold."
)
IRRELEVANT = "The recipe calls for two cups of flour, one egg and a pinch of salt."


def _tone_wav(path: Path, seconds: float = 1.0, rate: int = 16_000) -> None:
    frames = bytearray()
    for i in range(int(seconds * rate)):
        sample = int(3000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += sample.to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))


def main() -> int:
    models = read_models_lock(Paths.from_env().models_dir)
    options = RuntimeOptions()  # FP32, default threads: the reference configuration

    embedder = rt.load_embedder(models["embeddings"], options)
    query = embedder.embed_queries([QUESTION])[0]
    docs = embedder.embed_documents([RELEVANT, IRRELEVANT])
    sims = [sum(a * b for a, b in zip(query, d, strict=True)) for d in docs]
    assert embedder.dim == len(query) and all(math.isfinite(v) for v in query), "bad embedding"
    assert sims[0] > sims[1], f"embeddings rank wrong: {sims}"
    print(f"embeddings ok: dim={embedder.dim} sims={[round(s, 3) for s in sims]}")

    reranker = rt.load_reranker(models["reranker"], options)
    scores = reranker.score(QUESTION, [RELEVANT, IRRELEVANT])
    assert all(0.0 <= s <= 1.0 for s in scores) and scores[0] > scores[1], f"rerank: {scores}"
    print(f"reranker ok: {[round(s, 3) for s in scores]}")

    checker = rt.load_grounding_checker(models["grounding"], options)
    verdicts = checker.score(
        [
            (RELEVANT, "A sentence passes when its best score reaches the verify threshold."),
            (RELEVANT, "The checker was written in Rust by Google in 2019."),
        ]
    )
    assert verdicts[0] > 0.5 > verdicts[1], f"grounding: {verdicts}"
    print(f"grounding ok: {[round(s, 3) for s in verdicts]}")

    transcriber = rt.load_transcriber(models["stt"], options)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "tone.wav"
        _tone_wav(wav)
        transcript = transcriber.transcribe(wav)
    assert abs(transcript.duration_s - 1.0) < 0.01, "stt duration"
    speech = models["stt"].path / "calibration.wav"
    spoken = transcriber.transcribe(speech).text.lower()
    assert "ask not what your country" in spoken, f"stt: {spoken!r}"
    print(f"stt ok: {spoken!r}")
    print("ALL BUNDLED MODELS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
