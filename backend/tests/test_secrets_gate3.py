"""Gate 3 (SPEC §14, §7.4): a planted fake secret in a linked folder is skipped and reported by
file, line and rule ID only.

The secrets are generated at runtime from pieces, so no secret-looking literal sits in the
repository (GitHub push protection, CLAUDE.md). One test uses the real gitleaks binary (the image
ships it; skipped with a reason when it is not on PATH), one a pure-Python scanner (always runs).
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import shutil
import string
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Paths
from app.parse.documents import DocumentParser
from app.security.gitleaks import Finding, scan_folder
from tests.conftest import AppFactory
from tests.ingest_helpers import run_jobs, services_of


def _fake_aws_key(rng: random.Random) -> str:
    """An AWS-access-key-shaped string (prefix + 16 base32 characters), random per run."""
    alphabet = string.ascii_uppercase + "234567"
    return "AK" + "IA" + "".join(rng.choice(alphabet) for _ in range(16))


def _fake_private_key(rng: random.Random) -> str:
    """A PEM-shaped private key block with random (meaningless) content."""
    body = base64.b64encode(rng.randbytes(600)).decode()
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    kind = "RSA " + "PRIVATE" + " KEY"
    return "\n".join([f"-----BEGIN {kind}-----", *lines, f"-----END {kind}-----"])


def _planted_folder(paths: Paths) -> tuple[str, list[str]]:
    """``work/leaky``: clean code, an AWS-shaped key in settings.py (line 3), a key file."""
    rng = random.Random()
    aws, pem = _fake_aws_key(rng), _fake_private_key(rng)
    root = paths.browse_root / "work" / "leaky"
    (root / "app").mkdir(parents=True)
    (root / "app" / "client.py").write_text(
        "def fetch(url):\n    return url\n\n\nclass Client:\n    def get(self):\n        return 1\n"
    )
    (root / "app" / "settings.py").write_text(
        f'REGION = "eu-north-1"\nDEBUG = False\naws_access_key_id = "{aws}"\n'
    )
    (root / "deploy").mkdir()
    (root / "deploy" / "server.pem").write_text(pem + "\n")
    (root / "README.md").write_text("# Leaky\n\nA demo project with a planted fake secret.")
    return "work/leaky", [aws, pem.split("\n")[1], pem.split("\n")[2]]


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    return client


def _index_folder(client: TestClient, path: str) -> tuple[str, dict[str, Any]]:
    sid: str = client.post("/api/spaces", json={"name": "Gate 3"}).json()["id"]
    created = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": path})
    assert created.status_code == 201, created.text
    run_jobs(services_of(client))
    source: dict[str, Any] = client.get(f"/api/spaces/{sid}/sources/{created.json()['id']}").json()
    return sid, source


def _assert_gate3(
    client: TestClient, sid: str, source: dict[str, Any], secrets: list[str], caplog: Any
) -> dict[str, Any]:
    assert source["status"] == "ready", source
    assert source["stats"]["skipped_secrets"] == 2
    assert "2 files skipped: possible secrets." in (source["quality_warning"] or "")
    report = client.get(f"/api/spaces/{sid}/sources/{source['id']}/secrets").json()
    assert report["files_skipped"] == 2
    files = {f["file"] for f in report["findings"]}
    assert files == {"app/settings.py", "deploy/server.pem"}
    for finding in report["findings"]:
        assert set(finding) == {"file", "line", "rule_id"}  # never the value
    assert {"file": "app/settings.py", "line": 3} in [
        {"file": f["file"], "line": f["line"]} for f in report["findings"]
    ]

    snapshot = client.get(f"/api/spaces/{sid}/sources/{source['id']}/snapshot").json()
    indexed_files = {s["file"] for s in snapshot["map"]["segments"] if "file" in s}
    assert indexed_files == {"app/client.py", "README.md"}  # the clean files are indexed
    services = services_of(client)
    chunks = services.ingest.repo(sid).chunks(source["id"])
    stored = client.get(f"/api/spaces/{sid}/sources").text
    raw_report = (services.ingest.repo(sid).folder(source["id"]) / "secrets.json").read_text()
    everything = [
        snapshot["markdown"],
        json.dumps(report),
        stored,
        raw_report,
        caplog.text,
        *(c.text for c in chunks),
    ]
    for secret in secrets:
        assert not any(secret in text for text in everything)
    return report


def test_gate3_real_gitleaks_skips_planted_secret(
    client: TestClient, paths: Paths, caplog: pytest.LogCaptureFixture
) -> None:
    if shutil.which("gitleaks") is None:
        pytest.skip("gitleaks is not on PATH (the app image ships it; CI runs the fake scanner)")
    caplog.set_level(logging.DEBUG)
    path, secrets = _planted_folder(paths)
    services_of(client).ingest.tools.scanner = scan_folder
    sid, source = _index_folder(client, path)
    report = _assert_gate3(client, sid, source, secrets, caplog)
    rules = {f["file"]: f["rule_id"] for f in report["findings"]}
    assert rules["app/settings.py"] == "aws-access-token"
    assert rules["deploy/server.pem"] == "private-key"


_AWS_SHAPE = re.compile(r"\b(?:AKIA|ASIA)[A-Z2-7]{16}\b")
_PEM_SHAPE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


def _python_scanner(folder: Path) -> list[Finding]:
    """A tiny stand-in for gitleaks with two of its rules (regex only)."""
    findings: list[Finding] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            for rule, pattern in (("aws-access-token", _AWS_SHAPE), ("private-key", _PEM_SHAPE)):
                if pattern.search(line):
                    findings.append(Finding(path.relative_to(folder).as_posix(), number, rule))
    return findings


def test_gate3_python_scanner_skips_planted_secret(
    client: TestClient, paths: Paths, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    path, secrets = _planted_folder(paths)
    services_of(client).ingest.tools.scanner = _python_scanner
    sid, source = _index_folder(client, path)
    report = _assert_gate3(client, sid, source, secrets, caplog)
    assert sorted((f["file"], f["line"], f["rule_id"]) for f in report["findings"]) == [
        ("app/settings.py", 3, "aws-access-token"),
        ("deploy/server.pem", 1, "private-key"),
    ]
    # A refresh after the secret is removed indexes the file again (and only what changed).
    settings = paths.browse_root / path / "app" / "settings.py"
    settings.write_text('REGION = "eu-north-1"\nDEBUG = False\n')
    assert client.post(f"/api/spaces/{sid}/sources/{source['id']}/refresh").status_code == 200
    run_jobs(services_of(client))
    after = client.get(f"/api/spaces/{sid}/sources/{source['id']}").json()
    assert after["stats"]["skipped_secrets"] == 1
    assert after["stats"]["changed_files"] == 1 and after["stats"]["unchanged_files"] == 2
