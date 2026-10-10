"""One question → events for the answer stream, with the answer persisted."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.chat.llm import ChatAdapterFactory
from app.chat.pipeline import Refused, answer
from app.chat.repository import ChatStore
from app.chat.schemas import MessageOut, Stage
from app.retrieval.service import Retrieval

EventSink = Callable[[str, dict[str, Any]], None]
"""Receives ``(event name, data)``: status, sentence, refused, done (errors are the caller's)."""


class ChatService:
    """Runs the grounded pipeline for one space and stores the outcome."""

    def __init__(
        self,
        store: ChatStore,
        *,
        retrieve: Callable[[str], Retrieval],
        chat_adapter: ChatAdapterFactory,
        refuse_threshold: float,
    ) -> None:
        self._store = store
        self._retrieve = retrieve
        self._chat_adapter = chat_adapter
        self._refuse_threshold = refuse_threshold

    def respond(self, conversation_id: str, question: str, emit: EventSink) -> MessageOut:
        """Answer ``question`` (already stored as the user message) and emit the events.

        Every successful run ends with ``done`` carrying the stored assistant message, also
        after ``refused``. Exceptions (endpoint errors and the like) propagate to the caller.
        """

        def on_stage(stage: Stage) -> None:
            emit("status", {"stage": stage})

        outcome = answer(
            question,
            retrieve=self._retrieve,
            chat_adapter=self._chat_adapter,
            refuse_threshold=self._refuse_threshold,
            on_stage=on_stage,
        )
        if isinstance(outcome, Refused):
            message = self._store.add_refusal(conversation_id, outcome.refusal)
            emit("refused", outcome.refusal.model_dump())
        else:
            for index, sentence in enumerate(outcome.sentences):
                emit("sentence", {"index": index, **sentence.model_dump()})
            # Phase 1: dropped (uncited) sentences are not shown anywhere (layer 3).
            message = self._store.add_answer(conversation_id, outcome.sentences)
        emit("done", {"message": message.model_dump()})
        return message
