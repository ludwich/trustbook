"""Structured generation with validation and exactly one retry (grounding layer 3)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from app.capabilities.adapters.base import ChatAdapter, ChatMessage
from app.chat.output import InvalidOutputError
from app.chat.prompt import RETRY_INSTRUCTION, TEMPERATURE

logger = logging.getLogger("trustbook.chat")

ChatAdapterFactory = Callable[[], ChatAdapter]
"""Builds the configured chat adapter (raises ConfigError when chat is not set up)."""

MAX_TOKENS = 4_096  # room for reasoning models that think before the JSON


def generate_structured[T](
    adapter: ChatAdapter,
    messages: Sequence[ChatMessage],
    schema: dict[str, Any],
    parse: Callable[[str], T],
    *,
    max_tokens: int = MAX_TOKENS,
) -> T:
    """Ask for JSON matching ``schema`` and parse it; retry once if the reply is unusable.

    Raises :class:`InvalidOutputError` when both attempts fail, and lets adapter errors through.
    """
    first = adapter.chat(
        messages, temperature=TEMPERATURE, max_tokens=max_tokens, json_schema=schema
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
    second = adapter.chat(retry, temperature=TEMPERATURE, max_tokens=max_tokens, json_schema=schema)
    return parse(second.text)
