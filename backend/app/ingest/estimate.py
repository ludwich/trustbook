"""Processing estimates before adding sources, and per-job estimates for the queue (SPEC §11).

An estimate is a :class:`Workload` (how much text, how many pages, how much audio) priced with this
computer's calibration (:class:`Rates`). Folders are walked with the ingestion walker itself
(:func:`app.ingest.folder.walk_folder`), so ignore rules, size limits and the file cap are the
same; only files of 10 KB and more are opened, and only their first 64 KB, to spot generated
or minified files the same way reading does. Everything else comes from names and sizes.

Measured on ``psf/requests`` with the production tokenizer (docs/DECISIONS.md, estimates):

* code: 1,125 bytes and 360 tokens per chunk; text documents: 1,400 bytes and 445 tokens;
* calibration times synthetic chunks of 686 tokens (code, parallel streams) and 351 tokens
  (prose, one call). Embedding cost grows with tokens, so the calibrated chunk rates are
  converted to tokens per second and back to real chunks (``requests`` real chunks: 10.2/s
  where calibration stores 5.0/s for its longer code chunks).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from app.db.models import Source
from app.ingest.browse_root import resolve_in_root
from app.ingest.folder import TEXT_DOCUMENTS, skipped_when_read, walk_folder
from app.ingest.readers import HTML_UPLOADS, TEXT_UPLOADS, extension, original_file
from app.jobs.calibration import Calibration
from app.parse.audio import AUDIO_EXTENSIONS
from app.parse.documents import DOCLING_FORMATS
from app.parse.youtube import youtube_video_id

logger = logging.getLogger("trustbook.estimate")

# --- text → chunks (measured on psf/requests, 554 chunks) ----------------------------------------
CODE_BYTES_PER_CHUNK = 1_125
CODE_TOKENS_PER_CHUNK = 360
TEXT_BYTES_PER_CHUNK = 1_400
TEXT_TOKENS_PER_CHUNK = 445
HTML_TEXT_SHARE = 0.3  # of an HTML file's bytes, the readable text

# --- calibration → tokens per second ---------------------------------------------------------
CALIBRATION_CODE_TOKENS = 686  # app.capabilities.streams.synthetic_code_chunks
CALIBRATION_PROSE_TOKENS = 351  # app.jobs.calibration.synthetic_chunks
# Before calibration: a slow PC (the dev container measured 2.25 prose chunks/s before D-020).
DEFAULT_EMBED_TOKENS_PER_S = 700.0
DEFAULT_PARSE_PAGES_PER_S = 0.5
DEFAULT_STT_REALTIME_FACTOR = 1.0

# --- documents: pages from bytes (min 1), chunks per page --------------------------------------
# Text PDFs measured 8–12 KB per page, scans and image-heavy pages 30–300 KB.
DOCUMENT_BYTES_PER_PAGE = {"pdf": 25_000, "docx": 20_000, "pptx": 60_000, "xlsx": 50_000}
DOCUMENT_CHUNKS_PER_PAGE = {"pdf": 1.5, "docx": 1.5, "pptx": 0.5, "xlsx": 2.0}
DOCUMENT_OVERHEAD_S = 0.5  # opening and laying out one document

# --- audio: duration from bytes (typical voice-recording bitrates) ------------------------------
AUDIO_BYTES_PER_S = {
    "mp3": 16_000,  # 128 kbit/s
    "m4a": 16_000,
    "aac": 16_000,
    "ogg": 16_000,
    "opus": 8_000,  # 64 kbit/s
    "webm": 8_000,
    "flac": 50_000,  # ~55 % of 44.1 kHz mono 16-bit
    "wav": 88_200,  # 44.1 kHz mono 16-bit
}
AUDIO_SECONDS_PER_CHUNK = 150.0  # ~500 tokens of speech

# --- per job and per item --------------------------------------------------------------------
JOB_OVERHEAD_S = 2.0  # snapshot, rows, full-text index
# Secret scan, loading the indexing models the first time, writing a large index.
FOLDER_OVERHEAD_S = 15.0
FOLDER_FILE_S = 0.02  # listing, reading and chunking one file
URL_FETCH_S, URL_CHUNKS = 8.0, 10.0
YOUTUBE_FETCH_S, YOUTUBE_CHUNKS = 15.0, 15.0  # captions; without captions STT takes longer
PASTE_CHUNKS = 3.0

SNIFF_MIN_BYTES = 10_000  # a smaller file cannot hold a 10,000-character line
SNIFF_BYTES = 64 * 1024
CONFIRM_ABOVE_S = 120
ROUGH_NOTE = "rough estimate (speed test not run yet)"


@dataclass(frozen=True)
class Rates:
    """This computer's speed, with the quantities calibration has not measured."""

    code_chunks_per_s: float
    text_chunks_per_s: float
    parse_pages_per_s: float
    stt_realtime_factor: float
    missing: frozenset[str] = frozenset()  # "embedding", "parse", "stt"


