"""API shapes for chat and the overview (docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Badge = Literal["verified", "partially_verified", "refused", "unverified"]
RefusalReason = Literal["not_in_sources", "no_sources", "invalid_output"]
Stage = Literal["retrieving", "generating", "checking"]
OverviewStatus = Literal["none", "pending", "ready", "failed"]

MAX_QUESTION_CHARS = 4_000
TITLE_CHARS = 60


class CitationOut(BaseModel):
    n: int
    chunk_id: str
    source_id: str
    source_title: str
    locator: dict[str, Any]


class SentenceOut(BaseModel):
    text: str
    citations: list[CitationOut]


class SourceUsed(BaseModel):
    source_id: str
    title: str


class MessageContent(BaseModel):
    """``text`` for user messages and refusals (+ ``suggestion``); ``sentences`` for answers."""

    model_config = ConfigDict(extra="ignore")

    # Keys without a value are left out of the JSON (they are optional in the contract).
    text: str | None = Field(default=None, exclude_if=lambda v: v is None)
    suggestion: str | None = Field(default=None, exclude_if=lambda v: v is None)
    sentences: list[SentenceOut] | None = Field(default=None, exclude_if=lambda v: v is None)


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    created_at: str
    content: MessageContent
    badge: Badge | None
    removed_sentences: list[str]
    sources_used: list[SourceUsed]


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Type a question first.")
        return value.strip()


class ChatStatusOut(BaseModel):
    enabled_sources: int
    ready_sources: int
    total_sources: int
    chat_ready: bool


class Refusal(BaseModel):
    reason: RefusalReason
    message: str
    suggestion: str | None


class OverviewEstimate(BaseModel):
    tokens: int
    remote: bool


class OverviewOut(BaseModel):
    enabled: bool
    status: OverviewStatus
    sentences: list[SentenceOut]
    questions: list[str]
    generated_at: str | None
    stale: bool
    estimate: OverviewEstimate | None


class OverviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
