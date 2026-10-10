"""Deterministic mock chat adapter (TRUSTBOOK_MOCK_LLM=1). Never touches the network."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from app.capabilities.adapters.base import ChatMessage, ChatResult, Usage

MOCK_MODEL = "mock"


class MockChatAdapter:
    """Replies "OK" to the health ping; otherwise a deterministic echo (JSON when asked)."""

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        prompt_tokens = sum(len(m["content"].split()) for m in messages)
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
