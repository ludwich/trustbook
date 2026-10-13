"""``python -m trustbook_cli`` behaves like the console script (G3b-1).

No network: ``--version`` and a usage error never reach the API.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from trustbook_cli import __version__


def _run(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "TRUSTBOOK_DATA": str(tmp_path), "TRUSTBOOK_URL": "http://127.0.0.1:9"}
    return subprocess.run(  # noqa: S603 - our own interpreter and module, fixed arguments
        [sys.executable, "-m", "trustbook_cli", *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_version(tmp_path: Path) -> None:
    run = _run(tmp_path, "--version")
    assert run.returncode == 0
    assert run.stdout.strip() == f"trustbook-cli {__version__}"


def test_same_exit_code_and_error_document(tmp_path: Path) -> None:
    run = _run(tmp_path, "no-such-command", "--json")
    assert run.returncode == 1
    error = json.loads(run.stdout)
    assert error["schema"] == "trustbook.error/1"
    assert "invalid choice" in error["message"]
    assert "Traceback" not in run.stderr
