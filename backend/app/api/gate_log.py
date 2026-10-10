"""Gate log routes (docs/ARCHITECTURE.md, "Gate router and log")."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import ServicesDep
from app.chat.gate import GateLogNotFoundError, GateLogStore
from app.chat.schemas import GateCorrection, GateLogOut
from app.db.engine import get_engine

router = APIRouter()


def _store(services: ServicesDep, space_id: str) -> GateLogStore:
    return GateLogStore(get_engine(services.spaces.get(space_id).db_path))


@router.get("/spaces/{space_id}/gate-log")
def list_gate_log(
    space_id: str,
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[GateLogOut]:
    """The space's gate decisions, newest first."""
    return _store(services, space_id).list(limit)


@router.post("/spaces/{space_id}/gate-log/{entry_id}/correct")
def correct_gate_decision(
    space_id: str, entry_id: int, body: GateCorrection, services: ServicesDep
) -> GateLogOut:
    """Store the user's correction ("That's not right") of one decision; null clears it."""
    try:
        return _store(services, space_id).correct(entry_id, body.decision)
    except GateLogNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Gate log entry not found.") from exc
