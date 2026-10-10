"""Gate 1 follow-ups on the sources API: secrets report (G4), failed sources unticked (G7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Paths
from app.parse.documents import DocumentParser
from app.security.gitleaks import Finding, ScannerMissingError
from tests.conftest import AppFactory
from tests.ingest_helpers import fake_scanner, public_resolver, run_jobs, services_of

FAILED = "This source failed to load; refresh or remove it."


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.web.resolver = public_resolver
    tools.web.renderer = None
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    tools.stt = lambda: None
    return client


@pytest.fixture
def sid(client: TestClient) -> str:
    space_id: str = client.post("/api/spaces", json={"name": "Acme"}).json()["id"]
    return space_id


def _project(paths: Paths) -> None:
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "api.py").write_text("def get(url):\n    return request('get', url)\n")
    (root / "src" / "config.py").write_text('TOKEN = "ghp_fake_value"\nKEY = "sk_fake"\n')


def _source(client: TestClient, sid: str, source_id: str) -> dict[str, Any]:
    sources = client.get(f"/api/spaces/{sid}/sources").json()
    found: dict[str, Any] = next(s for s in sources if s["id"] == source_id)
    return found


def _add_folder(client: TestClient, sid: str) -> str:
    response = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"})
    assert response.status_code == 201, response.text
    source_id: str = response.json()["id"]
    return source_id


# --- G4: secrets report ------------------------------------------------------------------------


def test_secrets_report_lists_file_line_and_rule_only(
    client: TestClient, sid: str, paths: Paths
) -> None:
    _project(paths)
    services_of(client).ingest.tools.scanner = fake_scanner(
        [Finding("src/config.py", 1, "github-pat"), Finding("src/config.py", 2, "generic-api-key")]
    )
    source_id = _add_folder(client, sid)
    run_jobs(services_of(client))
    assert _source(client, sid, source_id)["stats"]["skipped_secrets"] == 1

    response = client.get(f"/api/spaces/{sid}/sources/{source_id}/secrets")
    assert response.status_code == 200
    assert response.json() == {
        "files_skipped": 1,
        "findings": [
            {"file": "src/config.py", "line": 1, "rule_id": "github-pat"},
            {"file": "src/config.py", "line": 2, "rule_id": "generic-api-key"},
        ],
    }
    assert "fake" not in response.text


def test_secrets_report_never_passes_on_other_fields(
    client: TestClient, sid: str, paths: Paths
) -> None:
    _project(paths)
    source_id = _add_folder(client, sid)
    run_jobs(services_of(client))
    report = services_of(client).spaces.get(sid).folder / "sources" / source_id / "secrets.json"
    report.write_text(
        json.dumps(
            {
                "files": ["a.env"],
                "findings": [
                    {"file": "a.env", "line": 3, "rule_id": "aws", "secret": "AKIAFAKE"},
                    {"file": "a.env", "line": "x", "rule_id": "bad"},
                    "junk",
                ],
            }
        )
    )
    response = client.get(f"/api/spaces/{sid}/sources/{source_id}/secrets")
    assert response.json() == {
        "files_skipped": 1,
        "findings": [{"file": "a.env", "line": 3, "rule_id": "aws"}],
    }
    assert "AKIAFAKE" not in response.text


def test_secrets_report_404_without_a_report(client: TestClient, sid: str, paths: Paths) -> None:
    _project(paths)
    source_id = _add_folder(client, sid)  # the fake scanner finds nothing
    run_jobs(services_of(client))
    response = client.get(f"/api/spaces/{sid}/sources/{source_id}/secrets")
    assert response.status_code == 404
    assert response.json() == {"detail": "This source has no secrets report."}
    assert client.get(f"/api/spaces/{sid}/sources/nope/secrets").status_code == 404


# --- G7: failed sources are unticked and not tickable -------------------------------------------


def _fail_folder(client: TestClient, sid: str, paths: Paths) -> str:
    _project(paths)

    def missing(_folder: Path) -> list[Finding]:
        raise ScannerMissingError("gitleaks")

    services_of(client).ingest.tools.scanner = missing
    source_id = _add_folder(client, sid)
    assert _source(client, sid, source_id)["enabled"] is True  # new sources start ticked
    run_jobs(services_of(client))
    return source_id


def test_failed_source_is_stored_unticked_and_cannot_be_ticked(
    client: TestClient, sid: str, paths: Paths
) -> None:
    source_id = _fail_folder(client, sid, paths)
    source = _source(client, sid, source_id)
    assert (source["status"], source["enabled"]) == ("failed", False)
    assert services_of(client).ingest.repo(sid).get(source_id).enabled is False

    url = f"/api/spaces/{sid}/sources"
    patched = client.patch(f"{url}/{source_id}", json={"enabled": True})
    assert (patched.status_code, patched.json()) == (409, {"detail": FAILED})
    put = client.put(f"{url}/enabled", json={"source_ids": [source_id], "enabled": True})
    assert (put.status_code, put.json()) == (409, {"detail": FAILED})
    # Unticking and renaming stay possible.
    assert (
        client.put(f"{url}/enabled", json={"source_ids": [source_id], "enabled": False}).json()[0][
            "enabled"
        ]
        is False
    )
    assert client.patch(f"{url}/{source_id}", json={"title": "Proj"}).json()["title"] == "Proj"
    status = client.get(f"/api/spaces/{sid}/chat/status").json()
    assert (status["enabled_sources"], status["ready_sources"]) == (0, 0)


def test_successful_refresh_ticks_a_failed_source_again(
    client: TestClient, sid: str, paths: Paths
) -> None:
    source_id = _fail_folder(client, sid, paths)
    services_of(client).ingest.tools.scanner = fake_scanner()
    assert client.post(f"/api/spaces/{sid}/sources/{source_id}/refresh").status_code == 200
    run_jobs(services_of(client))
    source = _source(client, sid, source_id)
    assert (source["status"], source["enabled"]) == ("ready", True)


def test_failed_refresh_unticks_again(client: TestClient, sid: str, paths: Paths) -> None:
    source_id = _fail_folder(client, sid, paths)
    client.post(f"/api/spaces/{sid}/sources/{source_id}/refresh")
    run_jobs(services_of(client))
    assert _source(client, sid, source_id)["enabled"] is False


def test_refresh_keeps_a_users_untick_on_a_ready_source(
    client: TestClient, sid: str, paths: Paths
) -> None:
    _project(paths)
    source_id = _add_folder(client, sid)
    run_jobs(services_of(client))
    client.patch(f"/api/spaces/{sid}/sources/{source_id}", json={"enabled": False})
    client.post(f"/api/spaces/{sid}/sources/{source_id}/refresh")
    run_jobs(services_of(client))
    source = _source(client, sid, source_id)
    assert (source["status"], source["enabled"]) == ("ready", False)


def test_cancelled_and_rejected_sources_are_not_tickable(client: TestClient, sid: str) -> None:
    queued = client.post(f"/api/spaces/{sid}/sources/paste", json={"text": "Some notes."}).json()
    client.post(f"/api/jobs/{queued['job_id']}/cancel")
    source = _source(client, sid, queued["id"])
    assert (source["status"], source["enabled"]) == ("failed", False)
    response = client.patch(f"/api/spaces/{sid}/sources/{queued['id']}", json={"enabled": True})
    assert response.status_code == 409

    upload = client.post(
        f"/api/spaces/{sid}/sources/upload", files={"files": ("x.exe", b"MZ", "application/x")}
    ).json()[0]
    assert (upload["status"], upload["enabled"]) == ("failed", False)
