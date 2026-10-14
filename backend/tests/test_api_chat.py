from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.chat import sse
from app.capabilities.adapters.base import AdapterError
from app.capabilities.bundled import ProviderRegistry
from app.chat.repository import ChatStore
from app.db.engine import get_engine
from app.index.memory import InMemoryIndexRegistry, InMemorySpaceIndex
from app.services import Services
from tests.chat_helpers import (
    KeywordEmbedder,
    OverlapReranker,
    ScriptedChat,
    add_chunk,
    add_source,
    answer_json,
    parse_sse,
)
from tests.conftest import AppFactory
from tests.fakes import Router, chat_reply

GUIDE = "Requests is an HTTP library for Python. Proxies are configured with the proxies argument."


@dataclass
class Env:
    client: TestClient
    space_id: str
    engine: Engine
    index: InMemorySpaceIndex
    services: Services

    def url(self, path: str = "") -> str:
        return f"/api/spaces/{self.space_id}{path}"

    def conversation(self) -> str:
        response = self.client.post(self.url("/conversations"), json={})
        assert response.status_code == 201
        return str(response.json()["id"])

    def ask(self, cid: str, question: str) -> list[tuple[str, Any]]:
        response = self.client.post(
            self.url(f"/conversations/{cid}/messages"), json={"content": question}
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        return parse_sse(response.text)


@pytest.fixture
def env(make_client: AppFactory, fake_providers: ProviderRegistry) -> Env:
    fake_providers.override(embedder=KeywordEmbedder(), reranker=OverlapReranker())
    indexes = InMemoryIndexRegistry()
    client = make_client(indexes=indexes)
    assert client.put("/api/capabilities/chat", json={"preset": "mock"}).status_code == 200
    space = client.post("/api/spaces", json={"name": "Acme"}).json()
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    record = services.spaces.get(space["id"])
    return Env(
        client, space["id"], get_engine(record.db_path), indexes.for_space(space["id"]), services
    )


def test_chat_status_counts(env: Env, make_client: AppFactory) -> None:
    add_source(env.engine, "Ready on")
    add_source(env.engine, "Ready off", enabled=False)
    add_source(env.engine, "Busy", status="reading")
    status = env.client.get(env.url("/chat/status")).json()
    assert status == {
        "enabled_sources": 1,
        "ready_sources": 2,
        "total_sources": 3,
        "chat_ready": True,
    }
    assert env.client.get("/api/spaces/nope/chat/status").status_code == 404


def test_conversation_crud(env: Env) -> None:
    assert env.client.get(env.url("/conversations")).json() == []
    first = env.client.post(env.url("/conversations")).json()
    assert set(first) == {"id", "title", "created_at", "updated_at"} and first["title"] == ""
    named = env.client.post(env.url("/conversations"), json={"title": " Plan "}).json()
    assert named["title"] == "Plan"
    listed = env.client.get(env.url("/conversations")).json()
    assert {c["id"] for c in listed} == {first["id"], named["id"]}
    assert env.client.get(env.url(f"/conversations/{first['id']}/messages")).json() == []
    assert env.client.delete(env.url(f"/conversations/{first['id']}")).status_code == 204
    assert env.client.delete(env.url(f"/conversations/{first['id']}")).status_code == 404
    assert env.client.get(env.url(f"/conversations/{first['id']}/messages")).status_code == 404


def test_grounded_answer_streams_and_is_persisted(env: Env) -> None:
    source = add_source(env.engine, "Requests guide")
    chunk_id = add_chunk(
        env.engine, env.index, source, GUIDE, locator={"heading": "Proxies", "offset": 0}
    )
    add_chunk(env.engine, env.index, source, "Sessions keep cookies.", ordinal=1)
    cid = env.conversation()
    question = "How are proxies configured? " + "Please be precise about the details. " * 3
    events = env.ask(cid, question)

    names = [name for name, _ in events]
    assert names == ["status", "status", "status", "sentence", "done"]
    assert [d["stage"] for n, d in events if n == "status"] == [
        "retrieving",
        "generating",
        "checking",
    ]
    sentence = events[3][1]
    citation = {
        "n": 1,
        "chunk_id": chunk_id,
        "source_id": source,
        "source_title": "Requests guide",
        "locator": {"heading": "Proxies", "offset": 0},
    }
    assert sentence == {
        "index": 0,
        "text": "Proxies are configured with the proxies argument.",
        "citations": [citation],
    }
    message = events[-1][1]["message"]
    assert message["role"] == "assistant" and message["badge"] == "verified"
    assert message["content"] == {
        "sentences": [{"text": sentence["text"], "citations": [citation]}],
    }
    assert message["removed_sentences"] == []
    assert message["refusal_reason"] is None  # answers carry no refusal reason (D-118)
    diagnostics = message["diagnostics"]
    assert diagnostics["sentence_scores"] == [1.0] and diagnostics["regenerated"] is False
    assert diagnostics["top_rerank"] > 0
    assert message["sources_used"] == [{"source_id": source, "title": "Requests guide"}]

    history = env.client.get(env.url(f"/conversations/{cid}/messages")).json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"]["text"] == question.strip()
    assert history[1] == message
    rows = ChatStore(env.engine).citation_rows(message["id"])
    assert [(r.sentence_index, r.chunk_id, r.verifier_score) for r in rows] == [(0, chunk_id, 1.0)]

    title = env.client.get(env.url("/conversations")).json()[0]["title"]
    assert title.startswith("How are proxies configured?") and len(title) <= 60
    env.ask(cid, "What do sessions keep?")
    assert env.client.get(env.url("/conversations")).json()[0]["title"] == title


def test_low_score_question_is_refused_before_generating(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_chunk(env.engine, env.index, add_source(env.engine, "Guide"), GUIDE)
    built: list[int] = []
    monkeypatch.setattr(env.services, "chat_adapter", lambda: built.append(1))
    cid = env.conversation()
    events = env.ask(cid, "What is the capital of France?")
    assert [n for n, _ in events] == ["status", "refused", "done"]
    refused = events[1][1]
    assert refused == {
        "reason": "not_in_sources",
        "message": "Not in your sources",
        "suggestion": "Add sources that cover “the capital of France”, then ask again.",
    }
    message = events[2][1]["message"]
    assert message["badge"] == "refused"
    assert message["refusal_reason"] == "not_in_sources"  # stored with the message (D-118)
    assert message["content"]["text"] == "Not in your sources"
    assert message["content"]["suggestion"] == refused["suggestion"]
    assert message["content"]["sentences"] == [] and message["sources_used"] == []
    assert built == []  # no model was even built
    assert env.client.get(env.url(f"/conversations/{cid}/messages")).json()[1] == message


LINE_SEPARATORS = "\u2028\u2029\u0085"


def test_sse_events_are_ascii_so_unicode_line_separators_stay_in_one_event() -> None:
    text = "Line one\u2028line two\u2029para\u0085next “quoted”."
    raw = sse("sentence", {"index": 0, "text": text, "citations": []})
    assert raw.isascii()
    # httpx's iter_lines() splits like str.splitlines(): the event must still be whole.
    assert parse_sse("\n".join(raw.splitlines()) + "\n\n") == [
        ("sentence", {"index": 0, "text": text, "citations": []})
    ]


def test_streamed_sentence_with_a_line_separator_arrives_in_one_event(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    title = "Requests\u2028guide\u2029v2\u0085(“draft”)"
    text = "Proxies are configured with the proxies argument."
    add_chunk(env.engine, env.index, add_source(env.engine, title), GUIDE)
    chat = ScriptedChat(answer_json((text, ["c1"])))
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    response = env.client.post(
        env.url(f"/conversations/{env.conversation()}/messages"),
        json={"content": "How are proxies configured?"},
    )
    assert response.status_code == 200
    body = response.text
    assert body.isascii() and not any(c in body for c in LINE_SEPARATORS)
    # httpx's iter_lines() splits like str.splitlines(): every event must still parse whole.
    events = parse_sse("\n".join(body.splitlines()) + "\n\n")
    assert [n for n, _ in events][-2:] == ["sentence", "done"]
    sentence = events[-2][1]
    assert sentence["text"] == text
    assert sentence["citations"][0]["source_title"] == title
    assert events[-1][1]["message"]["sources_used"][0]["title"] == title


def test_no_ticked_ready_sources_is_refused(env: Env) -> None:
    add_chunk(env.engine, env.index, add_source(env.engine, "Off", enabled=False), GUIDE)
    events = env.ask(env.conversation(), "How are proxies configured?")
    assert [n for n, _ in events] == ["status", "refused", "done"]
    assert events[1][1]["reason"] == "no_sources"
    assert events[1][1]["message"] == "Not in your sources"
    assert events[1][1]["suggestion"] == "Add or tick at least one ready source, then ask again."


def test_model_with_no_supported_sentences_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_chunk(env.engine, env.index, add_source(env.engine, "Guide"), GUIDE)
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat('{"sentences": []}'))
    events = env.ask(env.conversation(), "How are proxies configured?")
    assert [n for n, _ in events][-2:] == ["refused", "done"]
    assert events[-2][1]["reason"] == "not_in_sources"

    chat = ScriptedChat(answer_json(("From memory.", [])))
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    events = env.ask(env.conversation(), "How are proxies configured?")
    assert events[-2][1]["reason"] == "invalid_output"
    assert events[-1][1]["message"]["badge"] == "refused"

    chat = ScriptedChat("garbage", "still garbage")
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    events = env.ask(env.conversation(), "How are proxies configured?")
    assert events[-2][1]["reason"] == "invalid_output" and len(chat.calls) == 2
    assert events[-1][1]["message"]["refusal_reason"] == "invalid_output"


def test_refusal_reason_survives_a_reload_and_old_refusals_still_read(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-118 (G3-2): the UI after a reload and the CLI tell a verification refusal from a
    not-in-sources one by ``refusal_reason``; refusals stored before it have none."""
    add_chunk(env.engine, env.index, add_source(env.engine, "Guide"), GUIDE)
    cid = env.conversation()
    chat = ScriptedChat(
        answer_json(("Made-up fact.", ["c1"])), answer_json(("Another invention.", ["c1"]))
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    unsupported = env.ask(cid, "How are proxies configured?")[-1][1]["message"]
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat('{"sentences": []}'))
    missing = env.ask(cid, "How are proxies configured?")[-1][1]["message"]
    assert (unsupported["refusal_reason"], missing["refusal_reason"]) == (
        "unsupported",
        "not_in_sources",
    )
    assert unsupported["content"]["text"] == "Couldn't verify an answer"
    assert missing["content"]["text"] == "Not in your sources"

    # A refusal stored before migration 007: no reason, its stored title and suggestion as before.
    with env.engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE messages SET refusal_reason = NULL WHERE id = ?", (missing["id"],)
        )
    history = env.client.get(env.url(f"/conversations/{cid}/messages")).json()
    assert [m["refusal_reason"] for m in history] == [None, "unsupported", None, None]
    assert history[1] == unsupported
    assert history[3]["badge"] == "refused"
    assert history[3]["content"] == missing["content"]


def test_endpoint_failure_becomes_an_error_event(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    add_chunk(env.engine, env.index, add_source(env.engine, "Guide"), GUIDE)

    class Down(ScriptedChat):
        def chat(self, *_a: object, **_k: object) -> Any:
            raise AdapterError("unreachable", "Cannot reach llm.local.")

    monkeypatch.setattr(env.services, "chat_adapter", Down)
    cid = env.conversation()
    events = env.ask(cid, "How are proxies configured?")
    assert events[-1] == ("error", {"message": "Cannot reach llm.local."})
    assert [n for n, _ in events] == ["status", "status", "error"]
    history = env.client.get(env.url(f"/conversations/{cid}/messages")).json()
    assert [m["role"] for m in history] == ["user"]


def test_unexpected_failure_is_a_plain_error(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    add_chunk(env.engine, env.index, add_source(env.engine, "Guide"), GUIDE)

    def boom() -> Any:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(env.services, "chat_adapter", boom)
    events = env.ask(env.conversation(), "How are proxies configured?")
    assert events[-1] == ("error", {"message": "Something went wrong while answering. Try again."})


def test_message_validation_and_readiness(env: Env, make_client: AppFactory) -> None:
    cid = env.conversation()
    url = env.url(f"/conversations/{cid}/messages")
    assert env.client.post(url, json={"content": "   "}).status_code == 422
    assert env.client.post(url, json={"content": "x" * 4001}).status_code == 422
    assert env.client.post(url, json={}).status_code == 422
    missing = env.client.post(env.url("/conversations/nope/messages"), json={"content": "Hi?"})
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Conversation not found."}

    env.client.put("/api/capabilities/chat", json={"preset": "custom"})  # no address yet
    response = env.client.post(url, json={"content": "Hi?"})
    assert response.status_code == 409
    assert response.json() == {"detail": "Set up the chat model in Settings first."}
    assert env.client.get(env.url("/chat/status")).json()["chat_ready"] is False
    assert env.client.get(url).json() == []  # nothing stored


def test_remote_requests_are_logged_under_the_space(env: Env, router: Router) -> None:
    add_chunk(env.engine, env.index, add_source(env.engine, "Guide"), GUIDE)

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["temperature"] == 0.2
        assert body["response_format"]["type"] == "json_schema"
        assert "[c1] Source: Guide" in body["messages"][-1]["content"]
        return httpx.Response(200, json=chat_reply(answer_json(("Use proxies.", ["c1"]))))

    router.add("POST", "http://llm.example/v1/chat/completions", reply)
    env.client.put(
        "/api/capabilities/chat",
        json={"preset": "custom", "base_url": "http://llm.example/v1", "model": "m"},
    )
    events = env.ask(env.conversation(), "How are proxies configured?")
    assert [n for n, _ in events][-2:] == ["sentence", "done"]
    log = env.client.get("/api/request-log").json()
    assert log[0]["capability"] == "chat"
    assert (log[0]["space_id"], log[0]["space_name"]) == (env.space_id, "Acme")
    assert log[0]["endpoint_host"] == "llm.example"
