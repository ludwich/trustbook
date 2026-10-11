"""SSE integration of layers 4-5, badges, diagnostics and the gate (mock LLM, fake models)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.capabilities.adapters.base import AdapterError
from app.capabilities.bundled import ProviderRegistry
from app.chat.gate import CHITCHAT_REPLY
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
from tests.fakes import FakeGrounding, Router

GUIDE = "Requests is an HTTP library for Python. Proxies are configured with the proxies argument."
QUOTE = "Proxies are configured with the proxies argument."


@dataclass
class Env:
    client: TestClient
    space_id: str
    engine: Engine
    index: InMemorySpaceIndex
    services: Services
    grounding: FakeGrounding

    def url(self, path: str = "") -> str:
        return f"/api/spaces/{self.space_id}{path}"

    def conversation(self) -> str:
        return str(self.client.post(self.url("/conversations"), json={}).json()["id"])

    def ask(self, question: str, cid: str | None = None) -> list[tuple[str, Any]]:
        cid = cid or self.conversation()
        response = self.client.post(
            self.url(f"/conversations/{cid}/messages"), json={"content": question}
        )
        assert response.status_code == 200, response.text
        return parse_sse(response.text)

    def gate_log(self, query: str = "") -> list[dict[str, Any]]:
        response = self.client.get(self.url(f"/gate-log{query}"))
        assert response.status_code == 200, response.text
        data: list[dict[str, Any]] = response.json()
        return data


@pytest.fixture
def env(make_client: AppFactory, fake_providers: ProviderRegistry) -> Env:
    grounding = FakeGrounding()
    fake_providers.override(
        embedder=KeywordEmbedder(), reranker=OverlapReranker(), grounding=grounding
    )
    indexes = InMemoryIndexRegistry()
    client = make_client(indexes=indexes)
    assert client.put("/api/capabilities/chat", json={"preset": "mock"}).status_code == 200
    space = client.post("/api/spaces", json={"name": "Acme"}).json()
    services: Services = client.app.state.services  # type: ignore[attr-defined]
    record = services.spaces.get(space["id"])
    engine = get_engine(record.db_path)
    return Env(client, space["id"], engine, indexes.for_space(space["id"]), services, grounding)


def guide(env: Env, locator: dict[str, Any] | None = None) -> tuple[str, str]:
    source = add_source(env.engine, "Requests guide")
    return source, add_chunk(env.engine, env.index, source, GUIDE, locator=locator)


def names(events: list[tuple[str, Any]]) -> list[str]:
    return [name for name, _ in events]


def stages(events: list[tuple[str, Any]]) -> list[str]:
    return [data["stage"] for name, data in events if name == "status"]


def test_partially_verified_answer_streams_only_passing_sentences(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, chunk_id = guide(env)
    reply = answer_json((QUOTE, ["c1"]), ("Proxies also need a licence key.", ["c1"]))
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat(reply))
    cid = env.conversation()
    events = env.ask("How are proxies configured?", cid)

    assert names(events) == ["status", "status", "status", "sentence", "done"]
    assert stages(events) == ["retrieving", "generating", "checking"]
    citation = {
        "n": 1,
        "chunk_id": chunk_id,
        "source_id": source,
        "source_title": "Requests guide",
        "locator": {},
    }
    assert events[3][1] == {"index": 0, "text": QUOTE, "citations": [citation]}
    message = events[-1][1]["message"]
    assert message["badge"] == "partially_verified"
    assert message["removed_sentences"] == ["Proxies also need a licence key."]
    assert message["content"] == {"sentences": [{"text": QUOTE, "citations": [citation]}]}
    assert message["diagnostics"]["sentence_scores"][0] == 1.0
    assert message["diagnostics"]["sentence_scores"][1] < 0.5
    assert message["diagnostics"]["regenerated"] is False
    assert message["diagnostics"]["top_rerank"] == 1.0

    history = env.client.get(env.url(f"/conversations/{cid}/messages")).json()
    assert history[1] == message
    assert history[0]["diagnostics"] is None  # user message
    rows = ChatStore(env.engine).citation_rows(message["id"])
    assert [(r.sentence_index, r.chunk_id, r.verifier_score) for r in rows] == [(0, chunk_id, 1.0)]
    # Every pair went through the grounding checker: premise = chunk text, hypothesis = sentence,
    # then focused windows cut from that same chunk text (D-044).
    pairs = env.grounding.calls[-1]
    assert pairs[:2] == [(GUIDE, QUOTE), (GUIDE, "Proxies also need a licence key.")]
    for premise, _hypothesis in pairs[2:]:
        assert all(line in GUIDE for line in premise.splitlines())


def test_mostly_unsupported_answer_is_regenerated_then_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide(env)
    # The guide is prose: sentences the checker fails are not sent for a rescue (D-064), and
    # nothing passed the checker, so the judge is never called.
    chat = ScriptedChat(
        answer_json(("Made-up fact.", ["c1"])), answer_json(("Another invention.", ["c1"]))
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    events = env.ask("How are proxies configured?")
    assert names(events) == ["status"] * 5 + ["refused", "done"]
    assert stages(events) == ["retrieving", "generating", "checking", "generating", "checking"]
    refused = events[-2][1]
    assert refused["reason"] == "unsupported"
    assert refused["message"] == "Not in your sources"
    assert refused["suggestion"] == (
        "Add sources that cover “how are proxies configured”, then ask again."
    )
    message = events[-1][1]["message"]
    assert message["badge"] == "refused"
    assert message["content"]["sentences"] == []
    diagnostics = message["diagnostics"]
    assert diagnostics.pop("judge_seconds") is None
    fingerprint = diagnostics.pop("retrieval_fingerprint")
    assert isinstance(fingerprint, str) and len(fingerprint) == 16
    assert diagnostics == {
        "top_rerank": 1.0,
        "sentence_scores": [0.0],
        "regenerated": True,
        "sentence_methods": [None],
        "judge_vetoed": [],
        "citation_changes": [],
        "generation": {"temperature": 0.2, "seed": None},
    }
    assert len(chat.calls) == 2  # answer, regeneration


def test_generation_override_reaches_the_answer_and_judge_calls(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide(env)
    chat = ScriptedChat(
        answer_json((QUOTE, ["c1"]), ("Proxies also need a licence key.", ["c1"])),
        # One judge call confirms the checker pass; the failed prose sentence is not sent.
        json.dumps(
            {
                "verdicts": [
                    {
                        "index": 1,
                        "supported": True,
                        "evidence": [{"chunk": "c1", "start_line": 1, "end_line": 1}],
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    cid = env.conversation()
    response = env.client.post(
        env.url(f"/conversations/{cid}/messages"),
        json={
            "content": "How are proxies configured?",
            "generation": {"temperature": 0, "seed": 7},
        },
    )
    assert response.status_code == 200, response.text
    message = parse_sse(response.text)[-1][1]["message"]
    assert message["diagnostics"]["generation"] == {"temperature": 0.0, "seed": 7}
    answer_call, judge_call = chat.calls
    assert (answer_call["temperature"], answer_call["seed"]) == (0.0, 7)
    assert (judge_call["temperature"], judge_call["seed"]) == (0.0, 7)
    assert message["badge"] == "partially_verified"
    assert message["diagnostics"]["sentence_methods"] == ["checker", None]
    assert message["diagnostics"]["judge_vetoed"] == []


def test_default_generation_is_the_spec_temperature_without_a_seed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide(env)
    chat = ScriptedChat(answer_json((QUOTE, ["c1"])))
    monkeypatch.setattr(env.services, "chat_adapter", lambda: chat)
    message = env.ask("How are proxies configured?")[-1][1]["message"]
    assert message["diagnostics"]["generation"] == {"temperature": 0.2, "seed": None}
    assert (chat.calls[0]["temperature"], chat.calls[0]["seed"]) == (0.2, None)


@pytest.mark.parametrize(
    "generation",
    [{"temperature": 1.5}, {"temperature": -0.1}, {"seed": -1}, {"seed": 2**32}, {"top_p": 1}],
)
def test_generation_override_is_validated(env: Env, generation: dict[str, Any]) -> None:
    cid = env.conversation()
    response = env.client.post(
        env.url(f"/conversations/{cid}/messages"),
        json={"content": "How are proxies configured?", "generation": generation},
    )
    assert response.status_code == 422


def test_grounding_off_badges_unverified(env: Env) -> None:
    guide(env)
    assert env.client.put("/api/capabilities/grounding", json={"enabled": False}).status_code == 200
    events = env.ask("How are proxies configured?")
    message = events[-1][1]["message"]
    assert message["badge"] == "unverified"
    assert message["diagnostics"]["sentence_scores"] == [None]
    assert env.grounding.calls == []
    rows = ChatStore(env.engine).citation_rows(message["id"])
    assert [r.verifier_score for r in rows] == [None]


def test_mock_answer_is_verified(env: Env) -> None:
    guide(env)
    message = env.ask("How are proxies configured?")[-1][1]["message"]
    assert message["badge"] == "verified" and message["removed_sentences"] == []


def test_code_citations_outside_the_snapshot_are_removed(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = add_source(env.engine, "requests", source_type="folder", group="code")
    good_text = "File: src/api.py (lines 1-3)\ndef get(url):\n    return request('get', url)"
    bad_text = "File: src/api.py (lines 40-42)\ndef post(url):\n    return request('post', url)"
    add_chunk(
        env.engine,
        env.index,
        source,
        good_text,
        locator={"file": "src/api.py", "start_line": 1, "end_line": 3, "symbol": None},
    )
    add_chunk(
        env.engine,
        env.index,
        source,
        bad_text,
        ordinal=1,
        locator={"file": "src/api.py", "start_line": 40, "end_line": 42, "symbol": None},
    )
    folder = env.services.spaces.get(env.space_id).folder / "sources" / source
    folder.mkdir(parents=True)
    segments = [{"start": 0, "end": 99, "file": "src/api.py", "start_line": 1, "end_line": 30}]
    (folder / "map.json").write_text(
        json.dumps({"version": 1, "kind": "files", "segments": segments})
    )

    class ByLabel(ScriptedChat):
        """Cites whichever label holds each chunk (retrieval order is not fixed)."""

        def chat(self, messages: Any, **kwargs: Any) -> Any:
            prompt = messages[1]["content"]
            first_is_good = prompt.index("lines 1-3") < prompt.index("lines 40-42")
            good, bad = ("c1", "c2") if first_is_good else ("c2", "c1")
            self.replies = [
                answer_json(
                    ("`get` sends a GET request.", [good]),
                    ("`post` sends a POST request.", [bad]),
                )
            ]
            return super().chat(messages, **kwargs)

    env.grounding.scores.update(
        {"`get` sends a GET request.": 0.9, "`post` sends a POST request.": 0.9}
    )
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ByLabel())
    message = env.ask("How do get and post send a request?")[-1][1]["message"]
    assert message["badge"] == "partially_verified"
    assert [s["text"] for s in message["content"]["sentences"]] == ["`get` sends a GET request."]
    assert message["removed_sentences"] == ["`post` sends a POST request."]
    assert message["diagnostics"]["sentence_scores"] == [0.9, 0.0]


# --- gate -------------------------------------------------------------------------------------


def test_chitchat_gets_the_fixed_reply_without_an_answer_call(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide(env)
    monkeypatch.setattr(env.services, "chat_adapter", lambda: ScriptedChat())  # no replies
    cid = env.conversation()
    events = env.ask("Hello!", cid)
    assert names(events) == ["status", "done"]
    message = events[-1][1]["message"]
    assert message["role"] == "assistant" and message["badge"] is None
    assert message["content"] == {"text": CHITCHAT_REPLY}
    assert CHITCHAT_REPLY == (
        "I answer questions from the sources in this space. Ask about something in them."
    )
    assert message["removed_sentences"] == [] and message["sources_used"] == []
    assert env.client.get(env.url(f"/conversations/{cid}/messages")).json()[1] == message
    entry = env.gate_log()[0]
    assert (entry["input"], entry["decision"], entry["confidence"]) == ("Hello!", "chitchat", 0.9)
    assert entry["user_correction"] is None and entry["created"]
    assert message["gate_log_id"] == entry["id"]


def test_brainstorm_runs_the_grounded_pipeline_and_adds_the_ideation_hint(env: Env) -> None:
    guide(env)
    cid = env.conversation()
    events = env.ask("What if proxies were configured differently?", cid)
    assert "sentence" in names(events)
    message = events[-1][1]["message"]
    assert message["badge"] == "verified"
    assert message["content"]["hint"] == "ideation"
    assert env.client.get(env.url(f"/conversations/{cid}/messages")).json()[1] == message
    assert env.gate_log()[0]["decision"] == "brainstorm"

    refused = env.ask("Brainstorm ideas for a llama farm")[-1][1]["message"]
    assert refused["badge"] == "refused" and refused["content"]["hint"] == "ideation"


def test_sources_questions_have_no_hint_and_are_logged(env: Env) -> None:
    guide(env)
    cid = env.conversation()
    message = env.ask("How are proxies configured?", cid)[-1][1]["message"]
    assert "hint" not in message["content"]
    entry = env.gate_log()[0]
    assert (entry["decision"], entry["confidence"]) == ("sources", 0.7)
    assert message["gate_log_id"] == entry["id"]
    history = env.client.get(env.url(f"/conversations/{cid}/messages")).json()
    assert history[0]["gate_log_id"] is None  # user message
    assert history[1]["gate_log_id"] == entry["id"] and "gate_log_id" not in history[1]["content"]
    refused = env.ask("Brainstorm a llama farm")[-1][1]["message"]
    assert refused["badge"] == "refused" and refused["gate_log_id"] == env.gate_log()[0]["id"]


def test_gate_failure_or_no_gate_means_sources_with_null_confidence(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    guide(env)

    class Down:
        def chat(self, *_a: Any, **_k: Any) -> Any:
            raise AdapterError("unreachable", "down")

        def list_models(self) -> list[str]:
            return []

    monkeypatch.setattr("app.api.chat.gate_adapter", lambda _services: lambda: Down())
    message = env.ask("hello")[-1][1]["message"]
    assert message["content"]["text"] != CHITCHAT_REPLY  # answered or refused, never skipped
    assert (env.gate_log()[0]["decision"], env.gate_log()[0]["confidence"]) == ("sources", None)
    monkeypatch.undo()

    # A separate gate endpoint that is not set up: no call, logged as sources.
    response = env.client.put("/api/capabilities/gate", json={"use_chat": False})
    assert response.json()["status"] == "not_configured"
    env.ask("hello")
    assert (env.gate_log()[0]["decision"], env.gate_log()[0]["confidence"]) == ("sources", None)


def test_gate_requests_are_logged_under_the_gate_capability(env: Env, router: Router) -> None:
    guide(env)
    seen: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        content = json.dumps({"decision": "chitchat", "confidence": 0.6})
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
        )

    router.add("POST", "http://gate.example/v1/chat/completions", reply)
    env.client.put(
        "/api/capabilities/gate",
        json={
            "use_chat": False,
            "preset": "custom",
            "base_url": "http://gate.example/v1",
            "model": "small",
        },
    )
    events = env.ask("hi")
    assert events[-1][1]["message"]["content"] == {"text": CHITCHAT_REPLY}
    assert seen[0]["temperature"] == 0
    assert seen[0]["messages"][-1]["content"] == "hi"
    log = env.client.get("/api/request-log").json()
    assert (log[0]["capability"], log[0]["space_id"]) == ("gate", env.space_id)


def test_gate_log_routes(env: Env) -> None:
    guide(env)
    for question in ("hello", "How are proxies configured?", "thanks"):
        env.ask(question)
    entries = env.gate_log()
    assert [e["input"] for e in entries] == ["thanks", "How are proxies configured?", "hello"]
    assert set(entries[0]) == {
        "id",
        "input",
        "decision",
        "confidence",
        "user_correction",
        "created",
    }
    assert [e["input"] for e in env.gate_log("?limit=2")] == [
        "thanks",
        "How are proxies configured?",
    ]
    assert env.client.get(env.url("/gate-log?limit=0")).status_code == 422

    target = entries[1]["id"]
    response = env.client.post(
        env.url(f"/gate-log/{target}/correct"), json={"decision": "brainstorm"}
    )
    assert response.status_code == 200
    assert response.json()["user_correction"] == "brainstorm"
    assert response.json()["decision"] == "sources"
    assert env.gate_log()[1]["user_correction"] == "brainstorm"

    cleared = env.client.post(env.url(f"/gate-log/{target}/correct"), json={"decision": None})
    assert cleared.status_code == 200 and cleared.json()["user_correction"] is None
    assert env.client.post(env.url(f"/gate-log/{target}/correct"), json={}).status_code == 422

    bad = env.client.post(env.url(f"/gate-log/{target}/correct"), json={"decision": "web"})
    assert bad.status_code == 422
    missing = env.client.post(env.url("/gate-log/9999/correct"), json={"decision": "sources"})
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Gate log entry not found."}
    assert env.client.get("/api/spaces/nope/gate-log").status_code == 404
