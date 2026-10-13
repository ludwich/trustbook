"""Validation of the model's forced-citation JSON (grounding layer 3).

Rules:
- The reply must be one JSON object with a ``sentences`` list (else :class:`InvalidOutputError`,
  which triggers the one retry).
- A sentence needs non-empty text and at least one chunk id. Every id must be one of the ids
  given in the prompt; a sentence citing any unknown id is dropped (it is not anchored to what
  the model was shown).
- Some models pack several sentences into one ``text`` with inline markers (``… 0.1 ["c1", "c2"].
  When …``). The text is split at each marker: the part before a marker cites that marker's ids,
  text after the last marker (or text without markers) cites the declared ``chunk_ids``. Markers
  are removed, so prompt labels never reach stored text. Each part is validated on its own.
- A part with no word character once Markdown code-fence markers are removed (``}]}]}```json{``)
  is JSON or Markdown syntax that leaked into a string, not a sentence: it is dropped like an
  uncited sentence (D-117). Strict JSON-schema output keeps the reply valid JSON, so a model that
  tries to close its answer and start another one can only write that syntax inside a ``text``.
- A sentence whose text equals an earlier sentence's text in the same reply (whitespace is
  already normalised) is merged into the earlier one: shown once, citing the earlier sentence's
  ids followed by the new ones (D-117). It is verified like every sentence; every merged id is
  one the model gave for that exact text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

OVERVIEW_QUESTIONS = 3
MAX_SENTENCE_CHARS = 2_000
MAX_QUESTION_CHARS = 300

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)
_ID_SPLIT = re.compile(r"[\s,;]+")
_MARKER_ID = r"""\s*["']?\s*c\d+\s*["']?\s*"""
# ["c1", "c2"], [c1], [c1, c2], ['c1'], (c1), (c1, c2): one or more prompt labels in brackets.
_INLINE_MARKER = re.compile(
    rf"\[(?:{_MARKER_ID})(?:[,;](?:{_MARKER_ID}))*\]|\((?:{_MARKER_ID})(?:[,;](?:{_MARKER_ID}))*\)",
    re.IGNORECASE,
)
_LABEL = re.compile(r"c\d+", re.IGNORECASE)
_AFTER_MARKER = re.compile(r"[.!?;:,]*")
_FENCE_MARKER = re.compile(r"`{3,}[A-Za-z0-9_+-]*")
_WORD = re.compile(r"\w")


class InvalidOutputError(ValueError):
    """The model's reply is not usable JSON in the required shape."""


@dataclass(frozen=True)
class DraftSentence:
    """A validated sentence; ``chunk_ids`` are prompt labels (``c1`` …), unique, in order."""

    text: str
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class ParsedAnswer:
    """``dropped``: parts without valid chunk ids, and syntax fragments (also listed in
    ``fragments``). ``duplicates``: parts merged into an earlier sentence with the same text."""

    sentences: list[DraftSentence]
    dropped: list[str] = field(default_factory=list)
    returned: int = 0  # sentences the model returned before dropping
    fragments: list[str] = field(default_factory=list)
    duplicates: int = 0


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


def split_inline_citations(text: str) -> tuple[list[tuple[str, list[str]]], str]:
    """Split ``text`` at inline citation markers.

    Returns ``(parts, rest)``: each part is ``(text before a marker, that marker's ids)``, with
    punctuation right after the marker kept on the part ("0.1 ["c1"]." → "0.1."); ``rest`` is the
    text after the last marker (all of ``text`` when there is none). Markers right after one
    another add their ids to the same part; a marker with no text before it is dropped.
    Whitespace is normalised and no marker is left in any returned text.
    """
    parts: list[tuple[str, list[str]]] = []
    position = 0
    for marker in _INLINE_MARKER.finditer(text):
        before = " ".join(text[position : marker.start()].split())
        ids = [normalise_id(m.group(0)) for m in _LABEL.finditer(marker.group(0))]
        punct = _AFTER_MARKER.match(text, marker.end())
        tail = punct.group(0) if punct else ""
        position = marker.end() + len(tail)
        if before.strip(" .!?;:,"):
            parts.append((before + tail, ids))
        elif parts:  # [c1][c2] or [c1], [c2]: more ids for the same part
            previous, previous_ids = parts[-1]
            base = previous.rstrip(".!?;:,")
            parts[-1] = (base + (tail or previous[len(base) :]), previous_ids + ids)
    rest = " ".join(text[position:].split())
    return [(t, list(dict.fromkeys(i))) for t, i in parts], rest


def is_syntax_fragment(text: str) -> bool:
    """True when ``text`` has no word character once code-fence markers (```` ```json ````) are
    removed: JSON or Markdown syntax such as ``}]}]}```json{``, never a statement (D-117)."""
    return _WORD.search(_FENCE_MARKER.sub("", text)) is None


def merge_duplicates(sentences: Sequence[DraftSentence]) -> tuple[list[DraftSentence], int]:
    """Keep the first of sentences with the same text, citing its ids and then the later ones'
    new ids; returns the sentences and how many were merged (D-117). Exact text match only (the
    texts are whitespace-normalised already). A cited sentence and an uncited one (an ideation
    suggestion) are never merged."""
    first: dict[tuple[str, bool], int] = {}
    out: list[DraftSentence] = []
    merged = 0
    for sentence in sentences:
        key = (sentence.text, bool(sentence.chunk_ids))
        if key not in first:
            first[key] = len(out)
            out.append(sentence)
            continue
        merged += 1
        index = first[key]
        ids = tuple(dict.fromkeys((*out[index].chunk_ids, *sentence.chunk_ids)))
        out[index] = DraftSentence(out[index].text, ids)
    return out, merged


def parse_sentences(data: Any, valid_ids: Collection[str]) -> ParsedAnswer:
    """Validate ``{"sentences": [...]}``: drop sentences without valid chunk ids and syntax
    fragments, merge repeated sentences."""
    if not isinstance(data, dict) or not isinstance(data.get("sentences"), list):
        raise InvalidOutputError('missing "sentences" list')
    valid = set(valid_ids)
    kept: list[DraftSentence] = []
    dropped: list[str] = []
    fragments: list[str] = []
    items: list[Any] = data["sentences"]
    for item in items:
        if not isinstance(item, dict):
            dropped.append(str(item)[:MAX_SENTENCE_CHARS])
            continue
        raw_text = item.get("text")
        text = " ".join(raw_text.split()) if isinstance(raw_text, str) else ""
        declared = _ids(item.get("chunk_ids"))
        parts, rest = split_inline_citations(text)
        candidates: list[tuple[str, list[str] | None]] = [(t, i) for t, i in parts]
        if rest.strip(" .!?;:,"):
            candidates.append((rest, declared))
        for part_text, ids in candidates:
            if not part_text:
                continue  # nothing to show or drop
            if is_syntax_fragment(part_text):
                fragments.append(part_text[:MAX_SENTENCE_CHARS])
                dropped.append(part_text[:MAX_SENTENCE_CHARS])
                continue
            if not ids or any(i not in valid for i in ids):
                dropped.append(part_text[:MAX_SENTENCE_CHARS])
                continue
            kept.append(DraftSentence(part_text[:MAX_SENTENCE_CHARS], tuple(dict.fromkeys(ids))))
    sentences, duplicates = merge_duplicates(kept)
    return ParsedAnswer(sentences, dropped, len(items), fragments, duplicates)


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
