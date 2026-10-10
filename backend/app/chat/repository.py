"""Conversations, messages and citations in ``space.db``."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, literal_column
from sqlmodel import Session, col, select

from app.chat.citations import sources_used
from app.chat.schemas import (
    TITLE_CHARS,
    Badge,
    ConversationOut,
    Diagnostics,
    Hint,
    MessageContent,
    MessageOut,
    Refusal,
    SentenceOut,
)
from app.chat.text import truncate
from app.db.models import Citation, Conversation, Message
from app.timeutil import utc_now_iso


class ConversationNotFoundError(LookupError):
    """No conversation with this id in the space."""


def conversation_title(question: str) -> str:
    """A conversation's title: its first question, cut to 60 characters."""
    return truncate(question, TITLE_CHARS)


def _conversation_out(row: Conversation) -> ConversationOut:
    return ConversationOut(
        id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
    )


def _diagnostics(raw: Any) -> Diagnostics | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Diagnostics.model_validate(raw)
    except ValidationError:
        return None


def message_out(row: Message) -> MessageOut:
    """API shape of a stored message (``sources_used`` comes from its citations).

    ``diagnostics`` and ``gate_log_id`` are stored inside the content JSON and returned beside it.
    """
    diagnostics: Diagnostics | None = None
    gate_log_id: int | None = None
    try:
        data: Any = json.loads(row.content or "{}")
        content = MessageContent.model_validate(data)
        if isinstance(data, dict):
            diagnostics = _diagnostics(data.get("diagnostics"))
            raw_id = data.get("gate_log_id")
            if isinstance(raw_id, int) and not isinstance(raw_id, bool):
                gate_log_id = raw_id
    except (ValueError, ValidationError):
        content = MessageContent(text="")
    try:
        removed_raw: Any = json.loads(row.removed_sentences or "[]")
    except ValueError:
        removed_raw = []
    removed = [str(s) for s in removed_raw] if isinstance(removed_raw, list) else []
    badge: Badge | None = row.badge  # type: ignore[assignment]  # CHECK constraint in SQL
    return MessageOut(
        id=row.id,
        role="assistant" if row.role == "assistant" else "user",
        created_at=row.created_at,
        content=content,
        badge=badge,
        removed_sentences=removed,
        sources_used=sources_used(content.sentences or []),
        diagnostics=diagnostics if row.role == "assistant" else None,
        gate_log_id=gate_log_id if row.role == "assistant" else None,
    )


def _with_extras(
    content: dict[str, Any],
    diagnostics: Diagnostics | None,
    hint: Hint | None,
    gate_log_id: int | None,
) -> dict[str, Any]:
    if hint is not None:
        content["hint"] = hint
    if diagnostics is not None:
        content["diagnostics"] = diagnostics.model_dump()
    if gate_log_id is not None:
        content["gate_log_id"] = gate_log_id
    return content


