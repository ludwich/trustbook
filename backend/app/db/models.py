"""SQLModel table classes. Tables are created by SQL migrations, never by ``create_all``.

Global tables live in ``global.db``; the rest live in each space's ``space.db``.
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel

# --- global.db -----------------------------------------------------------------------------


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    kind: str
    title: str
    space_id: str | None = None
    payload: str = "{}"
    status: str = "queued"
    progress: float | None = None
    position: int
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class RequestLog(SQLModel, table=True):
    __tablename__ = "request_log"

    id: int | None = Field(default=None, primary_key=True)
    timestamp: str
    capability: str
    endpoint_host: str
    space_id: str | None = None
    space_name: str | None = None
    bytes_sent: int = 0
    tokens_sent: int | None = None
    excerpt: str = ""


GLOBAL_TABLES: tuple[type[SQLModel], ...] = (Job, RequestLog)

# --- space.db ------------------------------------------------------------------------------


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: str = Field(primary_key=True)
    type: str
    title: str
    origin: str | None = None
    snapshot_at: str | None = None
    commit_hash: str | None = None
    status: str = "queued"
    failure_reason: str | None = None
    failure_next_step: str | None = None
    quality_warning: str | None = None
    enabled: bool = True
    group: str
    stats: str = "{}"
    created_at: str
    updated_at: str
    job_id: str | None = None  # latest ingest job (migration 003)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"

    id: str = Field(primary_key=True)
    source_id: str = Field(foreign_key="sources.id")
    ordinal: int
    text: str
    start_offset: int
    end_offset: int
    locator: str = "{}"
    token_count: int
    # The chunk's parent (D-077, migration 005): its parent key and the JSON structure record
    # (app/chunking/structure.py). Navigation data for retrieval only, never citable.
    parent_key: str | None = None
    structure: str | None = None


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(primary_key=True)
    title: str = ""
    created_at: str
    updated_at: str


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id")
    role: str
    content: str = "{}"
    badge: str | None = None
    removed_sentences: str = "[]"
    created_at: str
    # Refused assistant messages only (D-118): not_in_sources, no_sources, invalid_output or
    # unsupported. NULL for answers, replies and refusals stored before migration 007.
    refusal_reason: str | None = None


class Citation(SQLModel, table=True):
    __tablename__ = "citations"

    id: int | None = Field(default=None, primary_key=True)
    message_id: str = Field(foreign_key="messages.id")
    sentence_index: int
    chunk_id: str
    verifier_score: float | None = None


class GateLog(SQLModel, table=True):
    __tablename__ = "gate_log"

    id: int | None = Field(default=None, primary_key=True)
    gate: str
    input: str
    decision: str
    confidence: float | None = None
    user_correction: str | None = None
    created: str


class IdeationSession(SQLModel, table=True):
    __tablename__ = "ideation_sessions"

    id: str = Field(primary_key=True)
    idea: str
    title: str = ""
    status: str = "active"
    created_at: str
    updated_at: str
    finished_at: str | None = None
    risks: str = "[]"  # JSON [{"text", "node_id"}] (migration 006)
    brief_at: str | None = None  # when ideas/<id>/brief.md was last written


class IdeationNode(SQLModel, table=True):
    __tablename__ = "ideation_nodes"

    id: str = Field(primary_key=True)
    session_id: str = Field(foreign_key="ideation_sessions.id")
    parent_id: str | None = Field(default=None, foreign_key="ideation_nodes.id")
    ordinal: int = 0
    title: str
    status: str = "tentative"
    question: str | None = None
    recommended_answer: str | None = None
    final_answer: str | None = None
    answer_source: str | None = None
    citations: str = "[]"
    created_at: str
    updated_at: str
    # Migration 006 (D-093 to D-101): seed or answer branch, the grounded recommendation (JSON),
    # when the question was asked and answered, and diagnostics (JSON, never shown).
    origin: str = "answer"
    recommendation: str | None = None
    asked_at: str | None = None
    answered_at: str | None = None
    diagnostics: str | None = None


class Overview(SQLModel, table=True):
    """The space overview (one row, id 1). ``sentences``/``questions`` are JSON."""

    __tablename__ = "overview"

    id: int = Field(default=1, primary_key=True)
    enabled: bool = True
    status: str = "none"
    sentences: str = "[]"
    questions: str = "[]"
    generated_at: str | None = None
    sources_signature: str | None = None
    job_id: str | None = None
    error: str | None = None
    updated_at: str


class SourceFile(SQLModel, table=True):
    """One file of a linked folder as last indexed (migration 004, incremental refresh)."""

    __tablename__ = "source_files"

    source_id: str = Field(primary_key=True, foreign_key="sources.id")
    path: str = Field(primary_key=True)
    sha256: str
    kind: str
    chunker: str
    start_offset: int
    end_offset: int
    chunk_ids: str = "[]"


SPACE_TABLES: tuple[type[SQLModel], ...] = (
    Source,
    Chunk,
    Conversation,
    Message,
    Citation,
    GateLog,
    IdeationSession,
    IdeationNode,
    Overview,
    SourceFile,
)
