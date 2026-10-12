"""API shapes for ideation (docs/ARCHITECTURE.md, "Phase 4: ideation")."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.chat.schemas import CitationOut

SessionStatus = Literal["active", "finished"]
NodeStatus = Literal["tentative", "open", "resolved", "dismissed"]
NodeOrigin = Literal["seed", "answer"]
AnswerSource = Literal["user", "accepted", "skipped"]
AnswerAction = Literal["answer", "accept", "skip"]
# cited: every shown sentence cites verified chunks; partly_cited: some do; suggestion: none do.
Grounding = Literal["cited", "partly_cited", "suggestion"]
# As in chat: verified (no cited sentence removed), partially_verified (some removed),
# unverified (grounding checker off); None when the recommendation had no cited sentence.
RecommendationBadge = Literal["verified", "partially_verified", "unverified"]
SentenceKind = Literal["cited", "suggestion"]

MAX_IDEA_CHARS = 4_000
MAX_ANSWER_CHARS = 4_000


class RecommendationSentence(BaseModel):
    """One sentence of a recommended answer. ``kind`` "cited": verified against ``citations``;
    "suggestion": the model's own idea, no citations, labelled "suggestion" in the UI."""

    text: str
    citations: list[CitationOut] = Field(default_factory=list)
    kind: SentenceKind


class Recommendation(BaseModel):
    """The recommended answer of a question. ``removed``: cited sentences that failed
    verification (layers 4-5); shown in the "Unverified, not from your sources" drawer, never as
    suggestions."""

    grounding: Grounding
    badge: RecommendationBadge | None = None
    sentences: list[RecommendationSentence] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


class Risk(BaseModel):
    text: str
    node_id: str | None = None


class NodeOut(BaseModel):
    """A branch of the map. Nodes come in tree pre-order; ``depth`` 0 is a seed's level."""

    id: str
    parent_id: str | None
    ordinal: int
    depth: int
    title: str
    status: NodeStatus
    origin: NodeOrigin
    question: str | None
    recommendation: Recommendation | None
    final_answer: str | None
    answer_source: AnswerSource | None
    asked_at: str | None
    answered_at: str | None
    dismissible: bool


class Progress(BaseModel):
    """``open`` counts tentative and open branches; dismissed ones are not counted."""

    resolved: int
    open: int


class SessionSummary(BaseModel):
    id: str
    title: str
    idea: str
    status: SessionStatus
    progress: Progress
    complete: bool
    created_at: str
    updated_at: str
    finished_at: str | None
    brief_at: str | None


class SessionOut(SessionSummary):
    nodes: list[NodeOut]
    current_node_id: str | None
    risks: list[Risk]


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea: str = Field(min_length=1, max_length=MAX_IDEA_CHARS)

    @field_validator("idea")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe your idea first.")
        return value.strip()


class AnswerCreate(BaseModel):
    """``text`` is required for ``answer`` and must be left out for ``accept`` and ``skip``."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64)
    action: AnswerAction
    text: str | None = Field(default=None, max_length=MAX_ANSWER_CHARS)

    @model_validator(mode="after")
    def _text_matches_action(self) -> AnswerCreate:
        if self.action == "answer":
            if self.text is None or not self.text.strip():
                raise ValueError("Type an answer first.")
            self.text = self.text.strip()
        elif self.text is not None:
            raise ValueError("Only an answer has text.")
        return self


class EstimateOut(BaseModel):
    """Approximate tokens sent per question (and once for the first branches); ``remote``: the
    chat endpoint is not on this computer, so the UI shows the estimate first (SPEC §11)."""

    tokens_per_turn: int
    seed_tokens: int
    remote: bool
