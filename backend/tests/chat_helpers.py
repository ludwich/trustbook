"""Helpers for retrieval/chat tests: sources and chunks written straight into space.db plus an
in-memory index, and deterministic embedder, reranker and chat fakes. No ingestion code is used."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session

from app.capabilities.adapters.base import ChatMessage, ChatResult
from app.chat.text import content_terms
from app.db.models import Chunk, Source
from app.index.base import IndexedChunk, SpaceIndex
from app.timeutil import utc_now_iso

DIM = 64


def _bucket(term: str) -> int:
    return int(hashlib.sha256(term.encode()).hexdigest(), 16) % DIM


class KeywordEmbedder:
    """Bag-of-words vectors (hashed content terms): similar wording → similar vectors."""

    dim = DIM

    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        self.queries.extend(texts)
        return self.embed_documents(texts)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * DIM
            for term in content_terms(text):
                vector[_bucket(term)] += 1.0
            norm = math.sqrt(sum(v * v for v in vector))
            vectors.append([v / norm for v in vector] if norm else [1.0] + [0.0] * (DIM - 1))
        return vectors


class OverlapReranker:
    """Score = share of the query's content terms found in the doc (0..1)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, docs: Sequence[str]) -> list[float]:
        self.calls.append((query, list(docs)))
        wanted = set(content_terms(query))
        if not wanted:
            return [0.0 for _ in docs]
        return [len(wanted & set(content_terms(doc))) / len(wanted) for doc in docs]


class ScriptedChat:
    """A chat adapter that replies with queued texts and records every call."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        self.calls.append(
            {"messages": list(messages), "temperature": temperature, "schema": json_schema}
        )
        if not self.replies:
            raise AssertionError("ScriptedChat ran out of replies")
        return ChatResult(text=self.replies.pop(0))

    def list_models(self) -> list[str]:
        return ["scripted"]


def answer_json(*sentences: tuple[str, list[str]]) -> str:
    return json.dumps({"sentences": [{"text": t, "chunk_ids": ids} for t, ids in sentences]})


def add_source(
    engine: Engine,
    title: str,
    *,
    status: str = "ready",
    enabled: bool = True,
    source_type: str = "paste",
    group: str = "documents",
    snapshot_at: str | None = None,
) -> str:
    now = utc_now_iso()
    source_id = uuid.uuid4().hex
    source = Source(
        id=source_id,
        type=source_type,
        title=title,
        status=status,
        enabled=enabled,
        group=group,
        snapshot_at=snapshot_at or now,
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        session.add(source)
        session.commit()
    return source_id


def add_chunk(
    engine: Engine,
    index: SpaceIndex,
    source_id: str,
    text: str,
    *,
    ordinal: int = 0,
    locator: dict[str, Any] | None = None,
    token_count: int | None = None,
    in_db: bool = True,
) -> str:
    """Insert one chunk row and index it with :class:`KeywordEmbedder` vectors."""
    chunk_id = uuid.uuid4().hex
    if in_db:
        with Session(engine) as session:
            session.add(
                Chunk(
                    id=chunk_id,
                    source_id=source_id,
                    ordinal=ordinal,
                    text=text,
                    start_offset=0,
                    end_offset=len(text),
                    locator=json.dumps(locator or {}),
                    token_count=token_count if token_count is not None else (len(text) + 3) // 4,
                )
            )
            session.commit()
    vector = KeywordEmbedder().embed_documents([text])[0]
    index.add([IndexedChunk(chunk_id, source_id, text)], [vector])
    return chunk_id


def set_source(engine: Engine, source_id: str, **values: Any) -> None:
    with Session(engine) as session:
        source = session.get(Source, source_id)
        assert source is not None
        for key, value in values.items():
            setattr(source, key, value)
        session.add(source)
        session.commit()


def parse_sse(body: str) -> list[tuple[str, Any]]:
    """Parse a text/event-stream body into (event, JSON data) pairs."""
    events: list[tuple[str, Any]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        events.append((name, json.loads("\n".join(data))))
    return events
