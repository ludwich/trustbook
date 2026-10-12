"""Ideation routes with the mock chat model: every route, the SSE order, the 409s, resume after a
restart, "Finish now" mid-session and during a turn, export, delete, the estimate and the
request log."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.capabilities.adapters.base import ChatMessage, ChatResult
from app.capabilities.adapters.mock import IDEA_SUGGESTION, UNSUPPORTED_SENTENCE, MockChatAdapter
from app.capabilities.bundled import ProviderRegistry
from app.db.engine import get_engine
from app.ideation.marker import BRIEF_MARKER
from app.ideation.repository import IdeationStore
from app.ideation.service import TURN_LOCKS, UNUSABLE_REPLY
from app.index.memory import InMemoryIndexRegistry, InMemorySpaceIndex
from app.services import Services
from tests.chat_helpers import (
    KeywordEmbedder,
    OverlapReranker,
    ScriptedChat,
    add_chunk,
    add_source,
    parse_sse,
)
from tests.conftest import AppFactory
from tests.fakes import Router, chat_reply

PUFFINS = (
    "Gull Island has a colony of puffins that nest on the north cliffs from May to August. "
    "The ferry to Gull Island leaves the harbour twice a day in summer."
)
HOSTEL = "The island hostel has twelve beds. Guided walks start at the lighthouse every morning."
IDEA = "Guided puffin walks on Gull Island for birdwatchers"


@dataclass
class Env:
    client: TestClient
    space_id: str
    engine: Engine
    index: InMemorySpaceIndex
    services: Services

    def url(self, path: str = "") -> str:
        return f"/api/spaces/{self.space_id}/ideation{path}"

    def sources(self) -> None:
        source = add_source(self.engine, "Gull Island guide")
        add_chunk(self.engine, self.index, source, PUFFINS, locator={"pages": [1]})
        add_chunk(self.engine, self.index, source, HOSTEL, ordinal=1, locator={"pages": [2]})

    def create(self, idea: str = IDEA) -> dict[str, Any]:
        response = self.client.post(self.url("/sessions"), json={"idea": idea})
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    def stream(self, path: str, body: dict[str, Any] | None = None) -> list[tuple[str, Any]]:
        response = self.client.post(self.url(path), json=body)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        return parse_sse(response.text)

    def turn(self, sid: str) -> dict[str, Any]:
        events = self.stream(f"/sessions/{sid}/turn")
        assert events[-1][0] == "done", events
        session: dict[str, Any] = events[-1][1]["session"]
        return session

    def answer(self, sid: str, node_id: str, action: str, text: str | None = None) -> Any:
        body: dict[str, Any] = {"node_id": node_id, "action": action}
        if text is not None:
            body["text"] = text
        events = self.stream(f"/sessions/{sid}/answer", body)
        assert events[-1][0] == "done", events
        return events[-1][1]["session"]

    def get(self, sid: str) -> dict[str, Any]:
        data: dict[str, Any] = self.client.get(self.url(f"/sessions/{sid}")).json()
        return data


def _env(client: TestClient, indexes: InMemoryIndexRegistry) -> Env:
    space = client.post("/api/spaces", json={"name": "Trips"}).json()
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    record = services.spaces.get(space["id"])
    engine = get_engine(record.db_path)
    return Env(client, space["id"], engine, indexes.for_space(space["id"]), services)


@pytest.fixture
def env(make_client: AppFactory, fake_providers: ProviderRegistry) -> Env:
    fake_providers.override(embedder=KeywordEmbedder(), reranker=OverlapReranker())
    indexes = InMemoryIndexRegistry()
    client = make_client(indexes=indexes)
    assert client.put("/api/capabilities/chat", json={"preset": "mock"}).status_code == 200
    return _env(client, indexes)


class Counting(MockChatAdapter):
    """The mock model, counting the schemas it was asked for."""

    def __init__(self, log: list[str]) -> None:
        super().__init__()
        self.log = log

    def chat(
        self, messages: Any, *, json_schema: dict[str, Any] | None = None, **kwargs: Any
    ) -> ChatResult:
        props = (json_schema or {}).get("properties", {})
        self.log.append(next(iter(props), "text"))
        return super().chat(messages, json_schema=json_schema, **kwargs)


def counting(env: Env, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    monkeypatch.setattr(env.services, "chat_adapter", lambda: Counting(log))
    return log


def current(session: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = next(
        n for n in session["nodes"] if n["id"] == session["current_node_id"]
    )
    return node


def test_full_session_to_complete_with_brief(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.sources()
    calls = counting(env, monkeypatch)
    created = env.create()
    assert calls == []  # creating a session makes no model call
    assert created["status"] == "active" and created["nodes"] == []
    assert created["progress"] == {"resolved": 0, "open": 0} and created["complete"] is False
    sid = created["id"]

    events = env.stream(f"/sessions/{sid}/turn")
    assert [n for n, _ in events] == ["status", "status", "status", "status", "done"]
    assert [d["stage"] for n, d in events if n == "status"] == [
        "generating",
        "retrieving",
        "generating",
        "checking",
    ]
    # One seed call, one turn call, and the judge confirming the cited sentence (as in chat).
    assert calls == ["title", "question", "verdicts"]
    session = events[-1][1]["session"]
    assert [n["title"] for n in session["nodes"]] == [
        "Who it is for",
        "What your sources say about guided",
        "Risks and open problems",
    ]
    assert [n["status"] for n in session["nodes"]] == ["open", "tentative", "tentative"]
    assert [n["dismissible"] for n in session["nodes"]] == [False, True, True]
    assert session["progress"] == {"resolved": 0, "open": 3}
    first = current(session)
    assert first["question"] == "What should be decided about “Who it is for”?"
    rec = first["recommendation"]
    assert (rec["grounding"], rec["badge"], rec["removed"]) == ("cited", "verified", [])
    sentence = rec["sentences"][0]
    assert sentence["kind"] == "cited"
    assert sentence["citations"][0]["source_title"] == "Gull Island guide"
    assert set(sentence["citations"][0]) == {
        "n",
        "chunk_id",
        "source_id",
        "source_title",
        "locator",
    }

    session = env.answer(sid, first["id"], "accept")
    accepted = next(n for n in session["nodes"] if n["id"] == first["id"])
    assert accepted["status"] == "resolved" and accepted["answer_source"] == "accepted"
    assert accepted["final_answer"] == sentence["text"]

    second = current(session)
    answer = "TB-BRANCH families with children; the main risk is bad weather"
    session = env.answer(sid, second["id"], "answer", answer)
    child = next(n for n in session["nodes"] if n["parent_id"] == second["id"])
    assert child["origin"] == "answer" and child["status"] == "tentative" and child["depth"] == 1
    assert session["risks"] == [{"text": f"Raised in an answer: {answer}", "node_id": second["id"]}]
    # The turn's question was chosen before the answer's new branch existed (one call per turn,
    # D-093); the new branch comes right after it, before any later seed.
    third = current(session)
    assert third["title"] == "Risks and open problems"
    session = env.answer(sid, third["id"], "skip")
    assert session["current_node_id"] == child["id"]
    while session["current_node_id"] is not None:
        session = env.answer(sid, session["current_node_id"], "answer", "Keep it simple.")
    assert session["complete"] is True
    assert session["progress"] == {"resolved": 4, "open": 0}
    assert env.client.get(env.url("/sessions")).json()[0]["complete"] is True

    finished = env.client.post(env.url(f"/sessions/{sid}/finish")).json()
    assert finished["status"] == "finished" and finished["brief_at"]
    response = env.client.get(env.url(f"/sessions/{sid}/brief"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and ".md" in disposition
    brief = response.text
    assert brief.startswith(BRIEF_MARKER + "\n# Idea brief: ")
    anchors = brief.split("## Cited anchors\n\n")[1].split("\n\n## ")[0]
    assert f"- {sentence['text']} (*Gull Island guide*, p. " in anchors
    open_questions = brief.split("## Open questions\n\n")[1].split("\n\n## ")[0]
    assert "(skipped)" in open_questions  # the skipped branch is an open question again
    assert "(accepted recommendation)" in brief and "```mermaid\nmindmap\n" in brief


def test_finish_now_mid_session_and_continue(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.sources()
    sid = env.create()["id"]
    session = env.turn(sid)
    waiting = session["current_node_id"]
    assert env.client.get(env.url(f"/sessions/{sid}/brief")).status_code == 404

    finished = env.client.post(env.url(f"/sessions/{sid}/finish"))
    assert finished.status_code == 200 and finished.json()["status"] == "finished"
    brief = env.client.get(env.url(f"/sessions/{sid}/brief")).text
    open_questions = brief.split("## Open questions\n\n")[1].split("\n\n## ")[0]
    assert "(waiting for an answer)" in open_questions
    assert open_questions.count("(not asked yet)") == 2
    assert "_No questions answered yet._" in brief

    turn = env.client.post(env.url(f"/sessions/{sid}/turn"))
    assert turn.status_code == 409 and "Continue" in turn.json()["detail"]
    answer = env.client.post(
        env.url(f"/sessions/{sid}/answer"), json={"node_id": waiting, "action": "skip"}
    )
    assert answer.status_code == 409
    seed = session["nodes"][1]["id"]
    assert env.client.post(env.url(f"/sessions/{sid}/nodes/{seed}/dismiss")).status_code == 409

    reopened = env.client.post(env.url(f"/sessions/{sid}/continue")).json()
    assert reopened["status"] == "active" and reopened["finished_at"] is None
    assert reopened["brief_at"] == finished.json()["brief_at"]  # the brief stays until rewritten
    calls = counting(env, monkeypatch)
    again = env.turn(sid)
    assert again["current_node_id"] == waiting and calls == []  # the waiting question is kept


def test_resume_after_restart(
    make_client: AppFactory, fake_providers: ProviderRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_providers.override(embedder=KeywordEmbedder(), reranker=OverlapReranker())
    indexes = InMemoryIndexRegistry()
    first = make_client(indexes=indexes)
    first.put("/api/capabilities/chat", json={"preset": "mock"})
    env = _env(first, indexes)
    env.sources()
    sid = env.create()["id"]
    before = env.turn(sid)
    first.__exit__(None, None, None)

    second = make_client(indexes=indexes)
    restarted = Env(second, env.space_id, env.engine, env.index, second.app.state.services)  # type: ignore[attr-defined]
    after = restarted.get(sid)
    assert after["nodes"] == before["nodes"]
    assert after["current_node_id"] == before["current_node_id"]
    calls = counting(restarted, monkeypatch)
    assert restarted.turn(sid)["current_node_id"] == before["current_node_id"]
    assert calls == []


def test_failed_cited_sentence_is_removed_never_a_suggestion(env: Env) -> None:
    env.sources()
    session = env.turn(env.create(f"{IDEA} TB-UNSUPPORTED")["id"])
    rec = current(session)["recommendation"]
    assert rec["removed"] == [UNSUPPORTED_SENTENCE]
    assert rec["badge"] == "partially_verified"
    assert UNSUPPORTED_SENTENCE not in [s["text"] for s in rec["sentences"]]
    assert all(s["kind"] == "cited" and s["citations"] for s in rec["sentences"])


def test_without_sources_recommendations_are_suggestions(env: Env) -> None:
    session = env.turn(env.create()["id"])
    rec = current(session)["recommendation"]
    assert rec == {
        "grounding": "suggestion",
        "badge": None,
        "sentences": [{"text": IDEA_SUGGESTION, "citations": [], "kind": "suggestion"}],
        "removed": [],
    }


def test_dismiss_rules(env: Env) -> None:
    sid = env.create()["id"]
    session = env.turn(sid)
    waiting, seed = session["current_node_id"], session["nodes"][2]["id"]
    dismissed = env.client.post(env.url(f"/sessions/{sid}/nodes/{seed}/dismiss"))
    assert dismissed.status_code == 200
    data = dismissed.json()
    assert next(n for n in data["nodes"] if n["id"] == seed)["status"] == "dismissed"
    assert data["progress"] == {"resolved": 0, "open": 2}
    again = env.client.post(env.url(f"/sessions/{sid}/nodes/{seed}/dismiss"))
    assert again.status_code == 409
    asked = env.client.post(env.url(f"/sessions/{sid}/nodes/{waiting}/dismiss"))
    assert asked.status_code == 409
    assert asked.json() == {
        "detail": "Only branches that have not been asked yet can be dismissed."
    }
    assert env.client.post(env.url(f"/sessions/{sid}/nodes/nope/dismiss")).status_code == 404


def test_validation_and_conflicts(env: Env) -> None:
    sessions = env.url("/sessions")
    assert env.client.post(sessions, json={"idea": "   "}).status_code == 422
    assert env.client.post(sessions, json={"idea": "x" * 4001}).status_code == 422
    assert env.client.post(sessions, json={}).status_code == 422
    assert env.client.get(env.url("/sessions/nope")).status_code == 404
    assert env.client.get(env.url("/sessions/nope")).json() == {"detail": "Idea session not found."}
    assert env.client.post(env.url("/sessions/nope/turn")).status_code == 404
    assert env.client.get("/api/spaces/nope/ideation/sessions").status_code == 404

    sid = env.create()["id"]
    answer_url = env.url(f"/sessions/{sid}/answer")
    pending = env.client.post(answer_url, json={"node_id": "x", "action": "skip"})
    assert pending.status_code == 404  # no such question
    session = env.turn(sid)
    waiting, later = session["current_node_id"], session["nodes"][1]["id"]
    for body in (
        {"node_id": waiting, "action": "answer"},
        {"node_id": waiting, "action": "answer", "text": "  "},
        {"node_id": waiting, "action": "skip", "text": "no"},
        {"node_id": waiting, "action": "maybe"},
    ):
        assert env.client.post(answer_url, json=body).status_code == 422, body
    not_asked = env.client.post(answer_url, json={"node_id": later, "action": "skip"})
    assert not_asked.status_code == 409
    assert not_asked.json() == {"detail": "This branch has not been asked yet."}
    env.answer(sid, waiting, "skip")
    twice = env.client.post(answer_url, json={"node_id": waiting, "action": "skip"})
    assert twice.json() == {"detail": "This question was already answered."}

    TURN_LOCKS.acquire((env.space_id, sid))
    try:
        busy = env.client.post(env.url(f"/sessions/{sid}/turn"))
        assert busy.status_code == 409 and "still being prepared" in busy.json()["detail"]
    finally:
        TURN_LOCKS.release((env.space_id, sid))

    env.client.put("/api/capabilities/chat", json={"preset": "custom"})  # no address yet
    not_ready = env.client.post(env.url(f"/sessions/{sid}/turn"))
    assert not_ready.status_code == 409
    assert not_ready.json() == {"detail": "Set up the chat model in Settings first."}


def test_accept_needs_a_recommendation(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = env.create()["id"]
    seed = json.dumps({"title": "T", "branches": ["A", "B", "C"]})
    empty = json.dumps(
        {"question": "Q?", "recommendation": {"sentences": []}, "new_branches": [], "risks": []}
    )
    chat = ScriptedChat(seed, empty)
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    waiting = env.turn(sid)["current_node_id"]
    response = env.client.post(
        env.url(f"/sessions/{sid}/answer"), json={"node_id": waiting, "action": "accept"}
    )
    assert response.status_code == 409 and "no recommended answer" in response.json()["detail"]


def test_unusable_reply_is_an_error_and_ask_again_works(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = env.create()["id"]
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat("bad", "bad"))
    events = env.stream(f"/sessions/{sid}/turn")
    assert events == [("status", {"stage": "generating"}), ("error", {"message": UNUSABLE_REPLY})]
    assert env.get(sid)["nodes"] == []
    monkeypatch.undo()
    assert env.turn(sid)["current_node_id"] is not None


def test_finish_during_a_turn_discards_its_result(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = env.create()["id"]
    store = IdeationStore(env.engine)

    class FinishesMidTurn(MockChatAdapter):
        def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
            schema = kwargs.get("json_schema") or {}
            if "question" in schema.get("properties", {}):
                store.set_status(sid, "finished", None)  # "Finish now" while the model works
            return super().chat(messages, **kwargs)

    monkeypatch.setattr(env.services, "chat_adapter", FinishesMidTurn)
    events = env.stream(f"/sessions/{sid}/turn")
    session = events[-1][1]["session"]
    assert events[-1][0] == "done" and session["status"] == "finished"
    assert session["current_node_id"] is None
    assert all(n["question"] is None for n in session["nodes"])


def test_get_reports_a_running_turn(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A page reloaded during a turn sees ``running`` and waits instead of asking again."""
    sid = env.create()["id"]
    assert env.get(sid)["running"] is False
    seen: list[bool] = []

    class LooksMidTurn(MockChatAdapter):
        def chat(self, messages: Any, **kwargs: Any) -> ChatResult:
            schema = kwargs.get("json_schema") or {}
            if "question" in schema.get("properties", {}):
                seen.append(env.get(sid)["running"])  # a reload while the model works
            return super().chat(messages, **kwargs)

    monkeypatch.setattr(env.services, "chat_adapter", LooksMidTurn)
    session = env.turn(sid)
    assert seen == [True]
    assert session["running"] is False  # the done event ends the turn
    assert env.get(sid)["running"] is False

    TURN_LOCKS.acquire((env.space_id, sid))
    try:
        assert env.get(sid)["running"] is True
        listed = env.client.get(env.url("/sessions")).json()
        assert "running" not in listed[0]  # only the full session carries it
    finally:
        TURN_LOCKS.release((env.space_id, sid))


