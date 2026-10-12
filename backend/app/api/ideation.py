"""Ideation routes (docs/ARCHITECTURE.md, "Phase 4: ideation").

Turns stream Server-Sent Events like chat answers (``status`` … then ``done`` or ``error``), in
the space's request-log context, so every model call of a session is in the request log.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from app.api.chat import CHAT_NOT_READY, stream_events
from app.api.deps import ServicesDep
from app.api.grounded import refuse_threshold, retriever, verifier_factory
from app.chat.service import EventSink
from app.db.engine import get_engine
from app.ideation.repository import IdeationStore
from app.ideation.schemas import (
    AnswerCreate,
    EstimateOut,
    SessionCreate,
    SessionOut,
    SessionSummary,
)
from app.ideation.service import IdeationError, IdeationService, estimate
from app.retrieval.service import Retrieval
from app.services import Services
from app.spaces.service import SpaceRecord, slugify

router = APIRouter(prefix="/spaces/{space_id}/ideation")

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _service(services: Services, space: SpaceRecord) -> IdeationService:
    def retrieve(query: str) -> Retrieval:
        return retriever(services, space)(query)

    return IdeationService(
        IdeationStore(get_engine(space.db_path)),
        space_id=space.id,
        space_folder=space.folder,
        retrieve=retrieve,
        chat_adapter=services.chat_adapter,
        refuse_threshold=refuse_threshold(services),
        verifier=verifier_factory(services, space),
    )


def _fail(exc: IdeationError) -> NoReturn:
    raise HTTPException(status_code=exc.status, detail=exc.message) from exc


def _stream(
    space: SpaceRecord, service: IdeationService, session_id: str, *, after: bool
) -> StreamingResponse:
    def run(emit: EventSink) -> None:
        try:
            service.run_turn(session_id, emit, after_answer=after)
        except IdeationError as exc:
            emit("error", {"message": exc.message})

    return StreamingResponse(
        stream_events(run, space), media_type="text/event-stream", headers=_SSE_HEADERS
    )


def _ready_for_turn(services: Services, service: IdeationService, session_id: str) -> None:
    """404 / 409 before a stream starts: unknown or finished session, chat not set up, busy."""
    try:
        session = service.get(session_id)
    except IdeationError as exc:
        _fail(exc)
    if session.status == "finished":
        _fail(IdeationError("This session is finished. Press Continue to add to it."))
    if not services.chat_state().ready:
        raise HTTPException(status_code=409, detail=CHAT_NOT_READY)
    if service.busy(session_id):
        _fail(IdeationError("The next question is still being prepared. Wait a moment."))


@router.get("/estimate")
def get_estimate(space_id: str, services: ServicesDep) -> EstimateOut:
    space = services.spaces.get(space_id)
    turn, seed = estimate(get_engine(space.db_path))
    return EstimateOut(tokens_per_turn=turn, seed_tokens=seed, remote=services.chat_state().remote)


@router.get("/sessions")
def list_sessions(space_id: str, services: ServicesDep) -> list[SessionSummary]:
    space = services.spaces.get(space_id)
    return IdeationStore(get_engine(space.db_path)).list_sessions()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(space_id: str, body: SessionCreate, services: ServicesDep) -> SessionOut:
    space = services.spaces.get(space_id)
    return IdeationStore(get_engine(space.db_path)).create(body.idea)


@router.get("/sessions/{session_id}")
def get_session(space_id: str, session_id: str, services: ServicesDep) -> SessionOut:
    service = _service(services, services.spaces.get(space_id))
    try:
        return service.get(session_id)
    except IdeationError as exc:
        _fail(exc)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(space_id: str, session_id: str, services: ServicesDep) -> Response:
    service = _service(services, services.spaces.get(space_id))
    try:
        service.delete(session_id)
    except IdeationError as exc:
        _fail(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/turn",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def post_turn(space_id: str, session_id: str, services: ServicesDep) -> StreamingResponse:
    space = services.spaces.get(space_id)
    service = _service(services, space)
    _ready_for_turn(services, service, session_id)
    return _stream(space, service, session_id, after=False)


@router.post(
    "/sessions/{session_id}/answer",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def post_answer(
    space_id: str, session_id: str, body: AnswerCreate, services: ServicesDep
) -> StreamingResponse:
    space = services.spaces.get(space_id)
    service = _service(services, space)
    _ready_for_turn(services, service, session_id)
    try:
        service.answer(session_id, body)
    except IdeationError as exc:
        _fail(exc)
    return _stream(space, service, session_id, after=True)


@router.post("/sessions/{session_id}/nodes/{node_id}/dismiss")
def dismiss_node(space_id: str, session_id: str, node_id: str, services: ServicesDep) -> SessionOut:
    service = _service(services, services.spaces.get(space_id))
    try:
        return service.dismiss(session_id, node_id)
    except IdeationError as exc:
        _fail(exc)


@router.post("/sessions/{session_id}/finish")
def finish_session(space_id: str, session_id: str, services: ServicesDep) -> SessionOut:
    service = _service(services, services.spaces.get(space_id))
    try:
        return service.finish(session_id)
    except IdeationError as exc:
        _fail(exc)


@router.post("/sessions/{session_id}/continue")
def continue_session(space_id: str, session_id: str, services: ServicesDep) -> SessionOut:
    service = _service(services, services.spaces.get(space_id))
    try:
        return service.reopen(session_id)
    except IdeationError as exc:
        _fail(exc)


@router.get(
    "/sessions/{session_id}/brief",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
)
def export_brief(space_id: str, session_id: str, services: ServicesDep) -> Response:
    service = _service(services, services.spaces.get(space_id))
    try:
        session, path = service.brief(session_id)
    except IdeationError as exc:
        _fail(exc)
    name = f"idea-brief-{slugify(session.title) or session.id[:8]}.md"
    return Response(
        content=path.read_text("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
