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

    ``dropped``: near-copies of a kept citation (D-052 extension). ``swapped``:
    ``[from, to]`` pairs, a near-copy replaced by the chunk that defines the named symbol.
    """

    sentence: int
    dropped: list[str] = Field(default_factory=list)
    swapped: list[list[str]] = Field(default_factory=list)


class FailedSentenceOut(BaseModel):
    """A sentence of the model's reply that failed verification and why (both capped at 300
    characters, D-075)."""

    text: str
    reason: str


class Timings(BaseModel):
    """Where an answer's time went, in seconds (D-080).

    ``generation_s``: each answer call (JSON retries and the regeneration included).
    ``verification_s``: each verification pass (first reply, regenerated reply).
    ``citation_s``: choosing the kept sentences' citations (None: not reached). ``gate_s``: the
    gate router before the answer (None: no gate call). ``total_s``: the answer pipeline from
    retrieval to the outcome (the gate not included).
    """

    retrieval_s: float | None = None
    generation_s: list[float] = Field(default_factory=list)
    verification_s: list[float] = Field(default_factory=list)
    citation_s: float | None = None
    gate_s: float | None = None
    total_s: float | None = None


class ExpansionOut(BaseModel):
    """A chunk promoted as parent context (D-077): its id, ``section_previous`` or
    ``class_head``, and the id of the context chunk it was promoted for (``referrer``)."""

    chunk_id: str
    kind: str
    referrer: str | None


class WindowOut(BaseModel):
    """A transcript window (D-091): the numbered chunks it names by their times only (its
    children, and any neighbour that is also numbered), the neighbours whose text it shows (time
    order) and the prompt tokens it adds. Never citable."""

    source_id: str
    numbered: list[str]
    chunk_ids: list[str]
    tokens: int


class Diagnostics(BaseModel):
    """Scores behind an answer, for threshold tuning (the eval runner reads them; the UI doesn't).

    ``sentence_scores`` follows the model's cited sentences, removed ones included: the grounding
    checker's best score per sentence, 0.0 when a code check failed or the judge vetoed it, None
    when the checker is off.
    ``sentence_methods`` (same order): what passed each sentence, ``checker`` or ``code_judge``
    (failed the checker, passed on the judge, D-045/D-063); None for failed sentences and when
    the checker is off.
    ``judge_vetoed``: indices (same order) of sentences the checker passed and the judge's
    confirmation failed. ``judge_seconds``: time spent waiting for the judge (None: not called).
    ``retrieval_fingerprint``: a hash of the ordered context chunk ids (None: no context).
    ``generation``: what the answer call used (None: no answer call).
    ``failed_sentences``: the failed sentences of the reply the outcome rests on (the second one
    after a regeneration, unless it was unusable), with the reason each failed (D-075).

    Latency (D-080): ``judge_calls``: every judge request for the answer (rescues, confirmations,
    solo re-asks, both passes, citation choice). ``judge_calls_per_pass``: those made by each
    verification pass (first reply, regenerated reply). ``answer_calls``: answer generation
    requests (JSON retries and the regeneration included). ``timings``: see :class:`Timings`.
    ``prompt_tokens`` (estimated, characters / 4) and ``completion_tokens`` (as the endpoint
    reports them, None when it does not): per answer call.

    Parent context (D-077): ``expansions``: the promoted chunks. ``promoted_tokens`` and
    ``outline_tokens``: their sizes. ``outline_fingerprint``: a hash of the outline text (None:
    no outline); ``retrieval_fingerprint`` still covers only the chunks. ``outline_only``: per
    verification pass, how many failed sentences miss a name or literal that the outline shows
    and no chunk holds. ``promoted_cited``: [kept sentences citing a promoted chunk, those of them
    also citing another chunk].

    Transcript windows (D-091): ``windows`` (see :class:`WindowOut`), ``window_tokens`` (their
    prompt tokens), ``window_fingerprint`` (a hash of their text; None: no window) and
    ``window_only``: per verification pass, how many failed sentences miss a name, or use a word,
    that a window shows and no chunk holds.
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
    failed_sentences: list[FailedSentenceOut] = Field(default_factory=list)
    judge_calls: int = 0
    judge_calls_per_pass: list[int] = Field(default_factory=list)
    answer_calls: int = 0
    timings: Timings | None = None
    prompt_tokens: list[int] = Field(default_factory=list)
    completion_tokens: list[int | None] = Field(default_factory=list)
    expansions: list[ExpansionOut] = Field(default_factory=list)
    promoted_tokens: int = 0
    outline_tokens: int = 0
    outline_fingerprint: str | None = None
    outline_only: list[int] = Field(default_factory=list)
    promoted_cited: list[int] = Field(default_factory=lambda: [0, 0])
    windows: list[WindowOut] = Field(default_factory=list)
    window_tokens: int = 0
    window_fingerprint: str | None = None
    window_only: list[int] = Field(default_factory=list)


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
