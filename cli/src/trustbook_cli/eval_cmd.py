"""``trustbook eval``: run the evaluation runner (``eval/runner.py``) with the same base URL.

The runner ships in the image (``/app/eval``) and in a checkout of the repository; a CLI installed
with pip has no runner, so ``TRUSTBOOK_EVAL_RUNNER`` can point at one. The runner reads YAML, so
outside the image it needs the ``eval`` extra (``pip install "trustbook-cli[eval]"``).

The runner's exit codes are mapped to the CLI's (D-087), where 2 means "refused":

======  ===================================================  ======
Runner  Meaning                                              CLI
======  ===================================================  ======
0       report written, every target met                     0
1       a ``--min-*``/``--max-*`` target missed, or a setup  4
        job did not end as expected
2       invalid input or the API unreachable                 1
130     interrupted                                          130
other   anything else (a crash, killed by a signal)          1
======  ===================================================  ======
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from trustbook_cli.client import TrustbookError
from trustbook_cli.output import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK, EXIT_TARGET_MISSED

IMAGE_RUNNER = Path("/app/eval/runner.py")
# eval/runner.py's exit codes.
RUNNER_OK = 0
RUNNER_TARGET_MISSED = 1
RUNNER_INTERRUPTED = 130
EVAL_MODULES = {"yaml": "PyYAML"}
INSTALL_HINT = 'pip install "trustbook-cli[eval]"'


def check_eval_dependencies(find_spec: Callable[[str], object | None] | None = None) -> None:
    """Fail early, with an install hint, if a module the runner imports is missing.

    The runner runs with this Python, so it sees the same packages.

    :raises TrustbookError: PyYAML (the ``eval`` extra) is not installed.
    """
    find = find_spec or importlib.util.find_spec
    missing = [package for module, package in EVAL_MODULES.items() if find(module) is None]
    if missing:
        raise TrustbookError(
            f"`trustbook eval` needs {', '.join(missing)}, which is not installed. Install the "
            f"CLI with its eval extra: {INSTALL_HINT} "
            '(from a checkout of the repository: pip install "./cli[eval]").'
        )


def cli_exit_code(runner_code: int) -> int:
    """The CLI's exit code for the runner's (the table in this module's docstring)."""
    if runner_code == RUNNER_OK:
        return EXIT_OK
    if runner_code == RUNNER_TARGET_MISSED:
        return EXIT_TARGET_MISSED
    if runner_code in (RUNNER_INTERRUPTED, -signal.SIGINT):
        return EXIT_INTERRUPTED
    return EXIT_ERROR


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
    """Run the runner with this Python and return its exit code, mapped by :func:`cli_exit_code`.

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
        return cli_exit_code(proc.wait())
