"""API shapes for capabilities (see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.config import TestResult

FieldType = Literal["text", "url", "password", "select", "toggle"]
Status = Literal["ready", "not_configured", "error", "disabled"]
Locality = Literal["local", "remote"]
Kind = Literal["endpoint", "bundled", "bundled_with_endpoint_override"]
AdapterName = Literal["openai_compatible", "azure_openai", "anthropic", "mock"]

__all__ = [
    "AdapterName",
    "BundledOut",
    "CapabilityConfig",
    "CapabilityConfigUpdate",
    "CapabilityOut",
    "FieldOut",
    "FieldType",
    "Kind",
    "Locality",
    "ModelsOut",
    "Option",
    "PresetOut",
    "Status",
    "TestResult",
]


class Option(BaseModel):
    value: str
    label: str


class FieldOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type: FieldType
    required: bool
    placeholder: str | None = None
    help: str | None = None
    options: list[Option] | None = None


class PresetOut(BaseModel):
    id: str
    label: str
    adapter: AdapterName
    base_url: str | None
    needs_api_key: bool


class CapabilityConfig(BaseModel):
    enabled: bool
    preset: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_version: str | None = None
    api_key_set: bool = False
    api_key_hint: str | None = None
    use_chat: bool | None = None
    provider: Literal["searxng", "brave", "tavily"] | None = None
    mode: Literal["bundled", "endpoint"] | None = None


class CapabilityConfigUpdate(BaseModel):
    """Any subset of the editable fields. ``api_key: ""`` clears the saved key."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    preset: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_version: str | None = None
    api_key: str | None = None
    use_chat: bool | None = None
    provider: str | None = None
    mode: str | None = None

    def is_empty(self) -> bool:
        return not self.model_fields_set


class BundledOut(BaseModel):
    model_id: str
    revision: str
    license: str
    present: bool
    fallback: bool


class CapabilityOut(BaseModel):
    id: str
    label: str
    description: str
    kind: Kind
    required: bool
    hidden: bool
    contract: str
    fields: list[FieldOut]
    presets: list[PresetOut]
    providers: list[Option]
    config: CapabilityConfig
    locality: Locality | None
    status: Status
    status_detail: str
    last_test: TestResult | None
    bundled: BundledOut | None


class ModelsOut(BaseModel):
    models: list[str]
