"""One question → events for the answer stream, with the answer persisted."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from app.chat.gate import CHITCHAT_REPLY, Gate, GateResult
from app.chat.llm import ChatAdapterFactory
from app.chat.pipeline import Refused, answer
from app.chat.repository import ChatStore
from app.chat.schemas import Diagnostics, Generation, Hint, MessageOut, Stage, Timings
from app.grounding.verify import Verifier
from app.retrieval.service import Retrieval

EventSink = Callable[[str, dict[str, Any]], None]
"""Receives ``(event name, data)``: status, sentence, refused, done (errors are the caller's)."""


class ChatService:
    """Runs the gate and the grounded pipeline for one space and stores the outcome."""

    def __init__(
        self,
        store: ChatStore,
        *,
        retrieve: Callable[[str], Retrieval],
        chat_adapter: ChatAdapterFactory,
        refuse_threshold: float,
        verifier: Callable[[], Verifier],
        gate: Gate | None = None,
        generation: Generation | None = None,
    ) -> None:
        self._store = store
        self._retrieve = retrieve
        self._chat_adapter = chat_adapter
        self._refuse_threshold = refuse_threshold
        self._verifier = verifier
        self._gate = gate
        self._generation = generation

    def respond(self, conversation_id: str, question: str, emit: EventSink) -> MessageOut:
        """Answer ``question`` (already stored as the user message) and emit the events.

        Every successful run ends with ``done`` carrying the stored assistant message, also
        after ``refused``. Sentences are emitted only after they passed verification.
        Exceptions (endpoint errors and the like) propagate to the caller.
        """
        last: list[Stage] = []

        def on_stage(stage: Stage) -> None:
            if not last or last[-1] != stage:
                last.append(stage)
                emit("status", {"stage": stage})

        on_stage("retrieving")
        started = time.perf_counter()
        routed = (
            self._gate.route(question) if self._gate is not None else GateResult("sources", None)
        )
        gate_s = round(time.perf_counter() - started, 3) if self._gate is not None else None
        if routed.decision == "chitchat":
            message = self._store.add_reply(
                conversation_id,
                CHITCHAT_REPLY,
                diagnostics=Diagnostics(),
                gate_log_id=routed.log_id,
            )
            emit("done", {"message": message.model_dump()})
            return message
        hint: Hint | None = "ideation" if routed.decision == "brainstorm" else None

        outcome = answer(
            question,
            retrieve=self._retrieve,
            chat_adapter=self._chat_adapter,
            refuse_threshold=self._refuse_threshold,
            verifier=self._verifier(),
            on_stage=on_stage,
            generation=self._generation,
        )
        timings = outcome.diagnostics.timings or Timings()
        timings = timings.model_copy(update={"gate_s": gate_s})
        outcome = replace(
            outcome, diagnostics=outcome.diagnostics.model_copy(update={"timings": timings})
        )
        if isinstance(outcome, Refused):
            message = self._store.add_refusal(
                conversation_id,
                outcome.refusal,
                diagnostics=outcome.diagnostics,
                hint=hint,
                gate_log_id=routed.log_id,
            )
            emit("refused", outcome.refusal.model_dump())
        else:
            for index, sentence in enumerate(outcome.sentences):
                emit("sentence", {"index": index, **sentence.model_dump()})
            # Layer 3 drops (uncited sentences) are not shown anywhere; layer 4/5 removals are
            # kept as removed_sentences for the "Unverified, not from your sources" drawer.
            message = self._store.add_answer(
                conversation_id,
                outcome.sentences,
                outcome.removed,
                badge=outcome.badge,
                scores=outcome.scores,
                diagnostics=outcome.diagnostics,
                hint=hint,
                gate_log_id=routed.log_id,
            )
        emit("done", {"message": message.model_dump()})
        return message
