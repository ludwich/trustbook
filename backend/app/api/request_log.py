"""Request log routes (what went out to which endpoint)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel

from app.api.deps import ServicesDep

router = APIRouter()


class RequestLogEntry(BaseModel):
    id: int
    timestamp: str
    capability: str
    endpoint_host: str
    space_id: str | None
    space_name: str | None
    bytes_sent: int
    tokens_sent: int | None
    excerpt: str


@router.get("/request-log")
def list_request_log(
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[RequestLogEntry]:
    rows = services.request_log.recent(limit=limit, before_id=before_id)
    names = services.spaces.names() if any(r.space_id for r in rows) else {}
    return [
        RequestLogEntry(
            id=int(row.id or 0),
            timestamp=row.timestamp,
            capability=row.capability,
            endpoint_host=row.endpoint_host,
            space_id=row.space_id,
            space_name=names.get(row.space_id, row.space_name) if row.space_id else None,
            bytes_sent=row.bytes_sent,
            tokens_sent=row.tokens_sent,
            excerpt=row.excerpt,
        )
        for row in rows
    ]


@router.delete("/request-log", status_code=status.HTTP_204_NO_CONTENT)
def clear_request_log(services: ServicesDep) -> Response:
    services.request_log.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
