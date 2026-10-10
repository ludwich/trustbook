"""Deterministic mock chat adapter (TRUSTBOOK_MOCK_LLM=1). Never touches the network.

For grounded prompts (a JSON schema with ``sentences``) it answers like a careful model would:
it reads the numbered chunks back out of the prompt, quotes the chunk sentence(s) that share the
most meaningful words with the question and cites them. When no chunk shares a meaningful word
with the question it returns ``{"sentences": []}``. Overview prompts (schema with ``questions``)
get summary sentences citing chunks plus exactly three questions. Gate prompts (schema with
``decision``) are classified by keywords: greetings and thanks → ``chitchat``; "brainstorm",
"ideas for", "what if" → ``brainstorm``; everything else → ``sources``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.capabilities.adapters.base import ChatMessage, ChatResult, Usage
from app.chat.prompt import PromptChunk, parse_prompt
from app.chat.text import content_terms, truncate

MOCK_MODEL = "mock"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MAX_QUOTE_CHARS = 300


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in _SENTENCE_END.split(flat) if s.strip()]


def _overlap(terms: set[str], text: str) -> int:
    return len(terms & set(content_terms(text)))


def _best_sentence(chunk: PromptChunk, terms: set[str]) -> str:
    """The chunk sentence sharing most terms with the question (first one on ties)."""
    candidates = _sentences(chunk.text) or [chunk.text]
    best = max(candidates, key=lambda s: (_overlap(terms, s), -candidates.index(s)))
    return truncate(best, MAX_QUOTE_CHARS)


def grounded_answer(chunks: Sequence[PromptChunk], question: str) -> dict[str, Any]:
    """1–2 sentences quoting the best-matching chunk(s), or no sentences when nothing matches."""
    terms = set(content_terms(question))
    scored = [(_overlap(terms, c.text), i, c) for i, c in enumerate(chunks)]
    matching = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
    if not matching:
        return {"sentences": []}
    best_score, _, best = matching[0]
    sentences = [{"text": _best_sentence(best, terms), "chunk_ids": [best.label]}]
    if len(matching) > 1 and matching[1][0] * 2 >= best_score:
        second = matching[1][2]
        text = _best_sentence(second, terms)
        if text != sentences[0]["text"]:
            sentences.append({"text": text, "chunk_ids": [second.label]})
    return {"sentences": sentences}


_GREETING_WORDS = frozenset(
    "hi hello hey hiya thanks thank you thx cheers ok okay good morning afternoon evening bye "
    "goodbye great nice there".split()
)
_BRAINSTORM_PHRASES = ("brainstorm", "ideas for", "what if")


def gate_decision(message: str) -> dict[str, Any]:
    """Deterministic gate classification of ``message``."""
    lowered = " ".join(message.lower().split())
    words = re.findall(r"[a-z']+", lowered)
    if words and all(w in _GREETING_WORDS for w in words):
        return {"decision": "chitchat", "confidence": 0.9}
    if any(phrase in lowered for phrase in _BRAINSTORM_PHRASES):
        return {"decision": "brainstorm", "confidence": 0.8}
    return {"decision": "sources", "confidence": 0.7}


def grounded_overview(chunks: Sequence[PromptChunk]) -> dict[str, Any]:
    """Up to three summary sentences (one per source first) and exactly three questions."""
    picked: list[PromptChunk] = []
    seen_sources: set[str] = set()
    for chunk in chunks:
        if chunk.source not in seen_sources:
            picked.append(chunk)
            seen_sources.add(chunk.source)
    picked = (picked + [c for c in chunks if c not in picked])[:3]
    sentences = [
        {
            "text": truncate((_sentences(c.text) or [c.text])[0], MAX_QUOTE_CHARS),
            "chunk_ids": [c.label],
        }
        for c in picked
        if c.text.strip()
    ]
    counts = Counter(t for c in chunks for t in content_terms(c.text))
    top = [term for term, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:3]
    questions = [f"What do the sources say about {term}?" for term in top]
    fillers = (
        "What are the main topics of these sources?",
        "Which key facts do the sources state?",
        "What conclusions do the sources reach?",
    )
    for filler in fillers:
        if len(questions) >= 3:
            break
        questions.append(filler)
    return {"sentences": sentences, "questions": questions}


class MockChatAdapter:
    """Replies "OK" to the health ping; grounded JSON for chat prompts; otherwise an echo."""

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
        think: bool = True,
    ) -> ChatResult:
        self.calls.append(list(messages))
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        prompt_tokens = sum(len(m["content"].split()) for m in messages)
        properties = (json_schema or {}).get("properties")
        if isinstance(properties, dict) and "decision" in properties:
            text = json.dumps(gate_decision(last_user))
            return ChatResult(text=text, usage=Usage(prompt_tokens, len(text.split())))
        if isinstance(properties, dict) and "sentences" in properties:
            # The prompt with the chunks is the first user message (a retry appends more).
            prompt = next((m["content"] for m in messages if m["role"] == "user"), "")
            chunks, question = parse_prompt(prompt)
            if "questions" in properties:
                data = grounded_overview(chunks)
            else:
                data = grounded_answer(chunks, question or "")
            text = json.dumps(data, ensure_ascii=False)
            return ChatResult(text=text, usage=Usage(prompt_tokens, len(text.split())))
        if last_user.strip().lower().startswith("reply with ok"):
            text = "OK"
        else:
            digest = hashlib.sha256(last_user.encode("utf-8")).hexdigest()[:8]
            echo = " ".join(last_user.split())[:80]
            text = f"Mock answer {digest}: {echo}"
        if json_schema is not None:
            text = json.dumps({"mock": True, "text": text}, sort_keys=True)
        return ChatResult(text=text, usage=Usage(prompt_tokens, len(text.split())))

    def list_models(self) -> list[str]:
        return [MOCK_MODEL]
