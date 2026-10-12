from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.capabilities.bundled import ProviderRegistry
from app.chat.overview import (
    OVERVIEW_KIND,
    estimate_prompt_tokens,
    sources_signature,
    spread_across_sources,
)
from app.chat.prompt import OVERVIEW_SCHEMA
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.memory import InMemoryIndexRegistry, InMemorySpaceIndex
from app.retrieval.service import RetrievedChunk
from app.services import Services
from tests.chat_helpers import (
    KeywordEmbedder,
    OverlapReranker,
    ScriptedChat,
    add_chunk,
    add_source,
    set_source,
)
from tests.conftest import AppFactory, wait_for
from tests.fakes import Router, chat_reply


def rc(cid: str, source: str, score: float, tokens: int = 100) -> RetrievedChunk:
    return RetrievedChunk(cid, source, source, "t", {}, tokens, score)


def test_spread_across_sources_round_robins_best_first() -> None:
    ranked = [rc("a1", "A", 0.9), rc("a2", "A", 0.8), rc("a3", "A", 0.7), rc("b1", "B", 0.6)]
    ranked += [rc("c1", "C", 0.5), rc("b2", "B", 0.4)]
    assert [c.chunk_id for c in spread_across_sources(ranked)] == [
        "a1",
        "b1",
        "c1",
        "a2",
        "b2",
        "a3",
    ]
    assert [c.chunk_id for c in spread_across_sources(ranked, max_chunks=2)] == ["a1", "b1"]
    big = [rc("x", "A", 0.9, 5000), rc("y", "B", 0.8, 2000), rc("z", "B", 0.1, 900)]
    assert [c.chunk_id for c in spread_across_sources(big)] == ["x", "z"]


def test_signature_and_estimate(tmp_path: Path) -> None:
    db = tmp_path / "space.db"
    migrate(db, "space")
    engine = get_engine(db)
    assert sources_signature(engine) is None and estimate_prompt_tokens(engine) is None
    index = InMemorySpaceIndex()
    source = add_source(engine, "Doc", snapshot_at="2026-10-01T00:00:00.000Z")
    add_chunk(engine, index, source, "x" * 400, token_count=100)
    first = sources_signature(engine)
    tokens = estimate_prompt_tokens(engine)
    assert first and tokens is not None and 100 < tokens < 1000
    set_source(engine, source, snapshot_at="2026-10-02T00:00:00.000Z")
    assert sources_signature(engine) != first
    add_chunk(engine, index, source, "y" * 400, ordinal=1, token_count=10_000)
    capped = estimate_prompt_tokens(engine)
    assert capped is not None and capped < 6000 + 1000  # the context budget caps it


@dataclass
class Env:
    client: TestClient
    space_id: str
    engine: Engine
    index: InMemorySpaceIndex
    services: Services

    def url(self, path: str = "") -> str:
        return f"/api/spaces/{self.space_id}/overview{path}"

    def overview(self) -> dict[str, Any]:
        response = self.client.get(self.url())
        assert response.status_code == 200
        data: dict[str, Any] = response.json()
        return data

    def wait_status(self, *statuses: str) -> dict[str, Any]:
        wait_for(lambda: self.overview()["status"] in statuses)
        return self.overview()


@pytest.fixture
def env(make_client: AppFactory, fake_providers: ProviderRegistry) -> Env:
    fake_providers.override(embedder=KeywordEmbedder(), reranker=OverlapReranker())
    indexes = InMemoryIndexRegistry()
    client = make_client(indexes=indexes, start_workers=True)
    client.put("/api/capabilities/chat", json={"preset": "mock"})
    space = client.post("/api/spaces", json={"name": "Acme"}).json()
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    record = services.spaces.get(space["id"])
    return Env(
        client, space["id"], get_engine(record.db_path), indexes.for_space(space["id"]), services
    )


def fill(env: Env) -> tuple[str, str]:
    a = add_source(env.engine, "Requests guide")
    add_chunk(env.engine, env.index, a, "Requests is an HTTP library for Python.")
    add_chunk(env.engine, env.index, a, "Sessions keep cookies between calls.", ordinal=1)
    b = add_source(env.engine, "Changelog")
    add_chunk(env.engine, env.index, b, "Version 2.0 added SOCKS proxy support.")
    return a, b


def test_initial_overview(env: Env) -> None:
    assert env.overview() == {
        "enabled": True,
        "status": "none",
        "sentences": [],
        "questions": [],
        "generated_at": None,
        "stale": False,
        "estimate": None,
    }


