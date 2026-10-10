"""Validation of the model's forced-citation JSON (grounding layer 3).

Rules:
- The reply must be one JSON object with a ``sentences`` list (else :class:`InvalidOutputError`,
  which triggers the one retry).
- A sentence needs non-empty text and at least one chunk id. Every id must be one of the ids
  given in the prompt; a sentence citing any unknown id is dropped (it is not anchored to what
  the model was shown).
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

OVERVIEW_QUESTIONS = 3
MAX_SENTENCE_CHARS = 2_000
MAX_QUESTION_CHARS = 300

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)
_ID_SPLIT = re.compile(r"[\s,;]+")


class InvalidOutputError(ValueError):
    """The model's reply is not usable JSON in the required shape."""


@dataclass(frozen=True)
class DraftSentence:
    """A validated sentence; ``chunk_ids`` are prompt labels (``c1`` …), unique, in order."""

    text: str
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class ParsedAnswer:
    sentences: list[DraftSentence]
    dropped: list[str] = field(default_factory=list)
    returned: int = 0  # sentences the model returned before dropping


@dataclass(frozen=True)
class ParsedOverview:
    answer: ParsedAnswer
    questions: list[str]


def extract_json(text: str) -> Any:
    """Parse the reply as JSON; tolerates a Markdown code fence and text around one object."""
    stripped = text.strip()
    if not stripped:
        raise InvalidOutputError("empty reply")
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except ValueError:
            pass
    raise InvalidOutputError("not JSON")


def normalise_id(value: str) -> str:
    """``" [C2] "`` → ``"c2"`` (formatting only; the id must still be one we gave)."""
    return value.strip().strip("[]()").strip().lower()


def _ids(raw: Any) -> list[str] | None:
    """Chunk ids from the model's ``chunk_ids`` value; None when it has the wrong type."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    ids: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        ids.extend(normalise_id(part) for part in _ID_SPLIT.split(item) if part.strip())
    return [i for i in ids if i]


def parse_sentences(data: Any, valid_ids: Collection[str]) -> ParsedAnswer:
    """Validate ``{"sentences": [...]}`` and drop sentences without valid chunk ids."""
    if not isinstance(data, dict) or not isinstance(data.get("sentences"), list):
        raise InvalidOutputError('missing "sentences" list')
    valid = set(valid_ids)
    kept: list[DraftSentence] = []
    dropped: list[str] = []
    items: list[Any] = data["sentences"]
    for item in items:
        if not isinstance(item, dict):
            dropped.append(str(item)[:MAX_SENTENCE_CHARS])
            continue
        raw_text = item.get("text")
        text = " ".join(raw_text.split()) if isinstance(raw_text, str) else ""
        ids = _ids(item.get("chunk_ids"))
        if not text:
            continue  # nothing to show or drop
        if not ids or any(i not in valid for i in ids):
            dropped.append(text[:MAX_SENTENCE_CHARS])
            continue
        kept.append(DraftSentence(text[:MAX_SENTENCE_CHARS], tuple(dict.fromkeys(ids))))
    return ParsedAnswer(kept, dropped, len(items))


def parse_answer(text: str, valid_ids: Collection[str]) -> ParsedAnswer:
    """Parse and validate an answer reply."""
    return parse_sentences(extract_json(text), valid_ids)


def parse_overview(text: str, valid_ids: Collection[str]) -> ParsedOverview:
    """Parse an overview reply: cited sentences plus exactly three questions."""
    data = extract_json(text)
    answer = parse_sentences(data, valid_ids)
    raw = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise InvalidOutputError('missing "questions" list')
    questions = [
        " ".join(q.split())[:MAX_QUESTION_CHARS] for q in raw if isinstance(q, str) and q.strip()
    ]
    questions = list(dict.fromkeys(questions))
    if len(questions) < OVERVIEW_QUESTIONS:
        raise InvalidOutputError("fewer than three questions")
    return ParsedOverview(answer, questions[:OVERVIEW_QUESTIONS])
