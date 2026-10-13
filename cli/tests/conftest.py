"""No test reads the real settings.json, environment defaults or the network."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (
        "TRUSTBOOK_URL",
        "TRUSTBOOK_SPACE",
        "TRUSTBOOK_BROWSE_ROOT",
        "TRUSTBOOK_EVAL_RUNNER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRUSTBOOK_DATA", str(tmp_path / "no-data"))
