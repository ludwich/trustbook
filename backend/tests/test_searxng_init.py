from __future__ import annotations

import re
import stat
from pathlib import Path

from app.searxng_init import main

TEMPLATE = 'use_default_settings: true\nserver:\n  secret_key: "ultrasecretkey"\n'


def _env(tmp_path: Path) -> dict[str, str]:
    template = tmp_path / "template.yml"
    template.write_text(TEMPLATE)
    return {
        "TRUSTBOOK_SEARXNG_TEMPLATE": str(template),
        "TRUSTBOOK_SEARXNG_CONFIG_DIR": str(tmp_path / "etc" / "searxng"),
    }


def test_writes_settings_with_secret(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert main(env) == 0
    target = tmp_path / "etc" / "searxng" / "settings.yml"
    text = target.read_text()
    assert "ultrasecretkey" not in text
    assert re.search(r'secret_key: "[0-9a-f]{64}"', text)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_idempotent(tmp_path: Path) -> None:
    env = _env(tmp_path)
    main(env)
    target = tmp_path / "etc" / "searxng" / "settings.yml"
    first = target.read_text()
    assert main(env) == 0
    assert target.read_text() == first


def test_missing_template(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env["TRUSTBOOK_SEARXNG_TEMPLATE"] = str(tmp_path / "nope.yml")
    assert main(env) == 1


def test_template_without_placeholder(tmp_path: Path) -> None:
    env = _env(tmp_path)
    Path(env["TRUSTBOOK_SEARXNG_TEMPLATE"]).write_text("server: {}\n")
    assert main(env) == 0
