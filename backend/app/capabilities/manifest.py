"""Load capability manifests (``manifests/*.yaml``, SPEC §6)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from app.capabilities.schemas import AdapterName, Kind, Option
from app.config import CAPABILITY_IDS

MANIFEST_DIR = Path(__file__).parent / "manifests"

LocalityRule = Literal["local", "from_endpoint", "from_mode", "from_provider"]


class PresetSpec(BaseModel):
    id: str
    label: str
    adapter: AdapterName
    base_url: str | None = None
    needs_api_key: bool = False
    mock_only: bool = False


class ProviderSpec(Option):
    """A selectable service; ``mock_only`` ones exist only with ``TRUSTBOOK_MOCK_LLM=1``."""

    mock_only: bool = False


class FieldOverride(BaseModel):
    label: str | None = None
    help: str | None = None
    placeholder: str | None = None


class Manifest(BaseModel):
    id: str
    label: str
    description: str
    kind: Kind
    required: bool
    hidden: bool = False
    contract: str
    locality: LocalityRule
    ui_fields: list[str] = Field(default_factory=list)
    health_check: str
    presets: list[PresetSpec] = Field(default_factory=list)
    presets_from: str | None = None
    providers: list[ProviderSpec] = Field(default_factory=list)
    field_overrides: dict[str, FieldOverride] = Field(default_factory=dict)


def load_manifests(folder: Path = MANIFEST_DIR) -> dict[str, Manifest]:
    """Read every manifest (in UI order); ``presets_from`` copies another manifest's presets."""
    manifests: dict[str, Manifest] = {}
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text("utf-8"))
        manifest = Manifest.model_validate(data)
        if manifest.id != path.stem:
            raise ValueError(f"Manifest {path.name} has id {manifest.id!r}")
        manifests[manifest.id] = manifest
    for manifest in manifests.values():
        if manifest.presets_from:
            manifest.presets = list(manifests[manifest.presets_from].presets)
    known = {cap_id: index for index, cap_id in enumerate(CAPABILITY_IDS)}
    ordered = sorted(manifests, key=lambda cap_id: (known.get(cap_id, len(known)), cap_id))
    return {cap_id: manifests[cap_id] for cap_id in ordered}
