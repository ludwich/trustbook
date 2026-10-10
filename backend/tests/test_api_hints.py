from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Paths


def test_dismissing_a_hint_is_remembered(client: TestClient, paths: Paths) -> None:
    assert client.get("/api/settings").json()["hints_dismissed"] == []
    response = client.put("/api/settings", json={"dismiss_hint": "chat_grounding"})
    assert response.status_code == 200
    assert response.json()["hints_dismissed"] == ["chat_grounding"]
    again = client.put("/api/settings", json={"dismiss_hint": "chat_grounding"}).json()
    assert again["hints_dismissed"] == ["chat_grounding"]
    assert again["power"] == "normal"
    saved = json.loads(paths.settings_file.read_text())
    assert saved["hints_dismissed"] == ["chat_grounding"]
    assert client.get("/api/settings").json()["hints_dismissed"] == ["chat_grounding"]


def test_hint_ids_are_validated(client: TestClient) -> None:
    for bad in ("", "Has Spaces", "x" * 65, "../etc"):
        assert client.put("/api/settings", json={"dismiss_hint": bad}).status_code == 422
    assert client.put("/api/settings", json={"power": "low"}).json()["hints_dismissed"] == []
