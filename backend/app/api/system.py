"""Health, setup and settings routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ServicesDep
from app.config import Power, Settings, Thresholds
from app.jobs.pool import workers_for_power

router = APIRouter()


class HealthOut(BaseModel):
    status: str
    version: str


class SetupOut(BaseModel):
    completed: bool
    chat_ready: bool


class SettingsOut(BaseModel):
    power: Power
    setup_completed: bool
    workers: int
    thresholds: Thresholds
    hints_dismissed: list[str]


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    power: Power | None = None
    dismiss_hint: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")


def _settings_out(settings: Settings) -> SettingsOut:
    return SettingsOut(
        power=settings.power,
        setup_completed=settings.setup_completed,
        workers=workers_for_power(settings.power),
        thresholds=settings.thresholds,
        hints_dismissed=list(settings.hints_dismissed),
    )


@router.get("/health")
def health(services: ServicesDep) -> HealthOut:
    return HealthOut(status="ok", version=services.paths.version)


@router.get("/setup")
def setup_status(services: ServicesDep) -> SetupOut:
    chat = services.capabilities.get("chat")
    return SetupOut(
        completed=services.settings.load().setup_completed, chat_ready=chat.status == "ready"
    )


@router.post("/setup/complete")
def setup_complete(services: ServicesDep) -> SetupOut:
    chat = services.capabilities.get("chat")
    if chat.status == "not_configured":
        raise HTTPException(status_code=409, detail="Set up the chat model first.")

    def apply(settings: Settings) -> None:
        settings.setup_completed = True

    services.settings.update(apply)
    return SetupOut(completed=True, chat_ready=chat.status == "ready")


@router.get("/settings")
def get_settings(services: ServicesDep) -> SettingsOut:
    return _settings_out(services.settings.load())


@router.put("/settings")
def put_settings(body: SettingsUpdate, services: ServicesDep) -> SettingsOut:
    def apply(settings: Settings) -> None:
        if body.power is not None:
            settings.power = body.power
        if body.dismiss_hint is not None and body.dismiss_hint not in settings.hints_dismissed:
            settings.hints_dismissed.append(body.dismiss_hint)

    updated = services.settings.update(apply)
    services.pool.resize(workers_for_power(updated.power))
    return _settings_out(updated)
