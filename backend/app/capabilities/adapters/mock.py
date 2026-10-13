"""Deterministic mock chat adapter (TRUSTBOOK_MOCK_LLM=1). Never touches the network.

For grounded prompts (a JSON schema with ``sentences``) it answers like a careful model would:
it reads the numbered chunks back out of the prompt, quotes the chunk sentence(s) that share the
most meaningful words with the question and cites them. When no chunk shares a meaningful word
with the question it returns ``{"sentences": []}``. Overview prompts (schema with ``questions``)
get summary sentences citing chunks plus exactly three questions. Gate prompts (schema with
``decision``) are classified by keywords: greetings and thanks → ``chitchat``; "brainstorm",
"ideas for", "what if" → ``brainstorm``; everything else → ``sources``.

Test hooks (docs/ARCHITECTURE.md, "Test hooks"):

- A question containing ``TB-UNSUPPORTED`` gets one extra sentence, :data:`UNSUPPORTED_SENTENCE`,
  citing ``c1``. It fails verification, so the answer is ``partially_verified``.
- A question containing ``TB-OUTLINE-FACT`` gets one extra sentence naming, in backticks, a name
  that only the prompt's code outline shows (D-077), citing ``c1``. The outline is not a chunk, so
  the sentence fails layer 5 and is removed. Without an outline nothing is added.
- A question containing ``TB-WINDOW-FACT`` gets one extra sentence quoted from a transcript
  window (D-091): the first sentence of a neighbour's line with a word no numbered chunk has,
  citing ``c1``. The window is not a chunk, so the sentence fails verification and is removed.
  Without a window nothing is added.

The mock never quotes the outline or a window otherwise: answers and judge verdicts read only the
numbered chunks (:func:`~app.chat.prompt.parse_prompt` returns both separately).
- Ideation (D-093): seed prompts (schema with ``branches``) get three branches from the idea's
  first content word ("Who it is for", "What your sources say about <word>", "Risks and open
  problems"). An idea containing ``TB-SEEDS4`` gets a fourth, "How it is paid for", before the
  risks (within the map's four-seed cap), so a session stays mid-session for longer; the trigger
  is left out of the title and branch names. Turn prompts (schema with ``question``) get a fixed
  question about the branch and a recommendation quoting the chunk sentence that shares most
  words with the idea and branch, citing it; without such a chunk, one uncited suggestion.
  ``TB-UNSUPPORTED`` in the idea adds :data:`UNSUPPORTED_SENTENCE` citing ``c1`` (it fails
  verification and is removed). A last answer containing ``TB-BRANCH`` adds one new branch under
  the answered one; one containing "risk" adds a risk about the answered branch.
- Judge prompts (schema with ``verdicts``): a statement is supported when its cited lines share
  at least two content words with it; each cited chunk's whole line range is the evidence.
- :class:`MockSearchAdapter`: the ``mock`` web-search provider, fixed results for any query.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.capabilities.adapters.base import ChatMessage, ChatResult, SearchResult, Usage
from app.capabilities.adapters.search import demote_homepages
from app.chat.prompt import PromptChunk, parse_prompt
from app.chat.text import content_terms, truncate
from app.grounding.code_judge import parse_judge_prompt
from app.ideation.prompt import parse_ideation_prompt
from app.retrieval.parents import WINDOW_PLACEHOLDER

MOCK_MODEL = "mock"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MAX_QUOTE_CHARS = 300
UNSUPPORTED_TRIGGER = "TB-UNSUPPORTED"
UNSUPPORTED_SENTENCE = "This also requires a licence key from the vendor."
OUTLINE_FACT_TRIGGER = "TB-OUTLINE-FACT"
OUTLINE_FACT_SENTENCE = "The file also defines `{name}`."
_OUTLINE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
WINDOW_FACT_TRIGGER = "TB-WINDOW-FACT"
_WINDOW_LINE = re.compile(r"^\s+\[[^\]]*\]\s+(.*)$")
_SPEAKER = re.compile(r"^[^\s:][^:]{0,38}:\s+")
JUDGE_MIN_SHARED = 2
BRANCH_TRIGGER = "TB-BRANCH"
SEEDS4_TRIGGER = "TB-SEEDS4"
SEEDS4_BRANCH = "How it is paid for"
IDEA_SUGGESTION = "Start small and try it with one real user before building more."


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in _SENTENCE_END.split(flat) if s.strip()]


def _overlap(terms: set[str], text: str) -> int:
    return len(terms & set(content_terms(text)))


def _body(text: str) -> str:
    """A chunk's text without a code chunk's ``File: … (lines a-b)`` header line.

    A model quotes the code, not the header; quoting it put line numbers into the answer, which
    the judge's anchor check (correctly) finds in no code line.
    """
    first, sep, rest = text.partition("\n")
    return rest if sep and first.startswith("File: ") else text


def _best_sentence(chunk: PromptChunk, terms: set[str]) -> str:
    """The chunk sentence sharing most terms with the question (first one on ties)."""
    body = _body(chunk.text)
    candidates = _sentences(body) or [body]
    best = max(candidates, key=lambda s: (_overlap(terms, s), -candidates.index(s)))
    return truncate(best, MAX_QUOTE_CHARS)


def outline_only_name(chunks: Sequence[PromptChunk], outline: str) -> str | None:
    """The first name of the outline's ``module:`` or class lines that no chunk contains."""
    texts = [c.text for c in chunks]
    for line in outline.splitlines():
        _, sep, names = line.partition(": ")
        if not sep or line.strip().startswith("imports:"):
            continue
        for name in _OUTLINE_NAME.findall(names):
            if name in ("class", "and", "more"):
                continue
            if not any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", t) for t in texts):
                return str(name)
    return None


