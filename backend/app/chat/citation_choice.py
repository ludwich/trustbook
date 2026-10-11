"""Which chunks a sentence cites: the choice after verification (D-052 and its extension).

The model's citations are never completed before verification: a sentence naming a backticked
identifier that its cited chunks lack fails layer 5, even when another context chunk holds the
name (D-064). For a sentence the checker passed:

1. D-052: when some cited chunks support it on their own, only those stay cited.
2. Near-copies: of the chunks that each support it alone, one whose supporting window
   (:func:`~app.grounding.code_checks.support_window`) is at least :data:`COPY_CONTAINMENT`
   contained in the window of a chunk already kept is dropped. Chunks are kept in order of
   preference: the chunk whose ``locator.symbol`` the sentence names (the definition), then the one
   the question names, then the higher rerank score for the question, then context order.
3. Upgrade: a kept citation whose symbol the sentence does not name may be swapped for a
   better-ranked context chunk whose symbol it does name, when the cited window is a near-copy of
   that chunk's window **and** that chunk passes verification alone (checker or judge, no layer 5
   problems). See :func:`upgrade_candidates`.

Citations are only ever removed when another kept citation supports the sentence alone, and only
swapped in (upgrade) when the new chunk passes verification alone. A choice that
would leave a backticked name or a literal outside the kept chunks is not made.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from app.chat.output import DraftSentence
from app.grounding.code_checks import missing_identifiers, missing_literals, support_window
from app.retrieval.service import RetrievedChunk

COPY_CONTAINMENT = 0.9  # share of a window's word 3-grams found in another window: a near-copy
MIN_WINDOW_SHINGLES = 4  # shorter windows say too little to call two chunks copies
SHINGLE_WORDS = 3
MIN_BARE_NAME = 4  # a symbol's last part counts as named, unquoted, from this length on

_SHINGLE_WORD = re.compile(r"[A-Za-z0-9_]+")


def _file(chunk: RetrievedChunk) -> str | None:
    file = chunk.locator.get("file")
    return file if isinstance(file, str) and file else None


# --- near-copies -----------------------------------------------------------------------------


def shingles(text: str) -> set[tuple[str, ...]]:
    """Word 3-grams of ``text`` (lower case; a shorter text is one shingle)."""
    words = [w.lower() for w in _SHINGLE_WORD.findall(text)]
    if len(words) < SHINGLE_WORDS:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def containment(part: str, whole: str) -> float:
    """Share of ``part``'s shingles that occur in ``whole`` (0 when ``part`` has none)."""
    found = shingles(part)
    if not found:
        return 0.0
    return len(found & shingles(whole)) / len(found)


def _word(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)", text) is not None


def names_symbol(text: str, chunk: RetrievedChunk) -> bool:
    """Whether ``text`` names the symbol the chunk defines (``locator.symbol``).

    The full dotted name, its last two parts (``Response.ok``), the last part in backticks, or a
    last part of at least :data:`MIN_BARE_NAME` characters as a whole word.
    """
    symbol = chunk.locator.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return False
    parts = symbol.split(".")
    last = parts[-1]
    if _word(symbol, text):
        return True
    if len(parts) > 2 and _word(".".join(parts[-2:]), text):
        return True
    if f"`{last}`" in text or f"`{last}()`" in text:
        return True
    return len(last) >= MIN_BARE_NAME and _word(last, text)


def _copy_of(window: str, others: Sequence[str]) -> bool:
    if len(shingles(window)) < MIN_WINDOW_SHINGLES:
        return False
    return any(containment(window, other) >= COPY_CONTAINMENT for other in others)


def _grounded(text: str, chunks: Sequence[RetrievedChunk]) -> bool:
    """Layer 5's name and literal checks still pass on ``chunks`` (a choice must keep them)."""
    texts = [c.text for c in chunks]
    if missing_identifiers(text, texts):
        return False
    return not (any(_file(c) for c in chunks) and missing_literals(text, texts))


@dataclass(frozen=True)
class Choice:
    """A checker-passed sentence after the choice, its per-chunk scores, and what changed."""

    sentence: DraftSentence
    scores: tuple[float | None, ...]
    dropped: tuple[str, ...] = ()


def choose(
    question: str,
    sentence: DraftSentence,
    scores: tuple[float | None, ...],
    chunks: Mapping[str, RetrievedChunk],
    threshold: float | None,
) -> Choice:
    """Steps 1 and 2 of the module docstring for one sentence the checker passed."""
    if threshold is None or len(sentence.chunk_ids) < 2:
        return Choice(sentence, scores)
    by_label = dict(zip(sentence.chunk_ids, scores, strict=True))
    alone = [label for label, s in by_label.items() if s is not None and s >= threshold]
    if not alone:
        return Choice(sentence, scores)  # supported only together: every citation stays
    position = {label: i for i, label in enumerate(chunks)}

    def preference(label: str) -> tuple[bool, bool, float, int]:
        chunk = chunks[label]
        return (
            not names_symbol(sentence.text, chunk),
            not names_symbol(question, chunk),
            -chunk.score,
            position[label],
        )

    kept: list[str] = []
    windows: list[str] = []
    for label in sorted(alone, key=preference):
        window = support_window(sentence.text, chunks[label].text)
        if kept and _copy_of(window, windows):
            continue
        kept.append(label)
        windows.append(window)
    final = [label for label in sentence.chunk_ids if label in kept]
    if not _grounded(sentence.text, [chunks[label] for label in final]):
        final = alone  # D-052 alone, if that keeps the names and literals
        if not _grounded(sentence.text, [chunks[label] for label in final]):
            return Choice(sentence, scores)
    dropped = tuple(label for label in alone if label not in final)
    return Choice(
        replace(sentence, chunk_ids=tuple(final)),
        tuple(by_label[label] for label in final),
        dropped,
    )


# --- upgrade to the defining chunk -----------------------------------------------------------


def upgrade_candidates(
    sentence: DraftSentence, chunks: Mapping[str, RetrievedChunk]
) -> list[tuple[str, str]]:
    """``(cited, candidate)`` pairs for step 3: at most one candidate per cited chunk.

    The candidate is a context chunk the sentence does not cite, whose symbol the sentence names
    while it does not name the cited chunk's, ranked higher for the question, and whose window
    contains the cited chunk's window (a near-copy). It still has to pass verification alone.
    """
    pairs: list[tuple[str, str]] = []
    taken: set[str] = set()
    for label in sentence.chunk_ids:
        cited = chunks[label]
        if names_symbol(sentence.text, cited):
            continue
        window = support_window(sentence.text, cited.text)
        if len(shingles(window)) < MIN_WINDOW_SHINGLES:
            continue
        for other, chunk in chunks.items():
            if other in sentence.chunk_ids or other in taken or chunk.score <= cited.score:
                continue
            if not names_symbol(sentence.text, chunk):
                continue
            if containment(window, support_window(sentence.text, chunk.text)) >= COPY_CONTAINMENT:
                pairs.append((label, other))
                taken.add(other)
                break
    return pairs
