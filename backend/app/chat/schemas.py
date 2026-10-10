"""API shapes for chat and the overview (docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Badge = Literal["verified", "partially_verified", "refused", "unverified"]
RefusalReason = Literal["not_in_sources", "no_sources", "invalid_output", "unsupported"]
Hint = Literal["ideation"]
GateDecision = Literal["sources", "brainstorm", "chitchat"]
GATE_DECISIONS: tuple[GateDecision, ...] = ("sources", "brainstorm", "chitchat")
Stage = Literal["retrieving", "generating", "checking"]
SentenceMethod = Literal["checker", "code_judge"]
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
    # "ideation" when the gate saw a brainstorming request (the UI points to the Ideation tab).
    hint: Hint | None = Field(default=None, exclude_if=lambda v: v is None)


class Diagnostics(BaseModel):
    """Scores behind an answer, for threshold tuning (the eval runner reads them; the UI doesn't).

    ``sentence_scores`` follows the model's cited sentences, removed ones included: the grounding
    checker's best score per sentence, 0.0 when a code check failed, None when the checker is off.
    ``sentence_methods`` (same order): what passed each sentence, ``checker`` or ``code_judge``
    (failed the checker, passed on the code judge, D-045); None for failed sentences and when the
    checker is off.
    """

    top_rerank: float | None = None
    sentence_scores: list[float | None] = Field(default_factory=list)
    regenerated: bool = False
    sentence_methods: list[SentenceMethod | None] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    created_at: str
    content: MessageContent
    badge: Badge | None
    removed_sentences: list[str]
    sources_used: list[SourceUsed]
    diagnostics: Diagnostics | None = None  # assistant messages only
    gate_log_id: int | None = None  # the gate decision behind an assistant message


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


class GateLogOut(BaseModel):
    id: int
    created: str
    input: str
    decision: str
    confidence: float | None
    user_correction: str | None


class GateCorrection(BaseModel):
    """``decision`` null clears the correction."""

    model_config = ConfigDict(extra="forbid")

    decision: GateDecision | None


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