def window_only_sentence(chunks: Sequence[PromptChunk], transcript: str) -> str | None:
    """The first sentence of a transcript window's neighbour lines that has a content word no
    chunk has (the speaker label left out); None when there is none."""
    held = {term for c in chunks for term in content_terms(c.text)}
    for line in transcript.splitlines():
        match = _WINDOW_LINE.match(line)
        if match is None or match.group(1).startswith(WINDOW_PLACEHOLDER):
            continue
        body = _SPEAKER.sub("", match.group(1), count=1)
        for sentence in _sentences(body):
            if set(content_terms(sentence)) - held:
                return truncate(sentence, MAX_QUOTE_CHARS)
    return None


def grounded_answer(
    chunks: Sequence[PromptChunk], question: str, outline: str = "", transcript: str = ""
) -> dict[str, Any]:
    """1–2 sentences quoting the best-matching chunk(s), or no sentences when nothing matches.

    ``outline`` and ``transcript``: the prompt's code outline and transcript windows; never
    quoted (only the ``TB-OUTLINE-FACT`` hook names one of the outline's names, and only the
    ``TB-WINDOW-FACT`` hook quotes a window).
    """
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
    if UNSUPPORTED_TRIGGER in question and chunks:
        sentences.append({"text": UNSUPPORTED_SENTENCE, "chunk_ids": [chunks[0].label]})
    if OUTLINE_FACT_TRIGGER in question and chunks:
        name = outline_only_name(chunks, outline)
        if name is not None:
            text = OUTLINE_FACT_SENTENCE.format(name=name)
            sentences.append({"text": text, "chunk_ids": [chunks[0].label]})
    if WINDOW_FACT_TRIGGER in question and chunks:
        quoted = window_only_sentence(chunks, transcript)
        if quoted is not None:
            sentences.append({"text": quoted, "chunk_ids": [chunks[0].label]})
    return {"sentences": sentences}


def judge_verdicts(prompt: str) -> dict[str, Any]:
    """Mock judge: supported when the cited lines share 2+ content words with the statement.

    The evidence is each cited chunk's whole line range. A real judge names the exact lines; the
    mock exists for UI tests, and picking only word-sharing lines missed lines that hold a
    statement's anchors (``DEFAULT_POOL_SIZE = 10``), so the confirmation step vetoed true answers.
    The anchoring rules themselves are unchanged and tested with explicit judge replies.
    """
    chunks, statements = parse_judge_prompt(prompt)
    verdicts: list[dict[str, Any]] = []
    for index, statement in enumerate(statements, start=1):
        terms = set(content_terms(statement.text))
        shared: set[str] = set()
        evidence: list[dict[str, Any]] = []
        for label in statement.labels:
            lines = chunks.get(label, [])
            for _number, line in lines:
                shared |= terms & set(content_terms(line))
            if lines:
                first, last = lines[0][0], lines[-1][0]
                evidence.append({"chunk": label, "start_line": first, "end_line": last})
        supported = len(shared) >= JUDGE_MIN_SHARED
        verdicts.append(
            {"index": index, "supported": supported, "evidence": evidence if supported else []}
        )
    return {"verdicts": verdicts}


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


