"""One reader per source type: origin → :class:`Snapshot` (SPEC §7.1)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.db.models import Source
from app.ingest import errors
from app.ingest.browse_root import BrowsePathError, BrowseRootMissingError, resolve_in_root
from app.ingest.errors import fail
from app.ingest.folder import folder_snapshot
from app.parse.audio import AUDIO_EXTENSIONS, SpeechToText, audio_snapshot
from app.parse.documents import DOCLING_FORMATS, DocumentParser
from app.parse.snapshot import Snapshot, read_snapshot_files
from app.parse.text import decode_text, html_title, html_to_markdown, looks_binary, text_snapshot
from app.parse.web import WebFetcher
from app.parse.youtube import (
    YoutubeClient,
    captions_snapshot,
    parse_json3,
    pick_captions,
    youtube_video_id,
)
from app.security.gitleaks import SecretScanner
from app.timeutil import utc_now_iso

TEXT_UPLOADS = frozenset({"txt", "md", "markdown", "csv", "tsv", "json", "log", "rst"})
HTML_UPLOADS = frozenset({"html", "htm"})
UPLOAD_EXTENSIONS = DOCLING_FORMATS | TEXT_UPLOADS | HTML_UPLOADS | AUDIO_EXTENSIONS

Progress = Callable[[float], None]
CheckCancelled = Callable[[], None]


@dataclass
class IngestTools:
    """Everything readers need; tests swap single tools (all network and binaries are seams)."""

    browse_root: Path
    documents: DocumentParser
    web: WebFetcher
    youtube: YoutubeClient
    scanner: SecretScanner
    stt: Callable[[], SpeechToText | None]


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def original_file(folder: Path) -> Path | None:
    return next((p for p in sorted(folder.glob("original.*")) if p.is_file()), None)


def parse_upload(
    tools: IngestTools,
    path: Path,
    title: str | None,
    progress: Progress,
    check_cancelled: CheckCancelled,
) -> Snapshot:
    """Snapshot of an uploaded file, chosen by its extension."""
    ext = extension(path.name)
    if ext not in UPLOAD_EXTENSIONS:
        raise fail(errors.UNSUPPORTED_FILE)
    if path.stat().st_size == 0:
        raise fail(errors.EMPTY_FILE)
    if ext in DOCLING_FORMATS:
        return tools.documents.parse(path, ext, title=title)
    if ext in AUDIO_EXTENSIONS:
        stt = tools.stt()
        if stt is None:
            raise fail(errors.NO_STT)
        return audio_snapshot(path, stt, title, progress=progress, check_cancelled=check_cancelled)
    data = path.read_bytes()
    if looks_binary(data):
        raise fail(errors.UNSUPPORTED_FILE)
    text = decode_text(data)
    if ext in HTML_UPLOADS:
        markdown = html_to_markdown(text)
        if not markdown.strip():
            raise fail(errors.NO_TEXT)
        return text_snapshot(markdown, html_title(text) or title)
    if not text.strip():
        raise fail(errors.NO_TEXT)
    return text_snapshot(text, title)


def read_youtube(
    tools: IngestTools, url: str, progress: Progress, check_cancelled: CheckCancelled
) -> Snapshot:
    """Captions (manual, then automatic) or, without captions, audio → speech-to-text."""
    if youtube_video_id(url) is None:
        raise fail(errors.NOT_YOUTUBE_VIDEO)
    info = tools.youtube.info(url)
    picked = pick_captions(info)
    if picked is not None:
        caption_url, auto = picked
        lines = parse_json3(tools.web.fetch(caption_url).body)
        if lines:
            return captions_snapshot(lines, info, auto)
    stt = tools.stt()
    if stt is None:
        raise fail(errors.NO_CAPTIONS_NO_STT)
    check_cancelled()
    title = str(info.get("title") or "") or None
    with tempfile.TemporaryDirectory(prefix="trustbook-yt-") as tmp:
        audio = tools.youtube.download_audio(url, Path(tmp))
        progress(0.3)
        snapshot = audio_snapshot(
            audio,
            stt,
            title,
            progress=lambda f: progress(0.3 + 0.7 * f),
            check_cancelled=check_cancelled,
        )
    duration = info.get("duration")
    if isinstance(duration, int | float):
        snapshot.stats["duration_s"] = float(duration)
    return snapshot


def read_source(
    tools: IngestTools,
    source: Source,
    folder: Path,
    progress: Progress,
    check_cancelled: CheckCancelled,
) -> tuple[Snapshot, str]:
    """(snapshot, snapshot time) for ``source``; ``folder`` is its ``sources/<id>/`` folder."""
    now = utc_now_iso()
    origin = source.origin or ""
    if source.type == "paste":
        stored = read_snapshot_files(folder)
        if stored is None or not stored[0].strip():
            raise fail(errors.NO_TEXT)
        return text_snapshot(stored[0], source.title), source.snapshot_at or now
    if source.type == "upload":
        path = original_file(folder)
        if path is None:
            raise fail(errors.EMPTY_FILE)
        return parse_upload(tools, path, source.title, progress, check_cancelled), now
    if source.type == "youtube" or (source.type == "search" and youtube_video_id(origin)):
        return read_youtube(tools, origin, progress, check_cancelled), utc_now_iso()
    if source.type in ("url", "search"):
        return tools.web.page_snapshot(origin, tools.documents, check_cancelled=check_cancelled)
    if source.type == "folder":
        try:
            root, relative = resolve_in_root(tools.browse_root, origin)
        except (BrowsePathError, BrowseRootMissingError, FileNotFoundError) as exc:
            raise fail(errors.FOLDER_MISSING) from exc
        snapshot = folder_snapshot(
            root,
            relative,
            browse_root=tools.browse_root,
            scanner=tools.scanner,
            documents=tools.documents,
            report_folder=folder,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        return snapshot, now
    raise fail(errors.UNSUPPORTED_FILE)
