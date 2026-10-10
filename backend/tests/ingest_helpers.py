"""Helpers for ingestion tests: run queued jobs, fake tools, tiny PDFs and audio."""

from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any

import numpy as np

from app.capabilities.bundled import Transcript
from app.parse.documents import ParsedDocument
from app.security.gitleaks import Finding
from app.services import Services

PUBLIC_IP = "93.184.216.34"


def services_of(client: Any) -> Services:
    services: Services = client.app.state.services
    return services


def run_jobs(services: Services, limit: int = 20) -> int:
    """Run queued jobs one by one in this thread (deterministic, no worker threads)."""
    ran = 0
    while ran < limit:
        job = services.jobs.claim_next(None)
        if job is None:
            break
        services.pool._run(job)
        ran += 1
    return ran


def public_resolver(_host: str) -> list[str]:
    return [PUBLIC_IP]


class TextTranscriber:
    """A bundled-STT stand-in that 'hears' a fixed sentence in every window."""

    def __init__(self, text: str = "Hello from the recording.") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, wav_path: Path) -> Transcript:
        self.calls += 1
        with wave.open(str(wav_path), "rb") as audio:
            duration = audio.getnframes() / float(audio.getframerate())
        return Transcript(text=self.text, duration_s=duration)


class FakeStt:
    warning = "Transcript made by a fake."

    def __init__(self, text: str = "Spoken words here.") -> None:
        self.text = text
        self.calls = 0

    def transcribe_wav(self, wav_path: Path) -> str:
        self.calls += 1
        return self.text


class FakeYoutube:
    def __init__(self, info: dict[str, Any], audio: Path | None = None) -> None:
        self._info = info
        self._audio = audio
        self.downloads = 0

    def info(self, url: str) -> dict[str, Any]:
        return self._info

    def download_audio(self, url: str, folder: Path) -> Path:
        self.downloads += 1
        assert self._audio is not None
        target = folder / "audio.wav"
        target.write_bytes(self._audio.read_bytes())
        return target


def fake_scanner(findings: list[Finding] | None = None) -> Any:
    def scan(_folder: Path) -> list[Finding]:
        return list(findings or [])

    return scan


def fake_converter(pages: list[tuple[int | None, str]], used_ocr: bool = False) -> Any:
    def convert(_path: Path) -> ParsedDocument:
        return ParsedDocument(pages=list(pages), used_ocr=used_ocr)

    return convert


def speech_wav(path: Path, bursts: int = 2, rate: int = 16_000) -> Path:
    """Tone bursts (1 s) separated by 1 s of silence: energy VAD sees ``bursts`` regions."""
    pieces = []
    for _ in range(bursts):
        t = np.arange(rate) / rate
        pieces.append((np.sin(2 * math.pi * 220 * t) * 6000).astype(np.int16))
        pieces.append(np.zeros(rate, dtype=np.int16))
    samples = np.concatenate(pieces)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())
    return path


def silent_wav(path: Path, seconds: float = 2.0, rate: int = 16_000) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def text_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page."""
    objects: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def blank_pdf(pages: int = 1) -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
