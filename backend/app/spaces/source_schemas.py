"""API shapes for sources, chunks and browsing (docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

SourceType = Literal["upload", "folder", "url", "youtube", "search", "paste"]
SourceGroup = Literal["documents", "transcripts", "web", "code"]
SourceStatus = Literal["queued", "reading", "indexing", "ready", "failed"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]

MAX_PASTE_CHARS = 2_000_000


class SourceOut(BaseModel):
    id: str
    type: SourceType
    title: str
    origin: str | None
    group: SourceGroup
    status: SourceStatus
    progress: float | None
    failure_reason: str | None
    failure_next_step: str | None
    quality_warning: str | None
    enabled: bool
    snapshot_at: str | None
    commit_hash: str | None
    stats: dict[str, Any]
    created_at: str
    job_id: str | None


class PasteCreate(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    text: Annotated[str, StringConstraints(max_length=MAX_PASTE_CHARS)]


class UrlCreate(BaseModel):
    url: Url


class FolderCreate(BaseModel):
    path: Annotated[str, StringConstraints(max_length=4096)]


class WebSearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    limit: int = 10


class WebSearchResultOut(BaseModel):
    title: str
    url: str
    snippet: str


class WebSearchOut(BaseModel):
    results: list[WebSearchResultOut]


class SearchItem(BaseModel):
    url: Url
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class FromSearchCreate(BaseModel):
    items: list[SearchItem] = Field(min_length=1, max_length=50)


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    title: Title | None = None


class EnabledUpdate(BaseModel):
    source_ids: list[str] = Field(max_length=10_000)
    enabled: bool


class SnapshotOut(BaseModel):
    markdown: str
    map: dict[str, Any]


class ChunkOut(BaseModel):
    id: str
    source_id: str
    source_title: str
    text: str
    start_offset: int
    end_offset: int
    locator: dict[str, Any]


class BrowseEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int | None


class BrowseOut(BaseModel):
    path: str
    parent: str | None
    entries: list[BrowseEntry]
