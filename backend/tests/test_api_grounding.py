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
from tests.parent_helpers import (
    ADAPTERS,
    adapters_specs,
    add_rows,
    doc,
    status_specs,
    turn,
    with_parent_rows,
)

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
    # Its own card (D-118, G3-2): not "Not in your sources" / "Add sources that cover …".
    assert refused["message"] == "Couldn't verify an answer"
    assert refused["suggestion"] == (
        "An answer was drafted from your sources, but Trustbook could not verify it against the "
        "passages it cited, so it is not shown. Try rephrasing the question, or name the file or "
        "function you mean."
    )
    message = events[-1][1]["message"]
    assert message["badge"] == "refused"
    assert message["refusal_reason"] == "unsupported"
    assert message["content"]["text"] == "Couldn't verify an answer"
    assert message["content"]["sentences"] == []
    diagnostics = message["diagnostics"]
    assert diagnostics.pop("judge_seconds") is None
    fingerprint = diagnostics.pop("retrieval_fingerprint")
    assert isinstance(fingerprint, str) and len(fingerprint) == 16
    timings = diagnostics.pop("timings")
    assert len(timings["generation_s"]) == 2 and len(timings["verification_s"]) == 2
    assert timings["citation_s"] is None and timings["gate_s"] >= 0
    assert len(diagnostics.pop("prompt_tokens")) == 2
    assert diagnostics == {
        "top_rerank": 1.0,
        "sentence_scores": [0.0],
        "regenerated": True,
        "sentence_methods": [None],
        "judge_vetoed": [],
        "citation_changes": [],
        "generation": {"temperature": 0.2, "seed": None},
        # The second reply's failed sentence and why it failed (D-075).
        "failed_sentences": [{"text": "Another invention.", "reason": "checker score 0.00 < 0.5"}],
        # Latency (D-080): two answer calls and two passes, no judge call.
        "judge_calls": 0,
        "judge_calls_per_pass": [0, 0],
        "answer_calls": 2,
        "completion_tokens": [None, None],
        "outline_only": [0, 0],
        "expansions": [],
        "promoted_tokens": 0,
        "outline_tokens": 0,
        "outline_fingerprint": None,
        "promoted_cited": [0, 0],
        # Transcript windows (D-091): none without a transcript.
        "windows": [],
        "window_tokens": 0,
        "window_fingerprint": None,
        "window_only": [0, 0],
        # Layer 3 clean-up (D-117): nothing to drop or merge.
        "syntax_fragments": [],
        "duplicate_sentences": 0,
    }
    assert len(chat.calls) == 2  # answer, regeneration
    # The regeneration is told which sentence of the first reply failed and why (D-074), in the
    # user message; the system message is the same on both attempts (D-079).
    first, second = (call["messages"] for call in chat.calls)
    assert (
        '1. "Made-up fact." (cited c1): not supported by the cited chunks.' in second[1]["content"]
    )
    assert "Made-up fact." not in first[1]["content"]
    assert second[0] == first[0]


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


# --- parent context and latency diagnostics (D-077, D-079, D-080) ----------------------------

PARENT_QUESTION = "How does send use the connection and urlparse request url?"


def adapters_source(env: Env, monkeypatch: pytest.MonkeyPatch) -> tuple[str, dict[int, str]]:
    """A folder source with ``adapters.py`` (structure as slice A stores it) and notes that
    outrank the class head, so ``HTTPAdapter.send`` is a child whose head gets promoted."""
    with_parent_rows(monkeypatch)
    source = add_source(env.engine, "requests", source_type="folder", group="code")
    notes = [
        doc(
            20 + n,
            1,
            5,
            f"Note {n} about the request.",
            section=None,
            head=False,
            file=f"notes{n}.md",
        )
        for n in range(8)
    ]
    ids = add_rows(env.engine, env.index, source, [*adapters_specs(), *notes])
    folder = env.services.spaces.get(env.space_id).folder / "sources" / source
    folder.mkdir(parents=True)
    segments = [{"start": 0, "end": 9999, "file": ADAPTERS, "start_line": 1, "end_line": 300}]
    (folder / "map.json").write_text(
        json.dumps({"version": 1, "kind": "files", "segments": segments})
    )
    return source, ids


def stored_locator(env: Env, chunk_id: str) -> dict[str, Any]:
    from sqlalchemy import text as sql

    with env.engine.connect() as conn:
        raw = conn.execute(sql("SELECT locator FROM chunks WHERE id = :id"), {"id": chunk_id})
        value: dict[str, Any] = json.loads(raw.scalar_one())
    return value


