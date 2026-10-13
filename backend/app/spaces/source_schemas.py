"""API shapes for sources, chunks and browsing (docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.jobs.handlers import JobStage

SourceType = Literal["upload", "folder", "url", "youtube", "search", "paste"]
SourceGroup = Literal["documents", "transcripts", "web", "code"]
SourceStatus = Literal["queued", "reading", "indexing", "ready", "failed"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]

MAX_PASTE_CHARS = 2_000_000


class AheadOut(BaseModel):
    """The linked folder's HEAD differs from the indexed commit (SPEC §7.5)."""

    indexed_commit: str
    head_commit: str
    commits_ahead: int | None  # None: can't be counted (e.g. a shallow clone without the commit)


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
    ahead: AheadOut | None = None
    # While reading or indexing: waiting for calibration or loading a model (D-113).
    stage: JobStage | None = None


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


class CodeContextOut(BaseModel):
    file: str
    commit: str | None
    language: str | None
    first_line: int
    lines: list[str]
    highlight: tuple[int, int]  # inclusive 1-based line range of the cited chunk


class TextContextOut(BaseModel):
    label: str | None
    before: str
    highlight: str
    after: str


class ChunkContextOut(BaseModel):
    """The source viewer's data for one cited chunk (docs/ARCHITECTURE.md, "Phase 2")."""

    chunk: ChunkOut
    source_type: str
    view: Literal["code", "text"]
    # Only the key that matches ``view`` is present in the JSON.
    code: CodeContextOut | None = Field(default=None, exclude_if=lambda v: v is None)
    text: TextContextOut | None = Field(default=None, exclude_if=lambda v: v is None)


class SecretFindingOut(BaseModel):
    """One possible secret: where it is, never what it is."""

    file: str
    line: int
    rule_id: str


class SecretsReportOut(BaseModel):
    files_skipped: int
    findings: list[SecretFindingOut]


class BrowseEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int | None


class BrowseOut(BaseModel):
    path: str
    parent: str | None
    entries: list[BrowseEntry]
