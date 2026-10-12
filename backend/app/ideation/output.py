"""Validation of the engine's JSON replies.

Seed: ``{"title", "branches": [str]}`` with at least 3 distinct titles (else
:class:`~app.chat.output.InvalidOutputError`, which triggers the one retry).

Turn: ``{"question", "recommendation": {"sentences": [...]}, "new_branches", "risks"}``.
Recommendation sentences differ from chat answers in one way only (D-094): a sentence with an
empty ``chunk_ids`` list is kept as a *suggestion* (chat drops it). A sentence citing any id that
was not in the prompt is dropped, never turned into a suggestion. Inline citation markers are
split as in chat (:func:`~app.chat.output.split_inline_citations`), so a sentence carrying a
marker counts as cited. Markers are removed from the question, branch titles and risks, which
are never shown as grounded.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from app.chat.output import (
    MAX_SENTENCE_CHARS,
    DraftSentence,
    InvalidOutputError,
    extract_json,
    normalise_id,
    split_inline_citations,
)
from app.ideation import map as branch_map

MAX_QUESTION_CHARS = 500
MAX_RECOMMENDATION_SENTENCES = 5
MAX_RISKS_PER_TURN = 2
MAX_PROPOSED_BRANCHES = 4  # read at most this many; the map's caps decide what is added


@dataclass(frozen=True)
class ParsedSeed:
    title: str
    branches: list[str]


@dataclass(frozen=True)
class ParsedTurn:
    """``sentences`` in the model's order: cited ones (labels in ``chunk_ids``) and suggestions
    (empty ``chunk_ids``). ``branches``: ``(title, parent label or None)``."""

    question: str
    sentences: list[DraftSentence] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    branches: list[tuple[str, str | None]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def cited(self) -> list[DraftSentence]:
        return [s for s in self.sentences if s.chunk_ids]


def plain(text: str) -> str:
    """``text`` on one line without inline citation markers (``[c1]``)."""
    parts, rest = split_inline_citations(" ".join(text.split()))
    return " ".join([*(t for t, _ in parts), rest]).strip()


def parse_seed(text: str) -> ParsedSeed:
    data = extract_json(text)
    if not isinstance(data, dict) or not isinstance(data.get("branches"), list):
        raise InvalidOutputError('missing "branches" list')
    raw_title = data.get("title")
    title = branch_map.clean_title(plain(raw_title), 60) if isinstance(raw_title, str) else ""
    branches = branch_map.seed_titles(plain(b) for b in data["branches"] if isinstance(b, str))
    if len(branches) < branch_map.MIN_SEEDS:
        raise InvalidOutputError("fewer than three branches")
    return ParsedSeed(title, branches)


def _ids(raw: Any) -> list[str] | None:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(i, str) for i in raw):
        return None
    return [normalise_id(i) for i in raw if normalise_id(i)]


def _recommendation(raw: Any, valid: set[str]) -> tuple[list[DraftSentence], list[str]]:
    items: Any = raw.get("sentences") if isinstance(raw, dict) else raw
    if items is None:
        return [], []
    if not isinstance(items, list):
        raise InvalidOutputError('"recommendation" has no "sentences" list')
    kept: list[DraftSentence] = []
    dropped: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            dropped.append(str(item)[:MAX_SENTENCE_CHARS])
            continue
        declared = _ids(item.get("chunk_ids"))
        parts, rest = split_inline_citations(" ".join(item["text"].split()))
        candidates: list[tuple[str, list[str] | None]] = [(t, i) for t, i in parts]
        if rest.strip(" .!?;:,"):
            candidates.append((rest, declared))
        for part, ids in candidates:
            if ids is None or any(i not in valid for i in ids):
                dropped.append(part[:MAX_SENTENCE_CHARS])
                continue
            kept.append(DraftSentence(part[:MAX_SENTENCE_CHARS], tuple(dict.fromkeys(ids))))
    return kept[:MAX_RECOMMENDATION_SENTENCES], dropped


def parse_turn(text: str, valid_ids: Collection[str], *, expect_question: bool) -> ParsedTurn:
    """Validate a turn reply. ``expect_question``: a branch was named, so the question must not
    be empty (when nothing was left to ask, the question and recommendation are ignored)."""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise InvalidOutputError("not a JSON object")
    raw_question = data.get("question")
    question = plain(raw_question)[:MAX_QUESTION_CHARS] if isinstance(raw_question, str) else ""
    sentences: list[DraftSentence] = []
    dropped: list[str] = []
    if expect_question:
        if not question:
            raise InvalidOutputError('missing "question"')
        sentences, dropped = _recommendation(data.get("recommendation"), set(valid_ids))
    else:
        question = ""
    branches: list[tuple[str, str | None]] = []
    raw_branches = data.get("new_branches")
    for item in raw_branches if isinstance(raw_branches, list) else []:
        if isinstance(item, str):
            title, parent = item, None
        elif isinstance(item, dict) and isinstance(item.get("title"), str):
            raw_parent = item.get("parent")
            title = item["title"]
            parent = normalise_id(raw_parent) if isinstance(raw_parent, str) else None
        else:
            continue
        cleaned = plain(title)
        if cleaned:
            branches.append((cleaned, parent or None))
        if len(branches) == MAX_PROPOSED_BRANCHES:
            break
    raw_risks = data.get("risks")
    risks = [plain(r) for r in (raw_risks if isinstance(raw_risks, list) else [])]
    risks = [r for r in dict.fromkeys(r for r in risks if r)][:MAX_RISKS_PER_TURN]
    return ParsedTurn(question, sentences, dropped, branches, risks)
