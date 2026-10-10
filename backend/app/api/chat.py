"""Chat routes: status, conversations and the streamed answer (docs/ARCHITECTURE.md, "Phase 1")."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.api.deps import ServicesDep
from app.capabilities.adapters.base import AdapterError, ChatAdapter
from app.capabilities.adapters.factory import build_chat_adapter
from app.capabilities.bundled import ProviderUnavailableError
from app.capabilities.http import space_context
from app.capabilities.registry import ConfigError
from app.chat.gate import GATE_TIMEOUT_S, Gate, GateLogStore
from app.chat.repository import ChatStore, ConversationNotFoundError
from app.chat.schemas import (
    ChatStatusOut,
    ConversationCreate,
    ConversationOut,
    MessageCreate,
    MessageOut,
)
from app.chat.service import ChatService, EventSink
from app.db.engine import get_engine
from app.grounding.verify import Verifier, build_verifier, checker_scores
from app.retrieval.service import Retrieval, retrieve
from app.retrieval.store import source_counts
from app.services import Services
from app.spaces.service import SpaceRecord

logger = logging.getLogger("trustbook.api.chat")

router = APIRouter()

CHAT_NOT_READY = "Set up the chat model in Settings first."
MODELS_MISSING = "The search models are not available in this install, so no answer can be given."
UNEXPECTED = "Something went wrong while answering. Try again."


def _store(space: SpaceRecord) -> ChatStore:
    return ChatStore(get_engine(space.db_path))


def _conversation(store: ChatStore, conversation_id: str) -> ConversationOut:
    try:
        return store.get_conversation(conversation_id)
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found.") from exc


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def stream_events(run: Callable[[EventSink], None], space: SpaceRecord) -> Iterator[str]:
    """Run ``run`` in a thread (inside the space's request-log context) and yield its events.

    Failures become one ``error`` event, so every stream ends with ``done`` or ``error``.
    """
    events: queue.Queue[str | None] = queue.Queue()

    def emit(name: str, data: dict[str, Any]) -> None:
        events.put(sse(name, data))

    def worker() -> None:
        try:
            with space_context(space.id, space.name):
                run(emit)
        except AdapterError as exc:
            emit("error", {"message": exc.message})
        except ConfigError as exc:
            emit("error", {"message": str(exc)})
        except ProviderUnavailableError:
            emit("error", {"message": MODELS_MISSING})
        except Exception:
            logger.exception("Answering failed in space %s", space.id)
            emit("error", {"message": UNEXPECTED})
        finally:
            events.put(None)

    threading.Thread(target=worker, name="trustbook-chat", daemon=True).start()
    while (item := events.get()) is not None:
        yield item


def gate_adapter(services: Services) -> Callable[[], ChatAdapter] | None:
    """The gate's adapter factory, or None when the gate is not ready (not set up, failing)."""
    if services.capabilities.get("gate").status != "ready":
        return None

    def build() -> ChatAdapter:
        endpoint = services.capabilities.endpoint("gate")
        return build_chat_adapter(endpoint, services.http, timeout=GATE_TIMEOUT_S)

    return build


def chat_service(services: Services, space: SpaceRecord) -> ChatService:
    engine = get_engine(space.db_path)
    index = services.indexes.for_space(space.id)

    def run_retrieval(question: str) -> Retrieval:
        providers = services.providers
        with providers.use.shared():  # waits while calibration measures
            return retrieve(engine, index, providers.embedder(), providers.reranker(), question)

    def verifier() -> Verifier:
        return build_verifier(
            services.grounding_settings(), checker_scores(services.providers), space.folder
        )

    return ChatService(
        ChatStore(engine),
        retrieve=run_retrieval,
        chat_adapter=services.chat_adapter,
        refuse_threshold=services.settings.load().thresholds.refuse,
        verifier=verifier,
        gate=Gate(GateLogStore(engine), adapter=gate_adapter(services)),
    )


@router.get("/spaces/{space_id}/chat/status")
def chat_status(space_id: str, services: ServicesDep) -> ChatStatusOut:
    space = services.spaces.get(space_id)
    with Session(get_engine(space.db_path)) as session:
        counts = source_counts(session)
    return ChatStatusOut(
        enabled_sources=counts.enabled_ready,
        ready_sources=counts.ready,
        total_sources=counts.total,
        chat_ready=services.chat_state().ready,
    )


@router.get("/spaces/{space_id}/conversations")
def list_conversations(space_id: str, services: ServicesDep) -> list[ConversationOut]:
    return _store(services.spaces.get(space_id)).list_conversations()


@router.post("/spaces/{space_id}/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(
    space_id: str, services: ServicesDep, body: ConversationCreate | None = None
) -> ConversationOut:
    title = body.title.strip() if body and body.title else None
    return _store(services.spaces.get(space_id)).create_conversation(title)


@router.delete(
    "/spaces/{space_id}/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(space_id: str, conversation_id: str, services: ServicesDep) -> Response:
    store = _store(services.spaces.get(space_id))
    _conversation(store, conversation_id)
    store.delete_conversation(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/spaces/{space_id}/conversations/{conversation_id}/messages")
def list_messages(space_id: str, conversation_id: str, services: ServicesDep) -> list[MessageOut]:
    store = _store(services.spaces.get(space_id))
    _conversation(store, conversation_id)
    return store.list_messages(conversation_id)


@router.post(
    "/spaces/{space_id}/conversations/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def post_message(
    space_id: str, conversation_id: str, body: MessageCreate, services: ServicesDep
) -> StreamingResponse:
    space = services.spaces.get(space_id)
    store = _store(space)
    _conversation(store, conversation_id)
    if not services.chat_state().ready:
        raise HTTPException(status_code=409, detail=CHAT_NOT_READY)
    question = body.content
    store.add_user_message(conversation_id, question)
    service = chat_service(services, space)

    def run(emit: EventSink) -> None:
        service.respond(conversation_id, question, emit)

    return StreamingResponse(
        stream_events(run, space),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
