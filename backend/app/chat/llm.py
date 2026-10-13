"""Structured generation with validation and exactly one retry (grounding layer 3)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.capabilities.adapters.base import ChatAdapter, ChatMessage, ChatResult
from app.chat.output import InvalidOutputError
from app.chat.prompt import RETRY_INSTRUCTION, TEMPERATURE, estimate_tokens, prompt_chars

logger = logging.getLogger("trustbook.chat")

ChatAdapterFactory = Callable[[], ChatAdapter]
"""Builds the configured chat adapter (raises ConfigError when chat is not set up)."""

MAX_TOKENS = 4_096  # room for reasoning models that think before the JSON


@dataclass(frozen=True)
class AnswerCall:
    """One request to the chat endpoint (D-080): how long it took, the estimated prompt tokens
    (characters / 4) and the completion tokens the endpoint reported (None: not reported)."""

    seconds: float
    prompt_tokens: int
    completion_tokens: int | None


def _chat(
    adapter: ChatAdapter,
    messages: Sequence[ChatMessage],
    calls: list[AnswerCall] | None,
    *,
    temperature: float,
    max_tokens: int,
    schema: dict[str, Any],
    seed: int | None,
    think: bool = True,
) -> ChatResult:
    started = time.perf_counter()
    result: ChatResult | None = None
    try:
        result = adapter.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=schema,
            seed=seed,
            think=think,
        )
        return result
    finally:
        if calls is not None:
            calls.append(
                AnswerCall(
                    seconds=time.perf_counter() - started,
                    prompt_tokens=estimate_tokens(prompt_chars(messages)),
                    completion_tokens=result.usage.completion_tokens if result else None,
                )
            )


def generate_structured[T](
    adapter: ChatAdapter,
    messages: Sequence[ChatMessage],
    schema: dict[str, Any],
    parse: Callable[[str], T],
    *,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
    seed: int | None = None,
    calls: list[AnswerCall] | None = None,
    think: bool = True,
) -> T:
    """Ask for JSON matching ``schema`` and parse it; retry once if the reply is unusable.

    ``temperature`` and ``seed`` default to the SPEC's 0.2 and none; the eval runner overrides
    them per request (F5). Raises :class:`InvalidOutputError` when both attempts fail, and lets
    adapter errors through. ``calls``: every request made (the retry included) is appended to it,
    also when its reply is unusable or the adapter fails (D-080). ``think`` False asks a reasoning
    model to answer without thinking first (ideation turns, D-103).
    """
    first = _chat(
        adapter,
        messages,
        calls,
        temperature=temperature,
        max_tokens=max_tokens,
        schema=schema,
        seed=seed,
        think=think,
    )
    try:
        return parse(first.text)
    except InvalidOutputError as exc:
        logger.info("Chat reply was not usable JSON (%s); retrying once", exc)
    retry: list[ChatMessage] = [
        *messages,
        {"role": "assistant", "content": first.text[:4_000] or "(empty reply)"},
        {"role": "user", "content": RETRY_INSTRUCTION},
    ]
    second = _chat(
        adapter,
        retry,
        calls,
        temperature=temperature,
        max_tokens=max_tokens,
        schema=schema,
        seed=seed,
        think=think,
    )
    return parse(second.text)
