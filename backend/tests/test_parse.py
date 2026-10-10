from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from app.ingest import errors
from app.ingest.errors import IngestError
from app.parse.audio import (
    SAMPLE_RATE,
    audio_snapshot,
    decode_audio,
    speech_regions,
    speech_windows,
    transcribe,
)
from app.parse.documents import DocumentParser
from app.parse.snapshot import read_snapshot_files, sections_from_markdown, write_snapshot
from app.parse.text import decode_text, html_to_markdown, looks_binary, page_title, text_snapshot
from app.parse.web import WebFetcher
from app.parse.youtube import (
    canonical_url,
    parse_json3,
    pick_captions,
    youtube_video_id,
)
from tests.ingest_helpers import (
    FakeStt,
    blank_pdf,
    fake_converter,
    public_resolver,
    silent_wav,
    speech_wav,
    text_pdf,
)

ARTICLE = (
    "<html><head><title>Valve guide</title></head><body><nav>Menu</nav><article>"
    "<h1>Valve maintenance</h1>"
    + "".join(
        f"<p>Step {i}: tighten valve {i} to {40 + i} Nm before the pressure test starts.</p>"
        for i in range(6)
    )
    + "<h2>Inspection</h2>"
    + "".join(
        f"<p>Inspect seal {i} every {i + 2} months and replace worn parts.</p>" for i in range(6)
    )
    + "</article><footer>Footer</footer></body></html>"
)


# --- text and snapshots ----------------------------------------------------------------------


def test_decode_text_and_binary_sniff() -> None:
    assert decode_text("héllo".encode()) == "héllo"
    assert decode_text("héllo".encode("cp1252")) == "héllo"
    assert decode_text(b"\xef\xbb\xbfbom") == "bom"
    assert decode_text("hi".encode("utf-16")) == "hi"
    assert looks_binary(b"ab\x00cd")
    assert not looks_binary("hi".encode("utf-16"))


def test_sections_and_snapshot_files(tmp_path: Path) -> None:
    md = "Preamble.\n\n# One\n\nText.\n\n## Two ##\n\nMore."
    segments = sections_from_markdown(md)
    assert [(s.heading, md[s.start : s.start + 5]) for s in segments] == [
        (None, "Pream"),
        ("One", "# One"),
        ("Two", "## Tw"),
    ]
    snap = text_snapshot("a\r\nb\x00\n", "T")
    assert snap.markdown == "a\nb"
    write_snapshot(tmp_path, snap)
    stored = read_snapshot_files(tmp_path)
    assert stored is not None
    assert stored[0] == "a\nb"
    assert stored[1]["version"] == 1 and stored[1]["kind"] == "sections"
    assert read_snapshot_files(tmp_path / "none") is None


def test_html_to_markdown_and_title() -> None:
    markdown = html_to_markdown(ARTICLE, "https://example.com/v")
    assert "tighten valve 3" in markdown
    assert "Menu" not in markdown and "Footer" not in markdown
    assert "# Valve maintenance" in markdown or "Valve maintenance" in markdown
    assert page_title(ARTICLE) in {"Valve guide", "Valve maintenance"}


# --- documents -------------------------------------------------------------------------------


def test_pypdf_fallback_reads_text_pages_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(text_pdf(["Hello page one", "Second page here"]))
    parser = DocumentParser(tmp_path, lambda: 1, available=False)
    snap = parser.parse(path, "pdf", title="doc.pdf")
    assert snap.kind == "pages"
    assert [s.page for s in snap.segments] == [1, 2]
    assert "Hello page one" in snap.markdown
    assert snap.stats["pages"] == 2
    assert snap.warnings == [errors.WARN_BASIC_PDF]