def _positive(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) and value > 0 else None


def rates_from(calibration: Calibration | None) -> Rates:
    """Rates from ``calibration`` (``None``: not run), with conservative defaults for gaps."""
    code = _positive(calibration.embedding_chunks_per_s_code) if calibration else None
    prose = _positive(calibration.embedding_chunks_per_s) if calibration else None
    parse = _positive(calibration.parse_pages_per_s) if calibration else None
    rtf = _positive(calibration.stt_realtime_factor) if calibration else None
    code_tokens = code * CALIBRATION_CODE_TOKENS if code else None
    prose_tokens = prose * CALIBRATION_PROSE_TOKENS if prose else None
    missing: set[str] = set()
    if code_tokens is None and prose_tokens is None:
        missing.add("embedding")
    if parse is None:
        missing.add("parse")
    if rtf is None:
        missing.add("stt")
    code_rate = code_tokens or prose_tokens or DEFAULT_EMBED_TOKENS_PER_S
    text_rate = prose_tokens or code_tokens or DEFAULT_EMBED_TOKENS_PER_S
    return Rates(
        code_chunks_per_s=code_rate / CODE_TOKENS_PER_CHUNK,
        text_chunks_per_s=text_rate / TEXT_TOKENS_PER_CHUNK,
        parse_pages_per_s=parse or DEFAULT_PARSE_PAGES_PER_S,
        stt_realtime_factor=rtf or DEFAULT_STT_REALTIME_FACTOR,
        missing=frozenset(missing),
    )


@dataclass
class Workload:
    """What adding one or more sources involves; priced by :meth:`seconds`."""

    code_chunks: float = 0.0
    text_chunks: float = 0.0
    pages: float = 0.0
    audio_s: float = 0.0
    fixed_s: float = 0.0
    files: int | None = None  # None: no files involved (web pages, pasted text)

    def __iadd__(self, other: Workload) -> Workload:
        self.code_chunks += other.code_chunks
        self.text_chunks += other.text_chunks
        self.pages += other.pages
        self.audio_s += other.audio_s
        self.fixed_s += other.fixed_s
        if other.files is not None:
            self.files = (self.files or 0) + other.files
        return self

    def seconds(self, rates: Rates) -> float:
        transcript_chunks = self.audio_s / AUDIO_SECONDS_PER_CHUNK
        return (
            self.fixed_s
            + self.code_chunks / rates.code_chunks_per_s
            + (self.text_chunks + transcript_chunks) / rates.text_chunks_per_s
            + self.pages / rates.parse_pages_per_s
            + self.audio_s * rates.stt_realtime_factor
        )

    def rough(self, rates: Rates) -> bool:
        """Whether a quantity this workload needs was not measured (defaults were used)."""
        needs = {"embedding"} if self.code_chunks or self.text_chunks or self.audio_s else set()
        if self.pages:
            needs |= {"parse", "embedding"}
        if self.audio_s:
            needs.add("stt")
        return bool(needs & rates.missing)


# --- workloads ---------------------------------------------------------------------------------


def document_workload(ext: str, size: int) -> Workload:
    """A PDF, Word, PowerPoint or Excel file: pages from its size, then chunks per page."""
    pages = max(1.0, size / DOCUMENT_BYTES_PER_PAGE[ext])
    return Workload(
        pages=pages, text_chunks=pages * DOCUMENT_CHUNKS_PER_PAGE[ext], fixed_s=DOCUMENT_OVERHEAD_S
    )


def upload_workload(name: str, size: int) -> Workload:
    """One uploaded file (one job), from its name and size only."""
    ext = extension(name)
    work = Workload(files=1, fixed_s=JOB_OVERHEAD_S)
    size = max(0, size)
    if ext in DOCLING_FORMATS:
        work += document_workload(ext, size)
    elif ext in AUDIO_EXTENSIONS:
        work.audio_s = size / AUDIO_BYTES_PER_S.get(ext, 16_000)
    elif ext in HTML_UPLOADS:
        work.text_chunks = max(1.0, size * HTML_TEXT_SHARE / TEXT_BYTES_PER_CHUNK)
    elif ext in TEXT_UPLOADS:
        work.text_chunks = max(1.0, size / TEXT_BYTES_PER_CHUNK)
    # Unsupported files fail at once: only the job overhead.
    return work


def item_workload(kind: str, count: int = 1) -> Workload:
    """``count`` web pages, YouTube videos or pasted texts of typical size."""
    count = max(0, count)
    if kind == "youtube":
        fetch, chunks = YOUTUBE_FETCH_S, YOUTUBE_CHUNKS
    elif kind in ("url", "search"):
        fetch, chunks = URL_FETCH_S, URL_CHUNKS
    else:  # paste
        fetch, chunks = 0.0, PASTE_CHUNKS
    return Workload(text_chunks=count * chunks, fixed_s=count * (fetch + JOB_OVERHEAD_S))


