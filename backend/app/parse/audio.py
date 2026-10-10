"""Audio → 16 kHz mono PCM (PyAV) → voice-activity windows → speech-to-text with timestamps.

The energy-based voice-activity check keeps silence and noise away from Whistle, which can invent
a short phrase on non-speech audio (DECISIONS D-008). Each speech window (at most 30 s) is
transcribed on its own, which gives the transcript its timestamps.
"""

from __future__ import annotations

import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.ingest import errors
from app.ingest.errors import fail
from app.parse.snapshot import Snapshot, SnapshotBuilder

SAMPLE_RATE = 16_000
FRAME = 480  # 30 ms
MAX_SECONDS = 4 * 3600
WINDOW_S = 30.0
MIN_SPEECH_S = 0.25
MERGE_GAP_S = 0.6
PAD_S = 0.2
ABS_THRESHOLD = 300.0  # RMS on the int16 scale (about -41 dBFS)
# A loud constant background must not hide speech: the adaptive threshold is capped.
MAX_THRESHOLD = 1500.0

AUDIO_EXTENSIONS = frozenset({"mp3", "m4a", "wav", "ogg", "webm", "flac", "opus", "aac"})

PCM = Any  # numpy int16 array


class SpeechToText(Protocol):
    """One speech-to-text backend; ``warning`` is the quality warning its transcripts get."""

    warning: str

    def transcribe_wav(self, wav_path: Path) -> str: ...


@dataclass(frozen=True)
class TimedText:
    start_s: float
    end_s: float
    text: str


def decode_audio(path: Path, max_seconds: float = MAX_SECONDS) -> PCM:
    """Decode any audio (or video) file to 16 kHz mono int16 samples."""
    import av

    chunks: list[Any] = []
    total = 0
    limit = int(max_seconds * SAMPLE_RATE)
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise fail(errors.AUDIO_UNREADABLE)
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    data = out.to_ndarray().reshape(-1)
                    chunks.append(data)
                    total += data.shape[0]
                    if total > limit:
                        raise fail(errors.AUDIO_TOO_LONG)
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
    except errors.IngestError:
        raise
    except (av.error.FFmpegError, ValueError, OSError, IndexError) as exc:
        raise fail(errors.AUDIO_UNREADABLE) from exc
    if not chunks:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(chunks).astype(np.int16, copy=False)


def frame_energy(samples: PCM) -> PCM:
    """RMS per 30 ms frame."""
    frames = samples.shape[0] // FRAME
    if frames == 0:
        return np.zeros(0)
    data = samples[: frames * FRAME].astype(np.float64).reshape(frames, FRAME)
    return np.sqrt((data * data).mean(axis=1))


def speech_regions(samples: PCM) -> list[tuple[int, int]]:
    """(start, end) sample ranges that contain speech energy, padded and merged."""
    energy = frame_energy(samples)
    if energy.size == 0:
        return []
    floor = float(np.percentile(energy, 10))
    threshold = max(ABS_THRESHOLD, min(floor * 3.0, MAX_THRESHOLD))
    voiced = energy > threshold
    regions: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate(voiced):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            regions.append((start, i))
            start = None
    if start is not None:
        regions.append((start, len(voiced)))

    merge_gap = int(MERGE_GAP_S * SAMPLE_RATE / FRAME)
    merged: list[tuple[int, int]] = []
    for region in regions:
        if merged and region[0] - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], region[1])
        else:
            merged.append(region)
    min_frames = int(MIN_SPEECH_S * SAMPLE_RATE / FRAME)
    pad = int(PAD_S * SAMPLE_RATE)
    total = samples.shape[0]
    return [
        (max(0, s * FRAME - pad), min(total, e * FRAME + pad))
        for s, e in merged
        if e - s >= min_frames
    ]


def speech_windows(samples: PCM, window_s: float = WINDOW_S) -> list[tuple[int, int]]:
    """Speech regions grouped into windows of at most ``window_s`` seconds.

    Long regions are cut at the quietest frame in the last fifth of the window.
    """
    limit = int(window_s * SAMPLE_RATE)
    energy = frame_energy(samples)
    windows: list[tuple[int, int]] = []
    for start, end in speech_regions(samples):
        if windows and end - windows[-1][0] <= limit:
            windows[-1] = (windows[-1][0], end)
            continue
        cursor = start
        while end - cursor > limit:
            lo = (cursor + limit * 4 // 5) // FRAME
            hi = min((cursor + limit) // FRAME, energy.size)
            cut = (lo + int(np.argmin(energy[lo:hi]))) * FRAME if hi > lo else cursor + limit
            windows.append((cursor, cut))
            cursor = cut
        windows.append((cursor, end))
    return windows


def write_wav(path: Path, samples: PCM) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.astype("<i2").tobytes())


def transcribe(
    samples: PCM,
    stt: SpeechToText,
    *,
    progress: Callable[[float], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> list[TimedText]:
    """Transcribe each speech window; raises "No speech found" when there is none."""
    windows = speech_windows(samples)
    if not windows:
        raise fail(errors.NO_SPEECH)
    results: list[TimedText] = []
    with tempfile.TemporaryDirectory(prefix="trustbook-audio-") as tmp:
        for i, (start, end) in enumerate(windows):
            if check_cancelled is not None:
                check_cancelled()
            path = Path(tmp) / f"window-{i}.wav"
            write_wav(path, samples[start:end])
            text = " ".join(stt.transcribe_wav(path).split())
            path.unlink(missing_ok=True)
            if text:
                results.append(TimedText(start / SAMPLE_RATE, end / SAMPLE_RATE, text))
            if progress is not None:
                progress((i + 1) / len(windows))
    if not results:
        raise fail(errors.NO_SPEECH)
    return results


def transcript_snapshot(
    parts: list[TimedText],
    title: str | None,
    duration_s: float,
    warning: str | None,
    *,
    separator: str = "\n\n",
) -> Snapshot:
    """A timestamp-mapped snapshot: one line or paragraph per timed part."""
    builder = SnapshotBuilder()
    for part in parts:
        if builder.length:
            builder.text(separator)
        builder.add(part.text, start_s=round(part.start_s, 2), end_s=round(part.end_s, 2))
    snapshot = Snapshot(builder.build(), "timestamps", builder.segments, title=title)
    snapshot.stats["duration_s"] = round(duration_s, 1)
    if warning:
        snapshot.warnings.append(warning)
    return snapshot


def audio_snapshot(
    path: Path,
    stt: SpeechToText,
    title: str | None,
    *,
    progress: Callable[[float], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> Snapshot:
    """Decode, detect speech and transcribe an audio file."""
    samples = decode_audio(path)
    duration = samples.shape[0] / SAMPLE_RATE
    parts = transcribe(samples, stt, progress=progress, check_cancelled=check_cancelled)
    return transcript_snapshot(parts, title, duration, stt.warning)
