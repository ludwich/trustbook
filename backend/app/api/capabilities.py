"""Capability routes: list, get, update, Test and model listing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from app.api.deps import ServicesDep
from app.capabilities.registry import ConfigError
from app.capabilities.schemas import (
    CapabilityConfigUpdate,
    CapabilityOut,
    ModelsOut,
    TestResult,
)

router = APIRouter()

OptionalDraft = Annotated[CapabilityConfigUpdate | None, Body()]


@router.get("/capabilities")
def list_capabilities(services: ServicesDep) -> list[CapabilityOut]:
    return services.capabilities.list_all()


@router.get("/capabilities/{cap_id}")
def get_capability(cap_id: str, services: ServicesDep) -> CapabilityOut:
    return services.capabilities.get(cap_id)


@router.put("/capabilities/{cap_id}")
def update_capability(
    cap_id: str, body: CapabilityConfigUpdate, services: ServicesDep
) -> CapabilityOut:
    return services.capabilities.update(cap_id, body)


@router.post("/capabilities/{cap_id}/test")
def test_capability(cap_id: str, services: ServicesDep, body: OptionalDraft = None) -> TestResult:
    services.capabilities.manifest(cap_id)
    return services.health.test(cap_id, body)


@router.post("/capabilities/{cap_id}/models")
def list_models(cap_id: str, services: ServicesDep, body: OptionalDraft = None) -> ModelsOut:
    services.capabilities.manifest(cap_id)
    if body is not None and not body.is_empty():
        services.capabilities.merge(cap_id, body)  # invalid drafts → 422
    try:
        models = services.health.list_models(cap_id, body)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelsOut(models=models)
