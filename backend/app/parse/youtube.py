"""YouTube: yt-dlp captions (manual, then automatic; English preferred), else audio → STT."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from app.ingest import errors
from app.ingest.errors import fail
from app.parse.audio import TimedText, transcript_snapshot
from app.parse.snapshot import Snapshot

YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtube-nocookie.com",
        "youtube-nocookie.com",
    }
)
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_AUDIO_BYTES = 600 * 1024 * 1024


def is_youtube_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in YOUTUBE_HOSTS


def youtube_video_id(url: str) -> str | None:
    """The 11-character video id of a watch/shorts/live/embed/youtu.be link, else ``None``."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    candidate: str | None = None
    if host == "youtu.be":
        candidate = segments[0] if segments else None
    elif segments[:1] == ["watch"]:
        values = parse_qs(parts.query).get("v") or []
        candidate = values[0] if values else None
    elif len(segments) >= 2 and segments[0] in ("shorts", "live", "embed", "v"):
        candidate = segments[1]
    return candidate if candidate and _VIDEO_ID.match(candidate) else None


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


class YoutubeClient(Protocol):
    def info(self, url: str) -> dict[str, Any]: ...

    def download_audio(self, url: str, folder: Path) -> Path: ...


class YtDlpClient:
    """yt-dlp without cache, playlists or post-processing (PyAV decodes the audio)."""

    _BASE: dict[str, Any] = {  # noqa: RUF012 - read-only option template
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cachedir": False,
        "noprogress": True,
    }

    def info(self, url: str) -> dict[str, Any]:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL({**self._BASE, "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise fail(errors.YOUTUBE_UNAVAILABLE) from exc
        if not isinstance(info, dict):
            raise fail(errors.YOUTUBE_UNAVAILABLE)
        return info

    def download_audio(self, url: str, folder: Path) -> Path:
        import yt_dlp

        options = {
            **self._BASE,
            "format": "bestaudio/best",
            "outtmpl": str(folder / "audio.%(ext)s"),
            "max_filesize": MAX_AUDIO_BYTES,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            raise fail(errors.YOUTUBE_UNAVAILABLE) from exc
        files = [p for p in folder.glob("audio.*") if p.is_file() and p.suffix != ".part"]
        if not files:
            raise fail(errors.YOUTUBE_UNAVAILABLE)
        return files[0]


def _pick_language(tracks: dict[str, Any], auto: bool) -> list[dict[str, Any]] | None:
    keys = [k for k, v in tracks.items() if isinstance(v, list) and v and k != "live_chat"]
    if not keys:
        return None
    order: list[Callable[[str], bool]] = []
    if auto:
        order.append(lambda k: k == "en-orig")
    order += [lambda k: k == "en", lambda k: k.startswith(("en-", "en_"))]
    if auto:
        order.append(lambda k: k.endswith("-orig"))
    for rule in order:
        for key in keys:
            if rule(key):
                return list(tracks[key])
    return list(tracks[keys[0]])


def pick_captions(info: dict[str, Any]) -> tuple[str, bool] | None:
    """(json3 caption URL, is automatic) — manual captions first, then automatic ones."""
    for field, auto in (("subtitles", False), ("automatic_captions", True)):
        tracks = info.get(field)
        if not isinstance(tracks, dict):
            continue
        formats = _pick_language(tracks, auto)
        for fmt in formats or []:
            if isinstance(fmt, dict) and fmt.get("ext") == "json3" and fmt.get("url"):
                return str(fmt["url"]), auto
    return None


def parse_json3(data: bytes) -> list[TimedText]:
    """Caption events → timed lines (empty and newline-only events are skipped)."""
    try:
        raw = json.loads(data)
    except ValueError:
        return []
    events = raw.get("events") if isinstance(raw, dict) else None
    lines: list[TimedText] = []
    for event in events or []:
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
            continue
        text = "".join(str(s.get("utf8", "")) for s in event["segs"] if isinstance(s, dict))
        text = " ".join(text.split())
        if not text:
            continue
        start = float(event.get("tStartMs") or 0) / 1000
        end = start + float(event.get("dDurationMs") or 0) / 1000
        lines.append(TimedText(round(start, 2), round(end, 2), text))
    return lines


def captions_snapshot(lines: list[TimedText], info: dict[str, Any], auto: bool) -> Snapshot:
    duration = info.get("duration")
    total = float(duration) if isinstance(duration, int | float) else lines[-1].end_s
    title = str(info.get("title") or "") or None
    warning = errors.WARN_AUTO_CAPTIONS if auto else None
    return transcript_snapshot(lines, title, total, warning, separator="\n")
