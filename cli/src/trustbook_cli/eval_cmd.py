"""``trustbook eval``: run the evaluation runner (``eval/runner.py``) with the same base URL.

The runner ships in the image (``/app/eval``) and in a checkout of the repository; a CLI installed
with pip has no runner, so ``TRUSTBOOK_EVAL_RUNNER`` can point at one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from trustbook_cli.client import TrustbookError

IMAGE_RUNNER = Path("/app/eval/runner.py")


def find_runner(env: Mapping[str, str], cwd: Path | None = None) -> Path:
    """``TRUSTBOOK_EVAL_RUNNER``, the image's runner, or ``eval/runner.py`` above ``cwd``/here.

    :raises TrustbookError: no runner was found.
    """
    if env.get("TRUSTBOOK_EVAL_RUNNER"):
        path = Path(env["TRUSTBOOK_EVAL_RUNNER"])
        if not path.is_file():
            raise TrustbookError(f"TRUSTBOOK_EVAL_RUNNER={path} is not a file.")
        return path
    candidates = [IMAGE_RUNNER]
    for start in (cwd or Path.cwd(), Path(__file__).resolve().parent):
        candidates.extend(folder / "eval" / "runner.py" for folder in (start, *start.parents))
    for path in candidates:
        if path.is_file():
            return path
    raise TrustbookError(
        "The eval runner was not found. Run this in the container (`docker compose exec app "
        "trustbook eval …`), in a checkout of the repository, or set TRUSTBOOK_EVAL_RUNNER."
    )


def runner_command(runner: Path, args: Sequence[str], base_url: str) -> list[str]:
    """The runner's command line; ``--base-url`` is added unless ``args`` set it."""
    command = [sys.executable, str(runner)]
    if not any(a == "--base-url" or a.startswith("--base-url=") for a in args):
        command += ["--base-url", base_url]
    return command + list(args)


def _pump(stream: IO[str], write: Callable[[str], None]) -> None:
    for line in stream:
        write(line)


def run_eval(
    runner: Path,
    args: Sequence[str],
    base_url: str,
    *,
    out: Callable[[str], None],
    err: Callable[[str], None],
) -> int:
    """Run the runner with this Python; its exit code is returned unchanged.

    Its stdout and stderr are passed on line by line through ``out`` and ``err`` (the CLI's
    redacting writers), so its progress still shows while it runs.
    """
    with subprocess.Popen(  # noqa: S603
        runner_command(runner, args, base_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ) as proc:
        assert proc.stdout is not None and proc.stderr is not None
        pump = threading.Thread(target=_pump, args=(proc.stderr, err), daemon=True)
        pump.start()
        _pump(proc.stdout, out)
        pump.join()
        return proc.wait()
