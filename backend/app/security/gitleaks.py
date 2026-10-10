"""gitleaks scan of a linked folder before indexing (SPEC §7.4, DECISIONS D-024).

Findings are reduced to file, line and rule ID straight after parsing. The secret value and the
matched text are never read into our structures, logged or stored. gitleaks runs with
``--redact`` so even its temporary report holds no secret, and the report is deleted afterwards.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("trustbook.gitleaks")

TIMEOUT_S = 1800
MAX_FILE_MB = 5


@dataclass(frozen=True)
class Finding:
    file: str  # relative to the scanned folder, "/" separators
    line: int
    rule_id: str

    def to_json(self) -> dict[str, object]:
        return {"file": self.file, "line": self.line, "rule_id": self.rule_id}


class ScannerMissingError(RuntimeError):
    """The gitleaks binary is not installed."""


class ScanFailedError(RuntimeError):
    """gitleaks ran but did not produce a readable report."""


SecretScanner = Callable[[Path], list[Finding]]


def _relative(file: str, folder: Path) -> str:
    path = Path(file)
    if path.is_absolute():
        try:
            path = path.relative_to(folder)
        except ValueError:
            try:
                path = path.resolve().relative_to(folder.resolve())
            except ValueError:
                return path.name
    text = path.as_posix()
    return text[2:] if text.startswith("./") else text


def parse_report(raw: object, folder: Path) -> list[Finding]:
    """Reduce a gitleaks JSON report to (file, line, rule) — nothing else is kept."""
    if not isinstance(raw, list):
        raise ScanFailedError("The secret scan report has an unexpected format.")
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file = item.get("File")
        if not isinstance(file, str) or not file:
            continue
        line = item.get("StartLine")
        rule = item.get("RuleID")
        findings.append(
            Finding(
                file=_relative(file, folder),
                line=int(line) if isinstance(line, int) else 0,
                rule_id=str(rule) if isinstance(rule, str) else "unknown",
            )
        )
    return findings


def _command(binary: str, folder: Path, report: Path, legacy: bool) -> list[str]:
    common = [
        "--report-format",
        "json",
        "--report-path",
        str(report),
        "--redact",
        "--no-banner",
        "--exit-code",
        "0",
        "--max-target-megabytes",
        str(MAX_FILE_MB),
        "--log-level",
        "error",
    ]
    if legacy:
        return [binary, "detect", "--no-git", "--source", str(folder), *common]
    return [binary, "dir", str(folder), *common]


def scan_folder(folder: Path, binary: str | None = None) -> list[Finding]:
    """Run gitleaks on ``folder`` and return its findings.

    :raises ScannerMissingError: gitleaks is not installed.
    :raises ScanFailedError: the scan did not finish.
    """
    exe = binary or shutil.which("gitleaks")
    if not exe:
        raise ScannerMissingError("gitleaks is not installed")
    with tempfile.TemporaryDirectory(prefix="trustbook-gitleaks-") as tmp:
        report = Path(tmp) / "report.json"
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp}
        for legacy in (False, True):
            try:
                result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
                    _command(exe, folder, report, legacy),
                    capture_output=True,
                    timeout=TIMEOUT_S,
                    check=False,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise ScanFailedError("The secret scan took too long.") from exc
            except OSError as exc:
                raise ScannerMissingError(str(exc)) from exc
            unknown = b"unknown command" in result.stderr.lower()
            if result.returncode == 0 or not unknown:
                break
        if result.returncode != 0 or not report.is_file():
            # stderr may quote file content, so only the exit code is logged.
            logger.warning("gitleaks exited with code %s", result.returncode)
            raise ScanFailedError("The secret scan did not finish.")
        try:
            raw = json.loads(report.read_text("utf-8") or "[]")
        except ValueError as exc:
            raise ScanFailedError("The secret scan report could not be read.") from exc
        return parse_report(raw, folder)
