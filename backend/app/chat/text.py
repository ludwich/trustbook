"""Small text helpers: content words of a question, truncation."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-']*")

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each either else
    few for from further had has have having he her here hers him his how i if in into is it its
    itself just me might more most must my no nor not now of off on once only or other our ours
    out over own same shall she should so some such than that the their theirs them then there
    these they this those through to too under until up upon us very was we were what when where
    which while who whom whose why will with would you your yours tell explain describe give
    show list please know say said does using use used get gets got make makes made way ways
    thing things something anything there's what's how's it's i'm can't don't doesn't
    """.split()
)


def words(text: str) -> list[str]:
    """Lower-case words of ``text`` in order."""
    return [w.lower().strip("'-") for w in _WORD.findall(text)]


def content_terms(text: str) -> list[str]:
    """Meaningful words (no stopwords, at least 3 characters), unique, in order of appearance."""
    seen: dict[str, None] = {}
    for word in words(text):
        if len(word) >= 3 and word not in STOPWORDS:
            seen.setdefault(word, None)
    return list(seen)


def truncate(text: str, limit: int) -> str:
    """Collapse whitespace and cut at a word boundary, adding "…" when shortened."""
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    cut = clean[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def topic_of(question: str, *, max_words: int = 6) -> str:
    """A short topic phrase for a question (its first content words), for refusal suggestions."""
    terms = content_terms(question)[:max_words]
    if terms:
        return " ".join(terms)
    return truncate(question.strip().rstrip("?"), 60)
