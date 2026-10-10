"""Phase 1 routes: sources, web search and passages (see docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, BinaryIO
from urllib.parse import urlsplit

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import ServicesDep
from app.capabilities.adapters.factory import build_search_adapter
from app.capabilities.http import space_context
from app.db.models import Source
from app.ingest import errors
from app.ingest.browse_root import BrowsePathError, BrowseRootMissingError, resolve_in_root
from app.ingest.errors import IngestError, fail
from app.ingest.readers import UPLOAD_EXTENSIONS, extension, original_file
from app.ingest.service import RefreshNotAllowedError
from app.parse.audio import AUDIO_EXTENSIONS
from app.parse.snapshot import normalise_text, read_snapshot_files, write_snapshot
from app.parse.text import text_snapshot
from app.parse.youtube import canonical_url, is_youtube_url, youtube_video_id
from app.spaces.source_schemas import (
    ChunkOut,
    EnabledUpdate,
    FolderCreate,
    FromSearchCreate,
    PasteCreate,
    SnapshotOut,
    SourceOut,
    SourceUpdate,
    UrlCreate,
    WebSearchOut,
    WebSearchRequest,
    WebSearchResultOut,
)
from app.spaces.sources import ChunkNotFoundError, SourceNotFoundError, SourceRepository
from app.timeutil import utc_now_iso

router = APIRouter()

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
# Served inline only when the browser cannot run them as a page on our origin.
INLINE_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "markdown": "text/plain; charset=utf-8",
    "csv": "text/plain; charset=utf-8",
    "tsv": "text/plain; charset=utf-8",
    "log": "text/plain; charset=utf-8",
    "rst": "text/plain; charset=utf-8",
    "json": "text/plain; charset=utf-8",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "opus": "audio/ogg",
    "aac": "audio/aac",
}


@contextlib.contextmanager
def _not_found() -> Iterator[None]:
    try:
        yield
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Source not found.") from exc
    except ChunkNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Passage not found.") from exc


def _repo(services: ServicesDep, space_id: str) -> SourceRepository:
    return services.ingest.repo(space_id)


def _web_url(raw: str) -> str:
    url = raw.strip()
    if "://" not in url and "." in url.split("/", 1)[0]:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(
            status_code=422, detail="Enter a web address starting with http:// or https://."
        )
    return url


def _paste_title(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned if len(cleaned) <= 80 else cleaned[:79].rstrip() + "…"
    return "Pasted text"


def _copy_limited(source: BinaryIO, dest: Path) -> None:
    written = 0
    with dest.open("wb") as out:
        while chunk := source.read(COPY_CHUNK):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise IngestError(
                    "This file is larger than the 200 MB limit.",
                    "Split it into smaller files and upload those.",
                )
            out.write(chunk)


# --- listing and creation ------------------------------------------------------------------


@router.get("/spaces/{space_id}/sources")
def list_sources(space_id: str, services: ServicesDep) -> list[SourceOut]:
    return [services.ingest.out(s) for s in _repo(services, space_id).list_all()]


@router.post("/spaces/{space_id}/sources/paste", status_code=status.HTTP_201_CREATED)
def add_paste(space_id: str, body: PasteCreate, services: ServicesDep) -> SourceOut:
    text = normalise_text(body.text)
    if not text.strip():
        raise HTTPException(status_code=422, detail="Paste some text first.")
    title = body.title or _paste_title(text)
    snapshot = text_snapshot(text, title)
    source = services.ingest.create(
        space_id,
        type="paste",
        title=title,
        origin=None,
        group="documents",
        prepare=lambda folder: write_snapshot(folder, snapshot),
        snapshot_at=utc_now_iso(),
    )
    return services.ingest.out(source)


@router.post("/spaces/{space_id}/sources/upload", status_code=status.HTTP_201_CREATED)
def add_uploads(
    space_id: str, files: Annotated[list[UploadFile], File()], services: ServicesDep
) -> list[SourceOut]:
    services.spaces.get(space_id)
    created: list[Source] = []
    for upload in files:
        name = re.split(r"[\\/]", upload.filename or "")[-1].strip() or "upload"
        ext = extension(name)

        def prepare(folder: Path, upload: UploadFile = upload, ext: str = ext) -> None:
            if ext not in UPLOAD_EXTENSIONS:
                raise fail(errors.UNSUPPORTED_FILE)
            _copy_limited(upload.file, folder / f"original.{ext}")

        created.append(
            services.ingest.create(
                space_id,
                type="upload",
                title=name[:200],
                origin=name,
                group="transcripts" if ext in AUDIO_EXTENSIONS else "documents",
                prepare=prepare,
            )
        )
    return [services.ingest.out(s) for s in created]


def _create_web(services: ServicesDep, space_id: str, url: str, kind: str, title: str) -> Source:
    video = youtube_video_id(url)
    if kind == "url" and video is not None:
        origin = canonical_url(video)
        return services.ingest.create(
            space_id, type="youtube", title=origin, origin=origin, group="transcripts"
        )
    if is_youtube_url(url) and video is None and kind == "url":
        raise HTTPException(status_code=422, detail=errors.NOT_YOUTUBE_VIDEO[0])
    return services.ingest.create(space_id, type=kind, title=title or url, origin=url, group="web")


@router.post("/spaces/{space_id}/sources/url", status_code=status.HTTP_201_CREATED)
def add_url(space_id: str, body: UrlCreate, services: ServicesDep) -> SourceOut:
    services.spaces.get(space_id)
    url = _web_url(body.url)
    return services.ingest.out(_create_web(services, space_id, url, "url", url))


@router.post("/spaces/{space_id}/sources/from-search", status_code=status.HTTP_201_CREATED)
def add_from_search(
    space_id: str, body: FromSearchCreate, services: ServicesDep
) -> list[SourceOut]:
    services.spaces.get(space_id)
    urls = [(_web_url(item.url), item.title) for item in body.items]
    return [
        services.ingest.out(_create_web(services, space_id, url, "search", title))
        for url, title in urls
    ]


@router.post("/spaces/{space_id}/sources/folder", status_code=status.HTTP_201_CREATED)
def add_folder(space_id: str, body: FolderCreate, services: ServicesDep) -> SourceOut:
    services.spaces.get(space_id)
    try:
        resolved, relative = resolve_in_root(services.paths.browse_root, body.path)
    except BrowseRootMissingError as exc:
        raise HTTPException(status_code=404, detail=_ROOT_MISSING) from exc
    except BrowsePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="This folder does not exist.") from exc
    if not relative:
        raise HTTPException(status_code=400, detail="Choose a folder inside your home folder.")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="This is a file, not a folder.")
    source = services.ingest.create(
        space_id,
        type="folder",
        title=relative.rsplit("/", 1)[-1],
        origin=relative,
        group="code",
    )
    return services.ingest.out(source)


_ROOT_MISSING = (
    "Your home folder is not available to Trustbook. Check TRUSTBOOK_BROWSE_ROOT in the .env file."
)


@router.post("/spaces/{space_id}/web-search")
def web_search(space_id: str, body: WebSearchRequest, services: ServicesDep) -> WebSearchOut:
    record = services.spaces.get(space_id)
    settings = services.capabilities.stored("web_search")
    if not settings.enabled:
        raise HTTPException(status_code=409, detail="Web search is turned off in Settings.")
    provider = settings.provider or "searxng"
    if provider != "searxng" and not settings.api_key:
        raise HTTPException(status_code=409, detail="Add the search API key in Settings first.")
    adapter = build_search_adapter(
        provider,
        settings.api_key,
        services.http,
        searxng_url=settings.base_url or services.paths.searxng_url,
    )
    with space_context(record.id, record.name):
        results = adapter.search(body.query, max(1, min(20, body.limit)))
    return WebSearchOut(
        results=[WebSearchResultOut(title=r.title, url=r.url, snippet=r.snippet) for r in results]
    )


# --- changes ---------------------------------------------------------------------------------


@router.put("/spaces/{space_id}/sources/enabled")
def set_enabled(space_id: str, body: EnabledUpdate, services: ServicesDep) -> list[SourceOut]:
    with _not_found():
        sources = _repo(services, space_id).set_enabled(body.source_ids, body.enabled)
    return [services.ingest.out(s) for s in sources]


@router.patch("/spaces/{space_id}/sources/{source_id}")
def update_source(
    space_id: str, source_id: str, body: SourceUpdate, services: ServicesDep
) -> SourceOut:
    repo = _repo(services, space_id)
    values = body.model_dump(exclude_none=True)
    with _not_found():
        source = repo.update(source_id, **values) if values else repo.get(source_id)
    return services.ingest.out(source)


@router.post("/spaces/{space_id}/sources/{source_id}/refresh")
def refresh_source(space_id: str, source_id: str, services: ServicesDep) -> SourceOut:
    with _not_found():
        try:
            source = services.ingest.refresh(space_id, source_id)
        except RefreshNotAllowedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return services.ingest.out(source)


@router.delete("/spaces/{space_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(space_id: str, source_id: str, services: ServicesDep) -> Response:
    with _not_found():
        services.ingest.delete(space_id, source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- reading -------------------------------------------------------------------------------


@router.get("/spaces/{space_id}/sources/{source_id}/snapshot")
def get_snapshot(space_id: str, source_id: str, services: ServicesDep) -> SnapshotOut:
    repo = _repo(services, space_id)
    with _not_found():
        repo.get(source_id)
    stored = read_snapshot_files(repo.folder(source_id))
    if stored is None:
        raise HTTPException(status_code=404, detail="This source has no snapshot yet.")
    return SnapshotOut(markdown=stored[0], map=stored[1])


@router.get("/spaces/{space_id}/sources/{source_id}/original")
def get_original(space_id: str, source_id: str, services: ServicesDep) -> FileResponse:
    repo = _repo(services, space_id)
    with _not_found():
        source = repo.get(source_id)
    path = original_file(repo.folder(source_id)) if source.type == "upload" else None
    if path is None:
        raise HTTPException(status_code=404, detail="This source has no original file.")
    ext = extension(path.name)
    filename = source.origin or path.name
    inline = INLINE_TYPES.get(ext)
    return FileResponse(
        path,
        media_type=inline or "application/octet-stream",
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.get("/spaces/{space_id}/chunks/{chunk_id}")
def get_chunk(space_id: str, chunk_id: str, services: ServicesDep) -> ChunkOut:
    with _not_found():
        chunk, source = _repo(services, space_id).chunk(chunk_id)
    try:
        locator = json.loads(chunk.locator or "{}")
    except ValueError:
        locator = {}
    return ChunkOut(
        id=chunk.id,
        source_id=chunk.source_id,
        source_title=source.title,
        text=chunk.text,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        locator=locator if isinstance(locator, dict) else {},
    )