def ideation_seed(prompt: str) -> dict[str, Any]:
    """Three branches named after the idea's first content word (four with ``TB-SEEDS4``), and a
    title from the idea's start."""
    parts = parse_ideation_prompt(prompt)
    idea = parts.idea.replace(SEEDS4_TRIGGER, " ")
    terms = content_terms(idea)
    about = f"What your sources say about {terms[0]}" if terms else "What your sources say"
    title = truncate(idea, 40) or "Untitled idea"
    extra = [SEEDS4_BRANCH] if SEEDS4_TRIGGER in parts.idea else []
    return {"title": title, "branches": ["Who it is for", about, *extra, "Risks and open problems"]}


def ideation_turn(prompt: str) -> dict[str, Any]:
    """A fixed question about the branch, a recommendation quoting the best chunk (or one
    suggestion), and the ``TB-BRANCH`` / "risk" hooks on the last answer."""
    parts = parse_ideation_prompt(prompt)
    question = ""
    sentences: list[dict[str, Any]] = []
    if parts.target_title:
        question = f"What should be decided about “{parts.target_title}”?"
        terms = set(content_terms(f"{parts.idea} {parts.target_title}"))
        scored = [(_overlap(terms, c.text), i, c) for i, c in enumerate(parts.chunks)]
        matching = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
        if matching:
            best = matching[0][2]
            sentences.append({"text": _best_sentence(best, terms), "chunk_ids": [best.label]})
        else:
            sentences.append({"text": IDEA_SUGGESTION, "chunk_ids": []})
        if UNSUPPORTED_TRIGGER in parts.idea and parts.chunks:
            sentences.append({"text": UNSUPPORTED_SENTENCE, "chunk_ids": [parts.chunks[0].label]})
    answer = parts.last_answer or ""
    branches: list[dict[str, str]] = []
    if BRANCH_TRIGGER in answer and parts.last_label:
        topic = truncate(answer.replace(BRANCH_TRIGGER, " "), 40) or "the answer"
        branches.append({"title": f"Follow-up on {topic}", "parent": parts.last_label})
    risks: list[dict[str, str]] = []
    if "risk" in answer.lower():
        text = f"Raised in an answer: {truncate(answer, 80)}"
        risks.append({"text": text, "branch": parts.last_label or ""})
    return {
        "question": question,
        "recommendation": {"sentences": sentences},
        "new_branches": branches,
        "risks": risks,
    }


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
        seed: int | None = None,
    ) -> ChatResult:
        self.calls.append(list(messages))
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        prompt_tokens = sum(len(m["content"].split()) for m in messages)
        properties = (json_schema or {}).get("properties")
        if isinstance(properties, dict) and "decision" in properties:
            text = json.dumps(gate_decision(last_user))
            return ChatResult(text=text, usage=Usage(prompt_tokens, len(text.split())))
        if isinstance(properties, dict) and "verdicts" in properties:
            text = json.dumps(judge_verdicts(last_user))
            return ChatResult(text=text, usage=Usage(prompt_tokens, len(text.split())))
        if isinstance(properties, dict) and ("branches" in properties or "question" in properties):
            prompt = next((m["content"] for m in messages if m["role"] == "user"), "")
            data = ideation_seed(prompt) if "branches" in properties else ideation_turn(prompt)
            text = json.dumps(data, ensure_ascii=False)
            return ChatResult(text=text, usage=Usage(prompt_tokens, len(text.split())))
        if isinstance(properties, dict) and "sentences" in properties:
            # The prompt with the chunks is the first user message (a retry appends more).
            prompt = next((m["content"] for m in messages if m["role"] == "user"), "")
            parts = parse_prompt(prompt)
            if "questions" in properties:
                data = grounded_overview(parts.chunks)
            else:
                data = grounded_answer(
                    parts.chunks, parts.question or "", parts.outline, parts.transcript
                )
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


# The ``mock`` web-search provider: bare homepages first (the order a search engine might give),
# then specific pages, so the homepage demotion for specific queries is visible.
MOCK_SEARCH_RESULTS: tuple[SearchResult, ...] = (
    SearchResult("Example", "https://www.example.com/sv-se/", "Example home page."),
    SearchResult("Example Organisation", "https://example.org/", "Welcome to Example."),
    SearchResult(
        "AB-731 certificate",
        "https://docs.example.com/certificates/ab-731",
        "What the AB-731 certificate covers and how to earn it.",
    ),
    SearchResult(
        "Exam AB-731",
        "https://learn.example.com/exams/ab-731",
        "Skills measured in exam AB-731, with study material.",
    ),
)


class MockSearchAdapter:
    """Fixed results for any query (only with ``TRUSTBOOK_MOCK_LLM=1``); never touches the network.

    Ranked like the real adapters: for specific queries, bare homepages go last.
    """

    def ping(self) -> None:
        return None

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return demote_homepages(query, list(MOCK_SEARCH_RESULTS))[: max(0, limit)]
