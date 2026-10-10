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


def message_out(row: Message) -> MessageOut:
    """API shape of a stored message (``sources_used`` comes from its citations)."""
    try:
        content = MessageContent.model_validate(json.loads(row.content or "{}"))
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
    )


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
        self, conversation_id: str, sentences: Sequence[SentenceOut], removed: Sequence[str] = ()
    ) -> MessageOut:
        """Store an answer with one ``citations`` row per (sentence, chunk)."""
        content: dict[str, Any] = {"sentences": [s.model_dump() for s in sentences]}
        return self._add(
            conversation_id, "assistant", content, badge=None, sentences=sentences, removed=removed
        )

    def add_refusal(self, conversation_id: str, refusal: Refusal) -> MessageOut:
        content: dict[str, Any] = {
            "text": refusal.message,
            "suggestion": refusal.suggestion,
            "sentences": [],
        }
        return self._add(conversation_id, "assistant", content, badge="refused")

    def _add(
        self,
        conversation_id: str,
        role: str,
        content: dict[str, Any],
        *,
        badge: Badge | None,
        sentences: Sequence[SentenceOut] = (),
        removed: Sequence[str] = (),
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
                for citation in sentence.citations:
                    session.add(
                        Citation(
                            message_id=row.id, sentence_index=index, chunk_id=citation.chunk_id
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
