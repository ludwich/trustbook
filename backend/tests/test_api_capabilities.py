from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.config import Paths
from tests.fakes import Router, chat_reply, write_models_lock

LLM = "http://llm.example:8080/v1"


def test_list_and_get(client: TestClient) -> None:
    caps = client.get("/api/capabilities").json()
    assert [c["id"] for c in caps] == [
        "chat",
        "gate",
        "embeddings",
        "reranker",
        "grounding",
        "stt",
        "web_search",
        "tts",
    ]
    chat = client.get("/api/capabilities/chat").json()
    assert set(chat) == {
        "id",
        "label",
        "description",
        "kind",
        "required",
        "hidden",
        "contract",
        "fields",
        "presets",
        "providers",
        "config",
        "locality",
        "status",
        "status_detail",
        "last_test",
        "bundled",
    }
    assert set(chat["config"]) == {
        "enabled",
        "preset",
        "base_url",
        "model",
        "api_version",
        "api_key_set",
        "api_key_hint",
        "use_chat",
        "provider",
        "mode",
    }
    assert set(chat["presets"][0]) == {"id", "label", "adapter", "base_url", "needs_api_key"}
    assert "mock" in [p["id"] for p in chat["presets"]]
    assert client.get("/api/capabilities/nope").status_code == 404


def test_bundled_status_with_models(make_client: object, paths: Paths) -> None:
    write_models_lock(paths.models_dir)
    client = make_client()  # type: ignore[operator]
    embeddings = client.get("/api/capabilities/embeddings").json()
    assert embeddings["status"] == "ready"
    assert embeddings["bundled"] == {
        "model_id": "google/embeddinggemma-2",
        "revision": "rev-embeddings",
        "license": "Apache-2.0",
        "present": True,
        "fallback": False,
    }


def test_put_never_returns_key(client: TestClient, paths: Paths) -> None:
    body = {"preset": "openai", "model": "gpt-x", "api_key": "sk-very-secret-a1b2"}
    response = client.put("/api/capabilities/chat", json=body)
    assert response.status_code == 200
    assert "sk-very-secret" not in response.text
    data = response.json()
    assert data["config"]["api_key_hint"] == "…a1b2"
    assert data["config"]["base_url"] == "https://api.openai.com/v1"
    assert (data["status"], data["locality"]) == ("ready", "remote")
    assert "sk-very-secret" not in client.get("/api/capabilities").text
    saved = json.loads(paths.settings_file.read_text())
    assert saved["capabilities"]["chat"]["api_key"] == "sk-very-secret-a1b2"


def test_put_validation(client: TestClient) -> None:
    bad_url = client.put("/api/capabilities/chat", json={"preset": "custom", "base_url": "x"})
    assert bad_url.status_code == 422
    assert bad_url.json()["detail"].startswith("Enter a full web address")
    assert client.put("/api/capabilities/chat", json={"unknown": 1}).status_code == 422
    assert client.put("/api/capabilities/chat", json={"provider": "brave"}).status_code == 422
    assert client.put("/api/capabilities/nope", json={}).status_code == 404


def test_test_endpoint_saved_and_draft(client: TestClient, router: Router) -> None:
    router.json("POST", LLM, chat_reply("OK"))
    draft = {"preset": "custom", "base_url": LLM, "model": "m"}
    result = client.post("/api/capabilities/chat/test", json=draft).json()
    assert result["ok"] is True
    assert set(result) == {"ok", "message", "latency_ms", "tested_at"}
    assert result["tested_at"].endswith("Z")
    assert client.get("/api/capabilities/chat").json()["last_test"] is None
    client.put("/api/capabilities/chat", json=draft)
    saved = client.post("/api/capabilities/chat/test").json()
    assert saved["ok"]
    assert client.get("/api/capabilities/chat").json()["last_test"] == saved
    empty_body = client.post("/api/capabilities/chat/test", json={}).json()
    assert client.get("/api/capabilities/chat").json()["last_test"] == empty_body


def test_test_failure_sets_error(client: TestClient, router: Router) -> None:
    router.add("POST", LLM, lambda _r: httpx.Response(401, json={"error": "bad"}))
    client.put(
        "/api/capabilities/chat",
        json={"preset": "custom", "base_url": LLM, "model": "m", "api_key": "k" * 20},
    )
    result = client.post("/api/capabilities/chat/test").json()
    assert result == {**result, "ok": False, "message": "The API key was rejected (401)."}
    chat = client.get("/api/capabilities/chat").json()
    assert chat["status"] == "error"
    assert chat["status_detail"] == "Last test failed: The API key was rejected (401)."
    log = client.get("/api/request-log").json()
    assert "k" * 20 not in json.dumps(log)


def test_test_invalid_draft(client: TestClient) -> None:
    response = client.post("/api/capabilities/chat/test", json={"preset": "nope"})
    assert response.status_code == 422
    assert client.post("/api/capabilities/nope/test").status_code == 404


def test_models_endpoint(client: TestClient, router: Router) -> None:
    router.json("GET", f"{LLM}/models", {"data": [{"id": "b"}, {"id": "a"}]})
    draft = {"preset": "custom", "base_url": LLM, "model": "m"}
    assert client.post("/api/capabilities/chat/models", json=draft).json() == {"models": ["a", "b"]}
    assert client.post("/api/capabilities/chat/models", json={"preset": "mock"}).json() == {
        "models": ["mock"]
    }
    azure = {
        "preset": "azure_openai",
        "base_url": "https://r.openai.azure.com",
        "model": "d",
        "api_version": "v",
        "api_key": "k" * 16,
    }
    assert client.post("/api/capabilities/chat/models", json=azure).json() == {"models": []}


def test_models_errors(client: TestClient, router: Router) -> None:
    router.add("GET", LLM, lambda r: httpx.Response(500, json={"error": "down"}))
    draft = {"preset": "custom", "base_url": LLM, "model": "m"}
    failed = client.post("/api/capabilities/chat/models", json=draft)
    assert failed.status_code == 502
    assert failed.json()["detail"].startswith("The endpoint had an internal error (500)")
    incomplete = client.post("/api/capabilities/chat/models")
    assert incomplete.status_code == 400
    assert incomplete.json() == {"detail": "Choose a provider."}
    assert client.post("/api/capabilities/embeddings/models").status_code == 400
    assert client.post("/api/capabilities/chat/models", json={"base_url": "x"}).status_code == 422


def test_search_and_stt_tests(client: TestClient, router: Router) -> None:
    router.json("GET", "http://searxng:8080/search", {"results": []})
    assert client.post("/api/capabilities/web_search/test").json()["ok"]
    assert client.post("/api/capabilities/stt/test").json()["message"] == "Model works."
    assert client.post("/api/capabilities/embeddings/test").json()["ok"]
