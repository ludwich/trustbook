"""The first line of every idea brief, and the test ingestion uses to keep briefs out (D-099).

A leaf module: ingestion imports it, and it imports nothing, so the ideation package never pulls
ingestion in (and ingestion never pulls in the engine).
"""

from __future__ import annotations

BRIEF_MARKER = "<!-- trustbook:idea-brief -->"
# A brief exported, then edited, may gain a byte-order mark or blank lines before the marker.
_HEAD_CHARS = 512

IDEA_BRIEF_REASON = (
    "This file is a Trustbook idea brief. Idea briefs are never used as sources, so answers "
    "only come from your own material."
)
IDEA_BRIEF_NEXT_STEP = "Add the documents the idea is based on instead."


def is_idea_brief(text: str) -> bool:
    """Whether ``text`` starts with the brief marker (a BOM and leading whitespace ignored)."""
    return text[:_HEAD_CHARS].lstrip("﻿ \t\r\n").startswith(BRIEF_MARKER)


def is_idea_brief_bytes(data: bytes) -> bool:
    """:func:`is_idea_brief` for raw file bytes (UTF-8, with or without a BOM)."""
    return is_idea_brief(data[:_HEAD_CHARS].decode("utf-8", "replace"))