def test_delete_removes_rows_and_brief(env: Env) -> None:
    sid = env.create()["id"]
    env.turn(sid)
    env.client.post(env.url(f"/sessions/{sid}/finish"))
    folder = env.services.spaces.get(env.space_id).folder / "ideas" / sid
    assert (folder / "brief.md").is_file()
    assert env.client.delete(env.url(f"/sessions/{sid}")).status_code == 204
    assert not folder.exists()
    assert env.client.get(env.url(f"/sessions/{sid}")).status_code == 404
    assert env.client.get(env.url(f"/sessions/{sid}/brief")).status_code == 404
    assert env.client.delete(env.url(f"/sessions/{sid}")).status_code == 404
    assert env.client.get(env.url("/sessions")).json() == []
    assert IdeationStore(env.engine).nodes(sid) == []


def test_list_sessions_newest_first(env: Env) -> None:
    first = env.create("First idea")
    second = env.create("Second idea")
    listed = env.client.get(env.url("/sessions")).json()
    assert [s["id"] for s in listed] == [second["id"], first["id"]]
    assert set(listed[0]) == {
        "id",
        "title",
        "idea",
        "status",
        "progress",
        "complete",
        "created_at",
        "updated_at",
        "finished_at",
        "brief_at",
    }


def test_estimate(env: Env) -> None:
    empty = env.client.get(env.url("/estimate")).json()
    assert empty["remote"] is False and empty["tokens_per_turn"] > empty["seed_tokens"] > 0
    env.sources()
    with_sources = env.client.get(env.url("/estimate")).json()
    assert with_sources["tokens_per_turn"] > empty["tokens_per_turn"]
    env.client.put(
        "/api/capabilities/chat",
        json={"preset": "custom", "base_url": "http://llm.example/v1", "model": "m"},
    )
    assert env.client.get(env.url("/estimate")).json()["remote"] is True


def test_every_ideation_call_is_in_the_request_log(env: Env, router: Router) -> None:
    env.sources()
    mock = MockChatAdapter()

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages: list[ChatMessage] = body["messages"]
        schema = body["response_format"]["json_schema"]["schema"]
        text = mock.chat(messages, json_schema=schema).text
        return httpx.Response(200, json=chat_reply(text))

    router.add("POST", "http://llm.example/v1/chat/completions", reply)
    env.client.put(
        "/api/capabilities/chat",
        json={"preset": "custom", "base_url": "http://llm.example/v1", "model": "m"},
    )
    session = env.turn(env.create()["id"])
    assert current(session)["recommendation"]["grounding"] == "cited"
    log = env.client.get("/api/request-log").json()
    chat_calls = [e for e in log if e["capability"] == "chat"]
    assert len(chat_calls) == len(router.requests) == 3  # seed, turn and the judge
    assert all(e["space_id"] == env.space_id and e["space_name"] == "Trips" for e in chat_calls)
    assert all(e["endpoint_host"] == "llm.example" for e in chat_calls)
