"""Space routes and the active-space setting."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import ServicesDep
from app.spaces.schemas import ActiveSpace, SpaceCreate, SpaceOut, SpaceUpdate
from app.spaces.service import SpaceRecord

router = APIRouter()


def _out(services: ServicesDep, record: SpaceRecord) -> SpaceOut:
    return SpaceOut.build(record, services.spaces.source_count(record))


@router.get("/spaces")
def list_spaces(services: ServicesDep) -> list[SpaceOut]:
    return [_out(services, record) for record in services.spaces.list_all()]


@router.post("/spaces", status_code=status.HTTP_201_CREATED)
def create_space(body: SpaceCreate, services: ServicesDep) -> SpaceOut:
    return _out(services, services.spaces.create(body.name, body.description))


@router.get("/spaces/{space_id}")
def get_space(space_id: str, services: ServicesDep) -> SpaceOut:
    return _out(services, services.spaces.get(space_id))


@router.patch("/spaces/{space_id}")
def update_space(space_id: str, body: SpaceUpdate, services: ServicesDep) -> SpaceOut:
    record = services.spaces.update(space_id, name=body.name, description=body.description)
    return _out(services, record)


@router.delete("/spaces/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_space(space_id: str, services: ServicesDep) -> Response:
    services.spaces.delete(space_id)
    if services.pool.active_space_id == space_id:
        services.pool.set_active_space(None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/active-space", status_code=status.HTTP_204_NO_CONTENT)
def set_active_space(body: ActiveSpace, services: ServicesDep) -> Response:
    if body.space_id is not None and services.spaces.find(body.space_id) is None:
        raise HTTPException(status_code=404, detail="Space not found.")
    services.pool.set_active_space(body.space_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
