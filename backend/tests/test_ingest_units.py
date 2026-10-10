from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ingest import errors
from app.ingest.errors import IngestError, http_status_error
from app.ingest.readers import IngestTools, extension, parse_upload, read_youtube
from app.ingest.stt import EndpointStt, resolve_stt
from app.parse.documents import DocumentParser
from app.parse.web import WebFetcher
from tests.conftest import AppFactory
from tests.fakes import Router
from tests.ingest_helpers import (
    FakeStt,
    FakeYoutube,
    fake_scanner,
    public_resolver,
    services_of,
    speech_wav,
)


def _tools(tmp_path: Path, **overrides: Any) -> IngestTools:
    options: dict[str, Any] = {
        "browse_root": tmp_path,
        "documents": DocumentParser(tmp_path, lambda: 1, available=False),
        "web": WebFetcher(resolver=public_resolver),
        "youtube": FakeYoutube({}),
        "scanner": fake_scanner(),
        "stt": lambda: None,
    }
    options.update(overrides)
    return IngestTools(**options)


def _upload(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_extension() -> None:
    assert extension("Report.PDF") == "pdf"
    assert extension("Makefile") == ""


def test_parse_upload_dispatch(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    noop = lambda _f: None  # noqa: E731

    csv = parse_upload(
        tools, _upload(tmp_path, "t.csv", b"a,b\n1,2\n"), "t.csv", noop, lambda: None
    )
    assert csv.markdown == "a,b\n1,2"
    with pytest.raises(IngestError, match="empty"):
        parse_upload(tools, _upload(tmp_path, "e.txt", b""), None, noop, lambda: None)
    with pytest.raises(IngestError, match="can't read"):
        parse_upload(tools, _upload(tmp_path, "x.txt", b"\x00\x01\x02"), None, noop, lambda: None)
    with pytest.raises(IngestError, match="can't read"):
        parse_upload(tools, _upload(tmp_path, "x.zip", b"PK"), None, noop, lambda: None)
    with pytest.raises(IngestError, match="No text"):
        parse_upload(
            tools,
            _upload(tmp_path, "e.html", b"<html><body></body></html>"),
            None,
            noop,
            lambda: None,
        )


def test_read_youtube_auto_captions_warning(tmp_path: Path, router: Router) -> None:
    router.json(
        "GET",
        "https://www.youtube.com/api/timedtext",
        {"events": [{"tStartMs": 0, "dDurationMs": 900, "segs": [{"utf8": "hi there"}]}]},
    )
    info = {
        "title": "Auto",
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext?x=1"}]
        },
    }
    tools = _tools(
        tmp_path,
        web=WebFetcher(transport=router.transport, resolver=public_resolver),
        youtube=FakeYoutube(info),
    )
    snap = read_youtube(tools, "https://youtu.be/dQw4w9WgXcQ", lambda _f: None, lambda: None)
    assert snap.warnings == [errors.WARN_AUTO_CAPTIONS]
    assert snap.title == "Auto"
    with pytest.raises(IngestError, match="single video"):
        read_youtube(tools, "https://www.youtube.com/@chan", lambda _f: None, lambda: None)


def test_read_youtube_stt_path(tmp_path: Path) -> None:
    fake = FakeYoutube({"title": "T", "duration": 4}, audio=speech_wav(tmp_path / "a.wav"))
    stt = FakeStt()
    tools = _tools(tmp_path, youtube=fake, stt=lambda: stt)
    snap = read_youtube(tools, "https://youtu.be/dQw4w9WgXcQ", lambda _f: None, lambda: None)
    assert snap.stats["duration_s"] == 4.0
    assert stt.calls == 1


def test_stt_endpoint_mode_is_logged_and_errors_are_plain(
    make_client: AppFactory, router: Router, tmp_path: Path
) -> None:
    client: TestClient = make_client()
    services = services_of(client)
    assert (
        client.put(
            "/api/capabilities/stt",
            json={
                "mode": "endpoint",
                "preset": "custom",
                "base_url": "http://localhost:9000",
                "model": "w",
            },
        ).status_code
        == 200
    )
    router.json("POST", "http://localhost:9000/v1/audio/transcriptions", {"text": " Hi there. "})
    stt = resolve_stt(services.capabilities, services.providers, services.http)
    assert isinstance(stt, EndpointStt)
    assert stt.transcribe_wav(speech_wav(tmp_path / "a.wav")) == "Hi there."
    assert client.get("/api/request-log").json()[0]["capability"] == "stt"

    router.routes.clear()
    router.add("POST", "http://localhost:9000/", lambda _r: httpx.Response(500))
    with pytest.raises(IngestError, match="Speech recognition failed"):
        stt.transcribe_wav(tmp_path / "a.wav")


def test_stt_unavailable_without_runtime(make_client: AppFactory, paths: Any) -> None:
    from app.capabilities.bundled import ProviderRegistry

    client = make_client(
        providers=ProviderRegistry(paths.models_dir, runtime_check=lambda _k: False)
    )
    services = services_of(client)
    assert resolve_stt(services.capabilities, services.providers, services.http) is None


def test_http_status_errors_have_next_steps() -> None:
    for status in (401, 403, 404, 410, 418, 429, 500):
        error = http_status_error(status)
        assert error.reason and error.next_step
        assert str(status) in error.reason