class ChatStore:
    """Thread-safe enough for SQLite: every call opens its own session."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- conversations ---------------------------------------------------------------------

    def list_conversations(self) -> list[ConversationOut]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(Conversation).order_by(
                    col(Conversation.updated_at).desc(), col(Conversation.created_at).desc()
                )
            ).all()
            return [_conversation_out(row) for row in rows]

    def create_conversation(self, title: str | None = None) -> ConversationOut:
        now = utc_now_iso()
        row = Conversation(
            id=uuid.uuid4().hex,
            title=conversation_title(title) if title else "",
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return _conversation_out(row)

    def get_conversation(self, conversation_id: str) -> ConversationOut:
        with Session(self._engine) as session:
            row = session.get(Conversation, conversation_id)
            if row is None:
                raise ConversationNotFoundError(conversation_id)
            return _conversation_out(row)

    def delete_conversation(self, conversation_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(Conversation, conversation_id)
            if row is None:
                raise ConversationNotFoundError(conversation_id)
            session.delete(row)  # messages and citations cascade (foreign keys are on)
            session.commit()

    # --- messages --------------------------------------------------------------------------

    def list_messages(self, conversation_id: str) -> list[MessageOut]:
        self.get_conversation(conversation_id)
        with Session(self._engine) as session:
            rows = session.exec(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(literal_column("messages.rowid"))  # insertion order
            ).all()
            return [message_out(row) for row in rows]

    def add_user_message(self, conversation_id: str, text: str) -> MessageOut:
        """Store a question; the first one also names an untitled conversation."""
        content = MessageContent(text=text).model_dump(exclude_none=True)
        return self._add(conversation_id, "user", content, badge=None, title_from=text)

    def add_answer(
        self,
        conversation_id: str,
        sentences: Sequence[SentenceOut],
        removed: Sequence[str] = (),
        *,
        badge: Badge | None = None,
        scores: Sequence[dict[str, float | None]] = (),
        diagnostics: Diagnostics | None = None,
        hint: Hint | None = None,
        gate_log_id: int | None = None,
    ) -> MessageOut:
        """Store an answer with one ``citations`` row per (sentence, chunk).

        ``scores[i]`` maps the chunk ids of sentence ``i`` to their grounding checker score,
        stored as ``citations.verifier_score``.
        """
        content: dict[str, Any] = {"sentences": [s.model_dump() for s in sentences]}
        return self._add(
            conversation_id,
            "assistant",
            _with_extras(content, diagnostics, hint, gate_log_id),
            badge=badge,
            sentences=sentences,
            removed=removed,
            scores=scores,
        )

    def add_refusal(
        self,
        conversation_id: str,
        refusal: Refusal,
        *,
        diagnostics: Diagnostics | None = None,
        hint: Hint | None = None,
        gate_log_id: int | None = None,
    ) -> MessageOut:
        content: dict[str, Any] = {
            "text": refusal.message,
            "suggestion": refusal.suggestion,
            "sentences": [],
        }
        return self._add(
            conversation_id,
            "assistant",
            _with_extras(content, diagnostics, hint, gate_log_id),
            badge="refused",
        )

    def add_reply(
        self,
        conversation_id: str,
        text: str,
        *,
        diagnostics: Diagnostics | None = None,
        gate_log_id: int | None = None,
    ) -> MessageOut:
        """A fixed assistant reply that is not an answer (the gate's chitchat reply): no badge."""
        content: dict[str, Any] = {"text": text}
        return self._add(
            conversation_id,
            "assistant",
            _with_extras(content, diagnostics, None, gate_log_id),
            badge=None,
        )

    def _add(
        self,
        conversation_id: str,
        role: str,
        content: dict[str, Any],
        *,
        badge: Badge | None,
        sentences: Sequence[SentenceOut] = (),
        removed: Sequence[str] = (),
        scores: Sequence[dict[str, float | None]] = (),
        title_from: str | None = None,
    ) -> MessageOut:
        now = utc_now_iso()
        row = Message(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            role=role,
            content=json.dumps(content, ensure_ascii=False),
            badge=badge,
            removed_sentences=json.dumps(list(removed), ensure_ascii=False),
            created_at=now,
        )
        with Session(self._engine) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(conversation_id)
            conversation.updated_at = now
            if title_from is not None and not conversation.title:
                conversation.title = conversation_title(title_from)
            session.add(conversation)
            session.add(row)
            session.flush()
            for index, sentence in enumerate(sentences):
                sentence_scores = scores[index] if index < len(scores) else {}
                for citation in sentence.citations:
                    session.add(
                        Citation(
                            message_id=row.id,
                            sentence_index=index,
                            chunk_id=citation.chunk_id,
                            verifier_score=sentence_scores.get(citation.chunk_id),
                        )
                    )
            session.commit()
            session.refresh(row)
            return message_out(row)

    def citation_rows(self, message_id: str) -> list[Citation]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(Citation)
                    .where(Citation.message_id == message_id)
                    .order_by(col(Citation.sentence_index), col(Citation.id))
                ).all()
            )