def test_first_ready_source_generates_the_overview(env: Env) -> None:
    """Local chat model (mock): the hook queues the job without asking."""
    a, b = fill(env)
    for hook in env.services.on_source_ready:
        hook(env.space_id, a)
    data = env.wait_status("ready", "failed")
    assert data["status"] == "ready", data
    assert len(data["questions"]) == 3
    assert data["sentences"] and all(s["citations"] for s in data["sentences"])
    cited_sources = {c["source_id"] for s in data["sentences"] for c in s["citations"]}
    assert cited_sources == {a, b}  # every source is represented
    numbers = [c["n"] for s in data["sentences"] for c in s["citations"]]
    assert numbers == sorted(numbers) and numbers[0] == 1
    assert data["generated_at"] and data["stale"] is False
    assert data["estimate"]["remote"] is False and data["estimate"]["tokens"] > 0
    jobs = env.client.get("/api/jobs").json()["jobs"]
    assert [(j["kind"], j["status"], j["space_id"]) for j in jobs] == [
        (OVERVIEW_KIND, "done", env.space_id)
    ]

    # Later sources only mark it stale; nothing is regenerated automatically.
    c = add_source(env.engine, "Notes")
    add_chunk(env.engine, env.index, c, "Notes about retries.")
    for hook in env.services.on_source_ready:
        hook(env.space_id, c)
    assert env.overview()["stale"] is True
    assert len(env.client.get("/api/jobs").json()["jobs"]) == 1

    response = env.client.post(env.url("/refresh"))
    assert response.status_code == 202
    assert response.json()["kind"] == OVERVIEW_KIND
    wait_for(lambda: env.overview()["stale"] is False)
    assert env.overview()["status"] == "ready"


def test_hook_does_nothing_when_disabled_or_chat_missing(env: Env) -> None:
    a, _ = fill(env)
    assert env.client.put(env.url(), json={"enabled": False}).json()["enabled"] is False
    env.services.overview.on_source_ready(env.space_id, a)
    env.services.overview.on_source_ready("no-such-space", a)
    assert env.overview()["status"] == "none"
    assert env.client.post(env.url("/refresh")).status_code == 409

    env.client.put(env.url(), json={"enabled": True})
    env.client.put("/api/capabilities/chat", json={"preset": "custom"})  # incomplete
    env.services.overview.on_source_ready(env.space_id, a)
    assert env.overview()["status"] == "none"
    response = env.client.post(env.url("/refresh"))
    assert response.status_code == 409
    assert response.json()["detail"] == "Set up the chat model in Settings first."
    assert env.client.get("/api/jobs").json()["jobs"] == []


def test_remote_chat_is_never_started_automatically(env: Env, router: Router) -> None:
    a, _ = fill(env)
    env.client.put(
        "/api/capabilities/chat",
        json={"preset": "custom", "base_url": "http://llm.example/v1", "model": "m"},
    )
    for hook in env.services.on_source_ready:
        hook(env.space_id, a)
    data = env.overview()
    assert (data["status"], data["sentences"], data["generated_at"]) == ("none", [], None)
    assert data["estimate"]["remote"] is True and data["estimate"]["tokens"] > 0
    assert env.client.get("/api/jobs").json()["jobs"] == []
    assert router.requests == []  # nothing left the machine


def test_refresh_from_none_and_without_sources(env: Env) -> None:
    response = env.client.post(env.url("/refresh"))
    assert response.status_code == 409
    assert "source" in response.json()["detail"]
    fill(env)
    assert env.client.post(env.url("/refresh")).status_code == 202
    assert env.wait_status("ready", "failed")["status"] == "ready"
    assert env.client.put(env.url(), json={"enabled": "maybe"}).status_code == 422
    assert env.client.get("/api/spaces/nope/overview").status_code == 404


