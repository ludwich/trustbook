from __future__ import annotations

import json
import stat
from pathlib import Path

from app.config import (
    CAPABILITY_IDS,
    Paths,
    Settings,
    SettingsStore,
    ensure_layout,
    key_hint,
)
from app.fsutil import atomic_write_json


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_paths_from_env_uses_variables(tmp_path: Path) -> None:
    paths = Paths.from_env(
        {
            "TRUSTBOOK_DATA": str(tmp_path / "d"),
            "TRUSTBOOK_BROWSE_ROOT": "/browse",
            "TRUSTBOOK_MODELS_DIR": "/models",
            "TRUSTBOOK_STATIC_DIR": "/static",
            "TRUSTBOOK_SEARXNG_URL": "http://search:9000/",
            "TRUSTBOOK_VERSION": "phase-0-rc",
            "TRUSTBOOK_MOCK_LLM": "1",
        }
    )
    assert paths.data == tmp_path / "d"
    assert paths.browse_root == Path("/browse")
    assert paths.models_dir == Path("/models")
    assert paths.static_dir == Path("/static")
    assert paths.searxng_url == "http://search:9000"
    assert paths.version == "phase-0-rc"
    assert paths.mock_llm is True
    assert paths.settings_file == tmp_path / "d" / "config" / "settings.json"
    assert paths.models_lock == Path("/models/models.lock.json")


def test_paths_defaults() -> None:
    paths = Paths.from_env({})
    expected = Path("/data") if Path("/data").is_dir() else Path.home() / "Trustbook"
    assert paths.data == expected
    assert paths.browse_root == Path("/host")
    assert paths.models_dir == Path("/opt/trustbook/models")
    assert paths.static_dir == Path("/app/static")
    assert paths.searxng_url == "http://searxng:8080"
    assert paths.version == "dev"
    assert paths.mock_llm is False


def test_ensure_layout_creates_folders(paths: Paths) -> None:
    ensure_layout(paths)
    for folder in (paths.config_dir, paths.spaces_dir, paths.clones_dir):
        assert folder.is_dir()
    assert _mode(paths.config_dir) == 0o700


def test_default_settings() -> None:
    settings = Settings()
    assert settings.power == "normal"
    assert settings.setup_completed is False
    assert settings.thresholds.refuse == 0.15
    assert settings.thresholds.verify == 0.5
    assert set(settings.capabilities) == set(CAPABILITY_IDS)
    assert settings.capability("chat").preset is None
    assert settings.capability("gate").use_chat is True
    assert settings.capability("grounding").enabled is True
    assert settings.capability("stt").mode == "bundled"
    assert settings.capability("web_search").provider == "searxng"
    assert settings.capability("tts").enabled is False


def test_save_is_atomic_and_private(paths: Paths) -> None:
    store = SettingsStore(paths)
    settings = store.load()
    settings.capabilities["chat"].api_key = "sk-secret-value-123456"
    store.save(settings)
    assert _mode(paths.settings_file) == 0o600
    assert [p.name for p in paths.config_dir.iterdir()] == ["settings.json"]
    reread = SettingsStore(paths).load()
    assert reread.capability("chat").api_key == "sk-secret-value-123456"


def test_save_tightens_existing_mode(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    paths.settings_file.write_text(Settings().model_dump_json())
    paths.settings_file.chmod(0o644)
    store = SettingsStore(paths)
    store.load()
    assert _mode(paths.settings_file) == 0o600
    store.save(store.load())
    assert _mode(paths.settings_file) == 0o600


def test_api_key_not_in_repr(paths: Paths) -> None:
    settings = Settings()
    settings.capabilities["chat"].api_key = "sk-hidden-123456789"
    assert "sk-hidden" not in repr(settings)


def test_update_applies_change(paths: Paths) -> None:
    store = SettingsStore(paths)

    def change(s: Settings) -> None:
        s.power = "high"

    assert store.update(change).power == "high"
    assert json.loads(paths.settings_file.read_text())["power"] == "high"


def test_load_returns_copy(paths: Paths) -> None:
    store = SettingsStore(paths)
    first = store.load()
    first.power = "low"
    assert store.load().power == "normal"


def test_unreadable_settings_backed_up(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    paths.settings_file.write_text("{not json")
    settings = SettingsStore(paths).load()
    assert settings.power == "normal"
    backups = list(paths.config_dir.glob("settings.json.unreadable-*"))
    assert len(backups) == 1
    assert _mode(backups[0]) == 0o600


def test_missing_capabilities_are_filled(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True)
    paths.settings_file.write_text(json.dumps({"power": "low", "capabilities": {}}))
    settings = SettingsStore(paths).load()
    assert settings.power == "low"
    assert set(settings.capabilities) == set(CAPABILITY_IDS)


def test_key_hint() -> None:
    assert key_hint(None) is None
    assert key_hint("") is None
    assert key_hint("short") == "…"
    assert key_hint("sk-abcdefgha1b2") == "…a1b2"


def test_atomic_write_json_mode(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "x.json"
    atomic_write_json(target, {"a": 1}, mode=0o640)
    assert json.loads(target.read_text()) == {"a": 1}
    assert _mode(target) == 0o640
    assert list(target.parent.iterdir()) == [target]
