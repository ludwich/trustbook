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


# Leading phrases that make a question a question; stripped repeatedly ("Can you tell me how do I
# …" → "how to …"). "How do I …" becomes "how to …", which reads well after "sources that cover".
_HOW_TO = re.compile(
    r"^how\s+(?:do|can|could|should|would|will|must|may)\s+(?:i|we|you|one|someone)\s+", re.I
)
_LEAD = re.compile(
    r"^(?:please|so|ok(?:ay)?|hey|hi|"
    r"can\s+you|could\s+you|would\s+you|will\s+you|"
    r"(?:can|could)\s+(?:i|we)\s+(?:ask|know)|"
    r"do\s+you\s+know|i\s+(?:want|would\s+like|need)\s+to\s+know|i\s+wonder|"
    r"tell\s+me(?:\s+about)?|show\s+me|explain|describe|"
    r"what(?:'s|\s+is|\s+are|\s+was|\s+were|\s+does|\s+do)|"
    r"is\s+there|are\s+there|is\s+it\s+possible\s+to|"
    r"does|do|did|is|are|was|were|can|could|should|will|would|has|have)"
    r"(?:\s+|[,:]\s*|$)",
    re.I,
)
TOPIC_CHARS = 80


def question_topic(question: str, *, limit: int = TOPIC_CHARS) -> str:
    """The question as a readable topic: leading question words and the "?" removed.

    The user's own words stay in order ("How do I set a base URL for Session calls?" →
    "how to set a base URL for Session calls"); long questions are cut on a word boundary.
    Returns "" when nothing is left.
    """
    text = " ".join(question.split())
    text = text.rstrip(" ?!.;:").strip()
    while True:
        how = _HOW_TO.match(text)
        if how:
            text = "how to " + text[how.end() :]
            break
        lead = _LEAD.match(text)
        if not lead or lead.end() == 0 or lead.end() >= len(text):
            break
        text = text[lead.end() :]
    text = text.strip(" ,;:-")
    if not text:
        return ""
    first, _, rest = text.partition(" ")
    # "How to …" / "Retry …" at the start of a question is capitalised only as sentence case.
    if first[:1].isupper() and first[1:].islower() and first.isalpha():
        text = first.lower() + (" " + rest if rest else "")
    return truncate(text, limit)