def test_a_code_answer_reports_parent_context_judge_calls_and_timings(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ids = adapters_source(env, monkeypatch)
    message = env.ask(f"{PARENT_QUESTION} TB-OUTLINE-FACT")[-1][1]["message"]
    d = message["diagnostics"]
    # The class head is promoted for the send child; the outline goes with it.
    assert d["expansions"] == [{"chunk_id": ids[2], "kind": "class_head", "referrer": ids[5]}]
    assert d["promoted_tokens"] == 60 and d["outline_tokens"] > 0
    assert isinstance(d["outline_fingerprint"], str) and len(d["outline_fingerprint"]) == 16
    # The mock's outline-only sentence is removed and counted (D-079: it fails closed).
    assert message["badge"] == "partially_verified"
    (removed,) = message["removed_sentences"]
    assert removed.startswith("The file also defines `") and d["outline_only"] == [1]
    # Latency (D-080).
    assert d["answer_calls"] == 1 and len(d["prompt_tokens"]) == 1 and d["prompt_tokens"][0] > 0
    assert d["judge_calls"] >= 1 and d["judge_calls_per_pass"] == [d["judge_calls"]]
    timings = d["timings"]
    assert set(timings) == {
        "retrieval_s",
        "generation_s",
        "verification_s",
        "citation_s",
        "gate_s",
        "total_s",
    }
    assert len(timings["generation_s"]) == 1 and len(timings["verification_s"]) == 1
    assert timings["total_s"] >= timings["retrieval_s"] >= 0 and timings["gate_s"] >= 0
    # Citations are the child chunks' own locators, never a parent span.
    for sentence in message["content"]["sentences"]:
        for citation in sentence["citations"]:
            assert citation["locator"] == stored_locator(env, citation["chunk_id"])
            assert "parent_key" not in citation["locator"]


def test_the_outline_changes_only_the_outline_fingerprint(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters_source(env, monkeypatch)
    on = env.ask(PARENT_QUESTION)[-1][1]["message"]["diagnostics"]
    put = env.client.put("/api/settings", json={"retrieval": {"outline": False}})
    assert put.status_code == 200
    no_outline = env.ask(PARENT_QUESTION)[-1][1]["message"]["diagnostics"]
    assert no_outline["retrieval_fingerprint"] == on["retrieval_fingerprint"]
    assert no_outline["outline_fingerprint"] is None and no_outline["outline_tokens"] == 0
    assert no_outline["expansions"] == on["expansions"]  # promotions stay
    env.client.put("/api/settings", json={"retrieval": {"parent_context": False}})
    off = env.ask(PARENT_QUESTION)[-1][1]["message"]["diagnostics"]
    assert off["expansions"] == [] and off["promoted_tokens"] == 0
    # The promoted chunk was a numbered chunk: the context fingerprint covers it.
    assert off["retrieval_fingerprint"] != on["retrieval_fingerprint"]


def test_a_data_table_in_context_brings_its_user_into_the_answer_context(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-119 (G3-2 F1): the ``_codes`` part with the 429 row is in the context, ``_init`` (which
    reads ``_codes``) is cut by the 8-chunk cap; it is promoted as ``data_user`` for the table
    part, a numbered chunk the diagnostics record like any expansion. Off with parent context."""

    with_parent_rows(monkeypatch)
    source = add_source(env.engine, "requests", source_type="folder", group="code")
    notes = [
        doc(20 + n, 1, 5, f"Note {n} about status names for 429.", section=None, head=False)
        for n in range(6)
    ]
    ids = add_rows(env.engine, env.index, source, [*status_specs(), *notes])
    question = "Which status names does 429 have?"
    d = env.ask(question)[-1][1]["message"]["diagnostics"]
    assert d["expansions"] == [{"chunk_id": ids[4], "kind": "data_user", "referrer": ids[2]}]
    assert d["promoted_tokens"] == 120
    env.client.put("/api/settings", json={"retrieval": {"parent_context": False}})
    off = env.ask(question)[-1][1]["message"]["diagnostics"]
    assert off["expansions"] == [] and off["promoted_tokens"] == 0


# --- transcript windows (D-091) ---------------------------------------------------------------

WINDOW_QUESTION = "When will we ship the release on Friday?"
FILLER = (
    "apples bicycles candles dolphins engines falcons glaciers harbours islands jackets kettles "
    "lanterns meadows needles oceans pebbles quarries rivers saddles tunnels umbrellas valleys "
    "wagons yachts zebras anchors barrels cabins dunes"
).split()


def transcript_source(env: Env) -> dict[int, str]:
    """A 29-turn call (400 tokens a turn): the context holds 8 turns, so neighbours are left."""
    source = add_source(env.engine, "Planning call", source_type="youtube")
    specs = [
        turn(n, f"Then we talked about {word} for a while.", tokens=400)
        for n, word in enumerate(FILLER)
    ]
    specs[14] = turn(14, "We agreed to ship the release on Friday.", tokens=400)
    return add_rows(env.engine, env.index, source, specs)


def test_a_transcript_answer_gets_windows_and_a_window_only_sentence_fails(env: Env) -> None:
    ids = transcript_source(env)
    message = env.ask(f"{WINDOW_QUESTION} TB-WINDOW-FACT")[-1][1]["message"]
    d = message["diagnostics"]
    assert d["windows"] and d["window_tokens"] == sum(w["tokens"] for w in d["windows"])
    assert ids[14] in d["windows"][0]["numbered"]
    assert isinstance(d["window_fingerprint"], str) and len(d["window_fingerprint"]) == 16
    # The mock quotes a neighbour only the window shows, citing c1: it fails and is removed.
    assert message["badge"] == "partially_verified"
    (removed,) = message["removed_sentences"]
    assert removed.startswith("Then we talked about ")
    assert d["window_only"] == [1]
    for sentence in message["content"]["sentences"]:
        for citation in sentence["citations"]:
            assert citation["locator"] == stored_locator(env, citation["chunk_id"])


def test_parent_context_off_gives_no_windows(env: Env) -> None:
    transcript_source(env)
    on = env.ask(WINDOW_QUESTION)[-1][1]["message"]["diagnostics"]
    assert on["windows"] and on["window_only"] == [0]
    put = env.client.put("/api/settings", json={"retrieval": {"parent_context": False}})
    assert put.status_code == 200
    off = env.ask(WINDOW_QUESTION)[-1][1]["message"]["diagnostics"]
    assert off["windows"] == [] and off["window_tokens"] == 0
    assert off["window_fingerprint"] is None
    # The windows never change the numbered chunks.
    assert off["retrieval_fingerprint"] == on["retrieval_fingerprint"]
    env.client.put("/api/settings", json={"retrieval": {"parent_context": True, "outline": False}})
    no_outline = env.ask(WINDOW_QUESTION)[-1][1]["message"]["diagnostics"]
    assert no_outline["window_fingerprint"] == on["window_fingerprint"]  # outline off keeps them
