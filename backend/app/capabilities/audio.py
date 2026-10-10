"""Synthetic audio for health checks and calibration."""

from __future__ import annotations

import io
import math
import random
import struct
import wave

SAMPLE_RATE = 16_000


def wav_bytes(seconds: float, *, noise: bool = False, seed: int = 7) -> bytes:
    """A 16 kHz mono 16-bit WAV: silence, or quiet deterministic noise with a tone."""
    frames = int(SAMPLE_RATE * seconds)
    rng = random.Random(seed)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        if not noise:
            out.writeframes(b"\x00\x00" * frames)
        else:
            samples = (
                int(800 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE) + rng.randint(-300, 300))
                for i in range(frames)
            )
            out.writeframes(b"".join(struct.pack("<h", s) for s in samples))
    return buffer.getvalue()
