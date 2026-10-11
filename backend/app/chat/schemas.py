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


MAX_SEED = 2**32 - 1


class Generation(BaseModel):
    """Per-request generation settings (only the eval runner sends them, F5 / D-055).

    Left out, the answer uses the SPEC default (temperature 0.2, no seed).
    """

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)


class GenerationUsed(BaseModel):
    """The temperature and seed the answer call actually used."""

    temperature: float
    seed: int | None = None


class CitationChange(BaseModel):
    """How Trustbook changed one sentence's citations (chunk ids only, never text).

    ``added``: cited before verification because the sentence names something only they contain
    (D-056). ``dropped``: near-copies of a kept citation (D-052 extension). ``swapped``:
    ``[from, to]`` pairs, a near-copy replaced by the chunk that defines the named symbol.
    """

    sentence: int
    added: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    swapped: list[list[str]] = Field(default_factory=list)


class Diagnostics(BaseModel):
    """Scores behind an answer, for threshold tuning (the eval runner reads them; the UI doesn't).

    ``sentence_scores`` follows the model's cited sentences, removed ones included: the grounding
    checker's best score per sentence, 0.0 when a code check failed or the judge vetoed it, None
    when the checker is off.
    ``sentence_methods`` (same order): what passed each sentence, ``checker`` or ``code_judge``
    (failed the checker, passed on the judge, D-045/D-056); None for failed sentences and when
    the checker is off.
    ``judge_vetoed``: indices (same order) of sentences the checker passed and the judge's
    confirmation failed. ``judge_seconds``: time spent waiting for the judge (None: not called).
    ``retrieval_fingerprint``: a hash of the ordered context chunk ids (None: no context).
    ``generation``: what the answer call used (None: no answer call).
    """

    top_rerank: float | None = None
    sentence_scores: list[float | None] = Field(default_factory=list)
    regenerated: bool = False
    sentence_methods: list[SentenceMethod | None] = Field(default_factory=list)
    judge_vetoed: list[int] = Field(default_factory=list)
    judge_seconds: float | None = None
    citation_changes: list[CitationChange] = Field(default_factory=list)
    retrieval_fingerprint: str | None = None
    generation: GenerationUsed | None = None


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
    generation: Generation | None = None

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