def text_workload(chars: int) -> Workload:
    """Pasted text of known length (one job)."""
    return Workload(text_chunks=max(1.0, chars / TEXT_BYTES_PER_CHUNK), fixed_s=JOB_OVERHEAD_S)


def _generated(path: Path, ext: str) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(SNIFF_BYTES)
    except OSError:
        return True  # unreadable now: reading would skip it too
    return skipped_when_read(head, ext)


def folder_workload(root: Path) -> Workload:
    """A linked folder, walked exactly as ingestion walks it (one job).

    :raises IngestError: more files than the folder limit (``FOLDER_TOO_BIG``).
    :raises OSError: the folder can't be read.
    """
    walk = walk_folder(root)
    work = Workload(files=0, fixed_s=JOB_OVERHEAD_S + FOLDER_OVERHEAD_S)
    code_bytes = text_bytes = 0
    for rel in walk.files:
        size = walk.sizes.get(rel, 0)
        ext = extension(rel)
        if ext in DOCLING_FORMATS:
            work += document_workload(ext, size)
        elif size >= SNIFF_MIN_BYTES and _generated(root / rel, ext):
            continue
        elif ext in TEXT_DOCUMENTS:
            text_bytes += size
        else:
            code_bytes += size
        work.files = (work.files or 0) + 1
        work.fixed_s += FOLDER_FILE_S
    work.code_chunks += code_bytes / CODE_BYTES_PER_CHUNK
    work.text_chunks += text_bytes / TEXT_BYTES_PER_CHUNK
    return work


def source_workload(source: Source, folder: Path, browse_root: Path) -> Workload | None:
    """The workload of an existing source's next ingest job; ``None`` when it can't be told."""
    origin = source.origin or ""
    try:
        if source.type == "paste":
            snapshot = folder / "snapshot.md"
            return text_workload(snapshot.stat().st_size) if snapshot.is_file() else None
        if source.type == "upload":
            path = original_file(folder)
            return upload_workload(path.name, path.stat().st_size) if path else None
        if source.type == "youtube" or (source.type == "search" and youtube_video_id(origin)):
            return item_workload("youtube")
        if source.type in ("url", "search"):
            return item_workload("url")
        if source.type == "folder":
            root, _relative = resolve_in_root(browse_root, origin)
            return folder_workload(root)
    except Exception:  # an estimate never blocks adding a source; the job reports real errors
        logger.debug("No estimate for source %s", source.id, exc_info=True)
    return None


# --- the answer --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    seconds: int
    files: int | None
    summary: str
    confirm: bool


def _duration(seconds: int) -> str:
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(5 * round(minutes / 5), 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _about_files(files: int) -> str:
    if files == 1:
        return "1 file"
    step = 100 if files >= 1_000 else 10 if files >= 100 else 1
    rounded = max(step, step * round(files / step))
    return f"About {rounded:,} files"


def summarise(seconds: int, files: int | None, rough: bool) -> str:
    """Plain English: "About 4,200 files, roughly 25 min on this PC" / "About 3 min on this PC"."""
    duration = _duration(seconds)
    if files:
        roughly = duration if seconds < 60 else f"roughly {duration}"
        text = f"{_about_files(files)}, {roughly} on this PC"
    elif seconds < 60:
        text = "Under a minute on this PC"
    else:
        text = f"About {duration} on this PC"
    return f"{text}, {ROUGH_NOTE}" if rough else text


def estimate(work: Workload, calibration: Calibration | None) -> Estimate:
    """Price ``work`` on this computer; ``confirm`` above :data:`CONFIRM_ABOVE_S` seconds."""
    rates = rates_from(calibration)
    seconds = math.ceil(work.seconds(rates))
    return Estimate(
        seconds=seconds,
        files=work.files,
        summary=summarise(seconds, work.files, work.rough(rates)),
        confirm=seconds > CONFIRM_ABOVE_S,
    )


def job_seconds(
    source: Source, folder: Path, browse_root: Path, calibration: Calibration | None
) -> int | None:
    """Estimated seconds for ``source``'s next ingest job (stored with the job at enqueue)."""
    work = source_workload(source, folder, browse_root)
    return None if work is None else math.ceil(work.seconds(rates_from(calibration)))


__all__ = [
    "CONFIRM_ABOVE_S",
    "Estimate",
    "Rates",
    "Workload",
    "estimate",
    "folder_workload",
    "item_workload",
    "job_seconds",
    "rates_from",
    "source_workload",
    "summarise",
    "text_workload",
    "upload_workload",
]
