"""Paths from the environment and the persisted ``settings.json``.

``settings.json`` lives in ``<data>/config/`` and is the only place API keys are stored.
It is written atomically and always has mode 600.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.fsutil import atomic_write_text
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.config")

Power = Literal["low", "normal", "high"]

CAPABILITY_IDS: tuple[str, ...] = (
    "chat",
    "gate",
    "embeddings",
    "reranker",
    "grounding",
    "stt",
    "web_search",
    "tts",
)


def _env_flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Paths:
    """All file-system locations and environment-driven options."""

    data: Path
    browse_root: Path
    models_dir: Path
    static_dir: Path
    searxng_url: str
    version: str
    mock_llm: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Paths:
        """Build paths from environment variables (``os.environ`` by default)."""
        source = os.environ if env is None else env
        data_env = source.get("TRUSTBOOK_DATA")
        if data_env:
            data = Path(data_env)
        elif Path("/data").is_dir():
            data = Path("/data")
        else:
            data = Path.home() / "Trustbook"
        return cls(
            data=data,
            browse_root=Path(source.get("TRUSTBOOK_BROWSE_ROOT") or "/host"),
            models_dir=Path(source.get("TRUSTBOOK_MODELS_DIR") or "/opt/trustbook/models"),
            static_dir=Path(source.get("TRUSTBOOK_STATIC_DIR") or "/app/static"),
            searxng_url=(source.get("TRUSTBOOK_SEARXNG_URL") or "http://searxng:8080").rstrip("/"),
            version=source.get("TRUSTBOOK_VERSION") or "dev",
            mock_llm=_env_flag(source.get("TRUSTBOOK_MOCK_LLM")),
        )

    @property
    def config_dir(self) -> Path:
        return self.data / "config"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def calibration_file(self) -> Path:
        return self.config_dir / "calibration.json"

    @property
    def global_db(self) -> Path:
        return self.data / "global.db"

    @property
    def spaces_dir(self) -> Path:
        return self.data / "spaces"

    @property
    def clones_dir(self) -> Path:
        return self.data / "clones"

    @property
    def models_lock(self) -> Path:
        return self.models_dir / "models.lock.json"


def ensure_layout(paths: Paths) -> None:
    """Create the data folder layout (``config/``, ``spaces/``, ``clones/``) if missing."""
    for folder in (paths.data, paths.config_dir, paths.spaces_dir, paths.clones_dir):
        folder.mkdir(parents=True, exist_ok=True)
    # The config folder holds API keys: keep it private to the owner.
    paths.config_dir.chmod(0o700)


class TestResult(BaseModel):
    """Outcome of a capability Test (also the API ``TestResult`` shape)."""

    __test__ = False  # not a pytest test class

    ok: bool
    message: str
    latency_ms: int | None = None
    tested_at: str = Field(default_factory=utc_now_iso)


class CapabilitySettings(BaseModel):
    """Saved configuration of one capability. ``api_key`` never leaves this file."""

    enabled: bool = True
    preset: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_version: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    use_chat: bool | None = None
    provider: str | None = None
    mode: str | None = None
    last_test: TestResult | None = None


class Thresholds(BaseModel):
    """Grounding thresholds (SPEC §8)."""

    refuse: float = 0.15
    verify: float = 0.5


def default_capabilities() -> dict[str, CapabilitySettings]:
    """Default per-capability configuration (chat starts unconfigured)."""
    return {
        "chat": CapabilitySettings(),
        "gate": CapabilitySettings(use_chat=True),
        "embeddings": CapabilitySettings(),
        "reranker": CapabilitySettings(),
        "grounding": CapabilitySettings(enabled=True),
        "stt": CapabilitySettings(mode="bundled"),
        "web_search": CapabilitySettings(provider="searxng"),
        "tts": CapabilitySettings(enabled=False),
    }


class Settings(BaseModel):
    """Everything persisted in ``config/settings.json``."""

    version: int = 1
    power: Power = "normal"
    setup_completed: bool = False
    thresholds: Thresholds = Field(default_factory=Thresholds)
    hints_dismissed: list[str] = Field(default_factory=list)  # one-time hints, e.g. chat_grounding
    capabilities: dict[str, CapabilitySettings] = Field(default_factory=default_capabilities)

    def capability(self, cap_id: str) -> CapabilitySettings:
        """Return the saved config for ``cap_id`` (defaults if it was never saved)."""
        existing = self.capabilities.get(cap_id)
        if existing is not None:
            return existing
        return default_capabilities().get(cap_id, CapabilitySettings())


def key_hint(api_key: str | None) -> str | None:
    """Return a short, safe hint for a key, e.g. ``"…a1b2"``. Short keys reveal nothing."""
    if not api_key:
        return None
    if len(api_key) < 12:
        return "…"
    return f"…{api_key[-4:]}"


class SettingsStore:
    """Thread-safe load/save of ``settings.json`` (atomic write, mode 600)."""

    def __init__(self, paths: Paths) -> None:
        self._paths = paths
        self._lock = threading.RLock()
        self._cache: Settings | None = None

    @property
    def path(self) -> Path:
        return self._paths.settings_file

    def load(self) -> Settings:
        """Return the current settings, reading the file on first use."""
        with self._lock:
            if self._cache is None:
                self._cache = self._read()
            return self._cache.model_copy(deep=True)

    def save(self, settings: Settings) -> Settings:
        """Persist ``settings`` atomically and return a copy."""
        with self._lock:
            self._paths.config_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, settings.model_dump_json(indent=2) + "\n", mode=0o600)
            self._cache = settings.model_copy(deep=True)
            return settings.model_copy(deep=True)

    def update(self, change: Callable[[Settings], None]) -> Settings:
        """Load, apply ``change`` in place, save, and return the new settings."""
        with self._lock:
            current = self.load()
            change(current)
            return self.save(current)

    def _read(self) -> Settings:
        path = self.path
        if not path.exists():
            return Settings()
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            settings = Settings.model_validate(raw)
        except (OSError, ValueError, ValidationError):
            backup = path.with_name(f"settings.json.unreadable-{utc_now_iso().replace(':', '-')}")
            path.replace(backup)
            backup.chmod(0o600)
            logger.warning(
                "settings.json could not be read; moved it to %s and use defaults", backup
            )
            return Settings()
        # Fill in capabilities added after the file was written.
        for cap_id, default in default_capabilities().items():
            settings.capabilities.setdefault(cap_id, default)
        return settings
