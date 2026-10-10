"""The gate router (SPEC §5, §6): one fixed-choice call that classifies a chat question.

``sources`` (the default) runs the grounded pipeline. ``brainstorm`` runs the same grounded
pipeline and adds a hint that points to the Ideation tab. ``chitchat`` gets a fixed reply and no
answer call. The gate never opens an ungrounded path: it can only skip answering.

Every decision is written to ``gate_log`` in ``space.db``. A gate that is not set up, fails, or
takes longer than :data:`GATE_TIMEOUT_S` counts as ``sources`` with confidence None.
"""

from __future__ import annotations

import contextvars
import logging
import math
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from app.capabilities.adapters.base import ChatAdapter, ChatMessage
from app.chat.output import InvalidOutputError, extract_json
from app.chat.schemas import GATE_DECISIONS, GateDecision, GateLogOut
from app.db.models import GateLog
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.gate")

GATE_NAME = "chat"  # gate_log.gate: the router in front of chat answers
GATE_TIMEOUT_S = 5.0
GATE_MAX_TOKENS = 200
GATE_TEMPERATURE = 0.0
MAX_INPUT_CHARS = 4_000

CHITCHAT_REPLY = "I answer questions from the sources in this space. Ask about something in them."

GATE_SYSTEM = """\
You route messages in a research notebook that answers only from the user's own sources.
Classify the user's message:
- "sources": a question or request for information. Use this when unsure.
- "brainstorm": the user wants new ideas, suggestions or "what if" thinking.
- "chitchat": a greeting, thanks or small talk with nothing to look up.
Reply with only this JSON object:
{"decision": "sources" | "brainstorm" | "chitchat", "confidence": number from 0 to 1}"""

GATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(GATE_DECISIONS)},
        "confidence": {"type": "number"},
    },
    "required": ["decision", "confidence"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    confidence: float | None
    log_id: int | None = None


FALLBACK = GateResult("sources", None)


def gate_messages(question: str) -> list[ChatMessage]:
    """Only the question goes to the gate, never any source text."""
    text = " ".join(question.split())[:MAX_INPUT_CHARS]
    return [{"role": "system", "content": GATE_SYSTEM}, {"role": "user", "content": text}]


def parse_gate(text: str) -> GateResult:
    """``{"decision": …, "confidence": …}`` → result; raises InvalidOutputError otherwise."""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise InvalidOutputError("not a JSON object")
    decision = data.get("decision")
    if not isinstance(decision, str) or decision.strip().lower() not in GATE_DECISIONS:
        raise InvalidOutputError("unknown decision")
    choice: GateDecision = next(d for d in GATE_DECISIONS if d == decision.strip().lower())
    raw = data.get("confidence")
    confidence: float | None = None
    if isinstance(raw, int | float) and not isinstance(raw, bool) and math.isfinite(raw):
        confidence = min(1.0, max(0.0, float(raw)))
    return GateResult(choice, confidence)


def classify(
    question: str,
    adapter: Callable[[], ChatAdapter],
    *,
    timeout_s: float = GATE_TIMEOUT_S,
) -> GateResult:
    """One fixed-choice call, at most ``timeout_s`` seconds; any failure → ``sources``.

    The call runs in a helper thread with a copy of the caller's context, so the request log
    attributes it to the same space. A call that times out is abandoned (it ends on its own
    with the adapter's timeout) and its result is ignored.
    """
    results: queue.Queue[GateResult | BaseException] = queue.Queue(maxsize=1)
    context = contextvars.copy_context()

    def call() -> None:
        try:
            reply = adapter().chat(
                gate_messages(question),
                temperature=GATE_TEMPERATURE,
                max_tokens=GATE_MAX_TOKENS,
                json_schema=GATE_SCHEMA,
                think=False,  # a router; a reasoning model would spend its tokens thinking
            )
            results.put(parse_gate(reply.text))
        except BaseException as exc:  # reported to the caller below
            results.put(exc)

    threading.Thread(target=context.run, args=(call,), name="trustbook-gate", daemon=True).start()
    try:
        outcome = results.get(timeout=timeout_s)
    except queue.Empty:
        logger.info("The gate took longer than %.0f s; using sources", timeout_s)
        return FALLBACK
    if isinstance(outcome, BaseException):
        logger.info("The gate failed (%s); using sources", type(outcome).__name__)
        return FALLBACK
    return outcome


# --- gate_log --------------------------------------------------------------------------------


class GateLogNotFoundError(LookupError):
    """No gate log entry with this id in the space."""


def _out(row: GateLog) -> GateLogOut:
    assert row.id is not None
    return GateLogOut(
        id=row.id,
        created=row.created,
        input=row.input,
        decision=row.decision,
        confidence=row.confidence,
        user_correction=row.user_correction,
    )


class GateLogStore:
    """``gate_log`` of one space."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, question: str, result: GateResult, *, gate: str = GATE_NAME) -> GateLogOut:
        row = GateLog(
            gate=gate,
            input=question[:MAX_INPUT_CHARS],
            decision=result.decision,
            confidence=result.confidence,
            created=utc_now_iso(),
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return _out(row)

    def list(self, limit: int = 100) -> list[GateLogOut]:
        with Session(self._engine) as session:
            rows = session.exec(select(GateLog).order_by(col(GateLog.id).desc()).limit(limit))
            return [_out(row) for row in rows.all()]

    def correct(self, entry_id: int, decision: GateDecision | None) -> GateLogOut:
        """Store the user's correction; None clears it."""
        with Session(self._engine) as session:
            row = session.get(GateLog, entry_id)
            if row is None:
                raise GateLogNotFoundError(entry_id)
            row.user_correction = decision
            session.add(row)
            session.commit()
            session.refresh(row)
            return _out(row)


class Gate:
    """Classifies a question (when the gate is set up) and logs the decision."""

    def __init__(
        self,
        store: GateLogStore,
        *,
        adapter: Callable[[], ChatAdapter] | None,
        timeout_s: float = GATE_TIMEOUT_S,
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._timeout_s = timeout_s

    def route(self, question: str) -> GateResult:
        """Decide and log. ``adapter`` None (gate off or not set up) → ``sources``, logged."""
        result = (
            FALLBACK
            if self._adapter is None
            else classify(question, self._adapter, timeout_s=self._timeout_s)
        )
        try:
            entry = self._store.add(question, result)
        except Exception:
            logger.exception("Could not write the gate log")
            return result
        return GateResult(result.decision, result.confidence, entry.id)
