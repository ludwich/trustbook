"""Transcript chunker: consecutive timed turns up to ~500 tokens (SPEC §7.2).

Turns (caption lines or speech-recognition windows) are never split. Past the target, the chunk
closes at the first turn that ends a sentence or changes speaker; text without punctuation (auto
captions) closes at the soft limit. Locator = start/end time (+ speaker when the chunk has one).
"""

from __future__ import annotations

import re

from app.chunking.document import TARGET_TOKENS, ChunkDraft
from app.chunking.tokens import TokenCounter
from app.parse.snapshot import Segment

SOFT_MAX_TOKENS = 600
MAX_TOKENS = 800
_ENDS_SENTENCE = re.compile(r"[.!?…][\"'”’)\]]*\s*$")


def _locator(group: list[Segment]) -> dict[str, object]:
    speakers = {s.speaker for s in group}
    return {
        "start_s": group[0].start_s if group[0].start_s is not None else 0.0,
        "end_s": group[-1].end_s if group[-1].end_s is not None else group[-1].start_s,
        "speaker": speakers.pop() if len(speakers) == 1 else None,
    }


def chunk_transcript(
    text: str,
    segments: list[Segment],
    counter: TokenCounter,
    *,
    target: int = TARGET_TOKENS,
    soft_max: int = SOFT_MAX_TOKENS,
    max_tokens: int = MAX_TOKENS,
) -> list[ChunkDraft]:
    """Group timed ``segments`` of ``text`` into chunks."""
    groups: list[list[Segment]] = []
    current: list[Segment] = []
    tokens = 0
    for segment in segments:
        seg_tokens = counter.count(text[segment.start : segment.end])
        if current:
            last = current[-1]
            speaker_change = segment.speaker != last.speaker
            sentence_done = bool(_ENDS_SENTENCE.search(text[last.start : last.end]))
            if (
                tokens + seg_tokens > max_tokens
                or (tokens >= target and (sentence_done or speaker_change))
                or tokens >= soft_max
            ):
                groups.append(current)
                current, tokens = [], 0
        current.append(segment)
        tokens += seg_tokens
    if current:
        groups.append(current)

    drafts: list[ChunkDraft] = []
    for group in groups:
        start, end = group[0].start, group[-1].end
        body = text[start:end]
        drafts.append(ChunkDraft(body, start, end, _locator(group), counter.count(body)))
    return drafts