def test_scanned_pdf_without_parser_fails_with_next_step(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    path.write_bytes(blank_pdf(2))
    parser = DocumentParser(tmp_path, lambda: 1, available=False)
    with pytest.raises(IngestError) as info:
        parser.parse(path, "pdf")
    assert info.value.reason == errors.SCANNED_PDF[0]
    assert "OCR" in (info.value.next_step or "")


def test_damaged_pdf_and_office_without_parser(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    parser = DocumentParser(tmp_path, lambda: 1, available=False)
    with pytest.raises(IngestError, match="could not be read"):
        parser.parse(broken, "pdf")
    with pytest.raises(IngestError, match="Word, PowerPoint and Excel"):
        parser.parse(tmp_path / "x.docx", "docx")


def test_docling_converter_pages_ocr_and_sections(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    parser = DocumentParser(
        tmp_path, lambda: 1, converter=fake_converter([(1, "# Intro\n\nText"), (2, "More")], True)
    )
    snap = parser.parse(path, "pdf")
    assert [s.page for s in snap.segments] == [1, 2]
    assert snap.warnings == [errors.WARN_OCR]

    docx = DocumentParser(tmp_path, lambda: 1, converter=fake_converter([(None, "# H\n\nBody")]))
    snap = docx.parse(tmp_path / "a.docx", "docx")
    assert snap.kind == "sections"
    assert snap.segments[0].heading == "H"

    empty = DocumentParser(tmp_path, lambda: 1, converter=fake_converter([(1, "  ")]))
    with pytest.raises(IngestError, match="even with text recognition"):
        empty.parse(path, "pdf")


def test_docling_failure_on_pdf_falls_back_to_pypdf(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(text_pdf(["Readable"]))

    def broken(_path: Path) -> Any:
        raise RuntimeError("model failed")

    parser = DocumentParser(tmp_path, lambda: 1, converter=broken)
    snap = parser.parse(path, "pdf")
    assert "Readable" in snap.markdown
    assert snap.warnings == [errors.WARN_BASIC_PDF]


# --- web -------------------------------------------------------------------------------------


def _fetcher(handler: Any, **kwargs: Any) -> WebFetcher:
    return WebFetcher(
        transport=httpx.MockTransport(handler),
        resolver=kwargs.pop("resolver", public_resolver),
        **kwargs,
    )


def test_fetch_follows_redirects_and_rechecks_each_hop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        if request.url.path == "/final":
            return httpx.Response(200, html=ARTICLE)
        if request.url.path == "/evil":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(404)

    fetcher = _fetcher(handler)
    page = fetcher.fetch("https://example.com/start")
    assert page.url == "https://example.com/final"
    assert page.content_type == "text/html"
    with pytest.raises(IngestError) as info:
        fetcher.fetch("https://example.com/evil")
    assert "local network" in info.value.reason
    with pytest.raises(IngestError, match="not found"):
        fetcher.fetch("https://example.com/missing")


def test_fetch_refuses_private_hosts_before_any_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    fetcher = _fetcher(handler, resolver=lambda _h: ["10.0.0.5"])
    with pytest.raises(IngestError, match="local network"):
        fetcher.fetch("https://intranet.example/")
    assert calls == []


@pytest.mark.parametrize(
    ("status", "text"),
    [(403, "refused"), (401, "refused"), (429, "limiting"), (503, "had an error")],
)
def test_fetch_status_messages(status: int, text: str) -> None:
    fetcher = _fetcher(lambda _r: httpx.Response(status))
    with pytest.raises(IngestError, match=text) as info:
        fetcher.fetch("https://example.com/")
    assert info.value.next_step


def test_fetch_size_cap_and_network_errors() -> None:
    big = _fetcher(lambda _r: httpx.Response(200, content=b"x" * 2000), max_bytes=1000)
    with pytest.raises(IngestError, match="15 MB"):
        big.fetch("https://example.com/")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(IngestError, match="could not reach"):
        _fetcher(unreachable).fetch("https://example.com/")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(IngestError, match="too long"):
        _fetcher(slow).fetch("https://example.com/")

    def dns_fails(_host: str) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(IngestError, match="could not reach"):
        _fetcher(slow, resolver=dns_fails).fetch("https://nowhere.example/")


class FakeRenderer:
    def __init__(self, html: str) -> None:
        self.html = html
        self.urls: list[str] = []

    def render(self, url: str) -> str:
        self.urls.append(url)
        return self.html


def test_page_snapshot_uses_browser_when_extraction_is_short(tmp_path: Path) -> None:
    shell = "<html><head><title>App</title></head><body><div id=root></div></body></html>"
    renderer = FakeRenderer(ARTICLE)
    fetcher = _fetcher(lambda _r: httpx.Response(200, html=shell), renderer=renderer)
    documents = DocumentParser(tmp_path, lambda: 1, available=False)
    snap, fetched_at = fetcher.page_snapshot("https://example.com/app", documents)
    assert renderer.urls == ["https://example.com/app"]
    assert "tighten valve 3" in snap.markdown
    assert snap.title == "App"
    assert fetched_at.endswith("Z")

    renderer.urls.clear()
    full = _fetcher(lambda _r: httpx.Response(200, html=ARTICLE), renderer=renderer)
    snap, _ = full.page_snapshot("https://example.com/v", documents)
    assert renderer.urls == []
    assert snap.title in {"Valve guide", "Valve maintenance"}


def test_page_snapshot_empty_pdf_and_unsupported(tmp_path: Path) -> None:
    documents = DocumentParser(tmp_path, lambda: 1, available=False)
    empty = _fetcher(lambda _r: httpx.Response(200, html="<html><body></body></html>"))
    with pytest.raises(IngestError) as info:
        empty.page_snapshot("https://example.com/", documents)
    assert info.value.reason == errors.PAGE_NO_TEXT[0]

    pdf = _fetcher(
        lambda _r: httpx.Response(
            200, content=text_pdf(["Linked PDF"]), headers={"content-type": "application/pdf"}
        )
    )
    snap, _ = pdf.page_snapshot("https://example.com/files/report.pdf", documents)
    assert snap.kind == "pages" and "Linked PDF" in snap.markdown
    assert snap.title == "report.pdf"

    zip_file = _fetcher(
        lambda _r: httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})
    )
    with pytest.raises(IngestError, match="file type"):
        zip_file.page_snapshot("https://example.com/a.zip", documents)

    plain = _fetcher(lambda _r: httpx.Response(200, text="# Notes\n\nPlain text page."))
    snap, _ = plain.page_snapshot("https://example.com/notes.txt", documents)
    assert snap.markdown.startswith("# Notes")


# --- audio -----------------------------------------------------------------------------------


def test_decode_and_vad_find_speech_windows(tmp_path: Path) -> None:
    samples = decode_audio(speech_wav(tmp_path / "s.wav", bursts=3))
    assert samples.dtype == np.int16
    assert abs(samples.shape[0] / SAMPLE_RATE - 6.0) < 0.05
    regions = speech_regions(samples)
    assert len(regions) == 3
    windows = speech_windows(samples, window_s=2.5)
    assert len(windows) == 3
    assert speech_windows(samples, window_s=30)[0][0] == regions[0][0]
    assert len(speech_windows(samples, window_s=30)) == 1


def test_long_speech_is_cut_into_windows() -> None:
    t = np.arange(SAMPLE_RATE * 70) / SAMPLE_RATE
    samples = (np.sin(2 * np.pi * 200 * t) * 5000).astype(np.int16)
    windows = speech_windows(samples)
    assert len(windows) == 3
    assert all((end - start) / SAMPLE_RATE <= 30.0 + 1e-6 for start, end in windows)


def test_transcribe_and_no_speech(tmp_path: Path) -> None:
    stt = FakeStt("Hello there.")
    snap = audio_snapshot(speech_wav(tmp_path / "s.wav"), stt, "Talk")
    assert stt.calls == 1  # both bursts fit one 30 s window
    assert snap.kind == "timestamps"
    assert snap.segments[0].start_s is not None and snap.segments[0].start_s < 0.1
    assert snap.stats["duration_s"] == 4.0
    assert snap.warnings == [FakeStt.warning]

    silent = decode_audio(silent_wav(tmp_path / "quiet.wav"))
    with pytest.raises(IngestError, match="No speech found"):
        transcribe(silent, stt)
    with pytest.raises(IngestError, match="No speech found"):
        transcribe(decode_audio(speech_wav(tmp_path / "s2.wav")), FakeStt(""))


def test_undecodable_audio(tmp_path: Path) -> None:
    path = tmp_path / "x.mp3"
    path.write_bytes(b"not audio")
    with pytest.raises(IngestError, match="could not be decoded"):
        decode_audio(path)


# --- YouTube ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "video"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=x", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/playlist?list=PL123", None),
        ("https://www.youtube.com/@channel", None),
        ("https://example.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_youtube_video_id(url: str, video: str | None) -> None:
    assert youtube_video_id(url) == video


def test_pick_captions_prefers_manual_english_then_auto() -> None:
    def track(name: str) -> list[dict[str, str]]:
        return [{"ext": "vtt", "url": f"{name}.vtt"}, {"ext": "json3", "url": f"{name}.json3"}]

    assert pick_captions({"subtitles": {"de": track("de"), "en-GB": track("en-GB")}}) == (
        "en-GB.json3",
        False,
    )
    assert pick_captions({"subtitles": {"de": track("de")}}) == ("de.json3", False)
    auto = {"subtitles": {}, "automatic_captions": {"af": track("af"), "en-orig": track("eo")}}
    assert pick_captions(auto) == ("eo.json3", True)
    assert pick_captions({"subtitles": {}, "automatic_captions": {}}) is None
    assert canonical_url("dQw4w9WgXcQ") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_parse_json3_skips_empty_events() -> None:
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hello "}, {"utf8": "world."}]},
            {"tStartMs": 1500, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 2000},
            {"tStartMs": 61200, "dDurationMs": 2000, "segs": [{"utf8": "Later line"}]},
        ]
    }
    lines = parse_json3(json.dumps(data).encode())
    assert [(x.start_s, x.end_s, x.text) for x in lines] == [
        (0.0, 1.5, "Hello world."),
        (61.2, 63.2, "Later line"),
    ]
    assert parse_json3(b"not json") == []