def test_unusable_overview_fails_and_keeps_grounding(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(env)
    uncited = json.dumps(
        {"sentences": [{"text": "Outside fact.", "chunk_ids": []}], "questions": ["a", "b", "c"]}
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat(uncited))
    env.client.post(env.url("/refresh"))
    data = env.wait_status("failed", "ready")
    assert data["status"] == "failed" and data["sentences"] == []
    job = env.client.get("/api/jobs").json()["jobs"][0]
    assert job["status"] == "failed"
    assert job["error"] == "The chat model's overview did not cite your sources."

    chat = ScriptedChat("not json", '{"sentences": [], "questions": ["only one"]}')
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    env.client.post(env.url("/refresh"))
    wait_for(lambda: len(chat.calls) == 2 and env.overview()["status"] == "failed")
    assert chat.calls[0]["schema"] == OVERVIEW_SCHEMA


def test_remote_estimate_and_request_log(env: Env, router: Router) -> None:
    fill(env)

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["temperature"] == 0.2
        data = {
            # Cites every chunk, so the one it quotes passes verification whatever the order.
            "sentences": [
                {"text": "Requests is an HTTP library.", "chunk_ids": ["c1", "c2", "c3"]}
            ],
            "questions": ["What is Requests?", "What do sessions keep?", "What changed in 2.0?"],
        }
        return httpx.Response(200, json=chat_reply(json.dumps(data)))

    router.add("POST", "http://llm.example/v1/chat/completions", reply)
    env.client.put(
        "/api/capabilities/chat",
        json={"preset": "custom", "base_url": "http://llm.example/v1", "model": "m"},
    )
    before = env.overview()
    assert before["estimate"]["remote"] is True and before["estimate"]["tokens"] > 0
    env.client.post(env.url("/refresh"))
    data = env.wait_status("ready", "failed")
    assert data["questions"] == [
        "What is Requests?",
        "What do sessions keep?",
        "What changed in 2.0?",
    ]
    log = env.client.get("/api/request-log").json()
    assert (log[0]["capability"], log[0]["space_id"]) == ("chat", env.space_id)
    assert log[0]["space_name"] == "Acme"


def test_disabling_cancels_a_waiting_job(make_client: AppFactory, fake_providers: Any) -> None:
    fake_providers.override(embedder=KeywordEmbedder(), reranker=OverlapReranker())
    indexes = InMemoryIndexRegistry()
    client = make_client(indexes=indexes)  # no workers: the job waits
    client.put("/api/capabilities/chat", json={"preset": "mock"})
    space = client.post("/api/spaces", json={"name": "Acme"}).json()
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    engine = get_engine(services.spaces.get(space["id"]).db_path)
    add_chunk(engine, indexes.for_space(space["id"]), add_source(engine, "Doc"), "Some text.")
    url = f"/api/spaces/{space['id']}/overview"
    job = client.post(f"{url}/refresh").json()
    assert client.get(url).json()["status"] == "pending"
    assert client.post(f"{url}/refresh").json()["id"] == job["id"]  # no duplicate job
    assert client.put(url, json={"enabled": False}).json()["status"] == "none"
    assert client.get("/api/jobs").json()["jobs"][0]["status"] == "cancelled"


def overview_reply(*sentences: tuple[str, list[str]]) -> str:
    return json.dumps(
        {
            "sentences": [{"text": t, "chunk_ids": ids} for t, ids in sentences],
            "questions": ["What is Requests?", "What do sessions keep?", "What changed in 2.0?"],
        }
    )


ALL_LABELS = ["c1", "c2", "c3"]


def test_overview_leaves_out_unsupported_sentences(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(env)
    reply = overview_reply(
        ("Requests is an HTTP library for Python.", ALL_LABELS),
        ("It was written on the moon.", ALL_LABELS),
        ("Version 2.0 added SOCKS proxy support.", ALL_LABELS),
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat(reply))
    env.client.post(env.url("/refresh"))
    data = env.wait_status("ready", "failed")
    assert data["status"] == "ready"
    assert [s["text"] for s in data["sentences"]] == [
        "Requests is an HTTP library for Python.",
        "Version 2.0 added SOCKS proxy support.",
    ]
    # Only the chunk that supports each sentence on its own stays cited (D-052), and citation
    # numbers count only the kept citations, in order of first use.
    first = data["sentences"][0]["citations"]
    assert [c["n"] for c in first] == [1]
    assert all(len(s["citations"]) >= 1 for s in data["sentences"])


def test_overview_with_no_supported_sentence_fails(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(env)
    reply = overview_reply(("It was written on the moon.", ALL_LABELS))
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat(reply))
    env.client.post(env.url("/refresh"))
    data = env.wait_status("failed", "ready")
    assert data["status"] == "failed" and data["sentences"] == []
    job = env.client.get("/api/jobs").json()["jobs"][0]
    assert job["error"] == (
        "None of the overview's sentences were supported by your sources, so it was not saved. "
        "Try again, or add more sources."
    )


def test_overview_with_grounding_off_still_runs_code_checks(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    fill(env)
    env.client.put("/api/capabilities/grounding", json={"enabled": False})
    reply = overview_reply(
        ("It was written on the moon.", ALL_LABELS),  # not checked: the checker is off
        ("It defines `moon_landing`.", ALL_LABELS),  # layer 5 still fails it
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat(reply))
    env.client.post(env.url("/refresh"))
    data = env.wait_status("ready", "failed")
    assert [s["text"] for s in data["sentences"]] == ["It was written on the moon."]
