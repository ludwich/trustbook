"""The gate router: parsing, the 5-second fallback, logging and the mock's classification."""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.adapters.base import AdapterError, ChatMessage, ChatResult
from app.capabilities.adapters.mock import MockChatAdapter, gate_decision
from app.capabilities.http import current_space, space_context
from app.chat.gate import (
    FALLBACK,
    GATE_SCHEMA,
    GATE_SYSTEM,
    Gate,
    GateLogNotFoundError,
    GateLogStore,
    GateResult,
    classify,
    gate_messages,
    parse_gate,
)
from app.chat.output import InvalidOutputError
from app.db.engine import get_engine
from app.db.migrate import migrate
from tests.chat_helpers import ScriptedChat


@pytest.fixture
def store(tmp_path: Path) -> GateLogStore:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return GateLogStore(get_engine(db))


def test_parse_gate() -> None:
    assert parse_gate('{"decision": "brainstorm", "confidence": 0.8}') == GateResult(
        "brainstorm", 0.8
    )
    assert parse_gate('```json\n{"decision": " Chitchat ", "confidence": 3}\n```') == GateResult(
        "chitchat", 1.0
    )
    assert parse_gate('{"decision": "sources"}') == GateResult("sources", None)
    assert parse_gate('{"decision": "sources", "confidence": true}').confidence is None
    for bad in ('{"decision": "web"}', '{"confidence": 1}', "[1]", "nope", ""):
        with pytest.raises(InvalidOutputError):
            parse_gate(bad)


def test_gate_prompt_holds_only_the_question() -> None:
    messages = gate_messages("  What   is\nthis? ")
    assert messages == [
        {"role": "system", "content": GATE_SYSTEM},
        {"role": "user", "content": "What is this?"},
    ]
    assert GATE_SCHEMA["properties"]["decision"]["enum"] == ["sources", "brainstorm", "chitchat"]
    assert GATE_SCHEMA["required"] == ["decision", "confidence"]


def test_classify_makes_one_fixed_choice_call_at_temperature_zero() -> None:
    chat = ScriptedChat('{"decision": "chitchat", "confidence": 0.95}')
    assert classify("hi", lambda: chat) == GateResult("chitchat", 0.95)
    assert len(chat.calls) == 1
    assert chat.calls[0]["temperature"] == 0.0
    assert chat.calls[0]["schema"] == GATE_SCHEMA
    assert chat.calls[0]["think"] is False  # reasoning models must not think for a router


class Slow:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def chat(self, messages: Sequence[ChatMessage], **_kwargs: Any) -> ChatResult:
        time.sleep(self.seconds)
        return ChatResult('{"decision": "chitchat", "confidence": 1}')

    def list_models(self) -> list[str]:
        return []


class Failing:
    def chat(self, messages: Sequence[ChatMessage], **_kwargs: Any) -> ChatResult:
        raise AdapterError("unreachable", "down")

    def list_models(self) -> list[str]:
        return []


def test_classify_falls_back_to_sources_on_timeout_failure_or_bad_output() -> None:
    started = time.monotonic()
    assert classify("hi", lambda: Slow(1.0), timeout_s=0.05) == FALLBACK
    assert time.monotonic() - started < 0.8
    assert classify("hi", Failing) == FALLBACK
    assert classify("hi", lambda: ScriptedChat("I think it's chitchat")) == FALLBACK

    def broken_factory() -> Failing:
        raise RuntimeError("no config")

    assert classify("hi", broken_factory) == FALLBACK
    assert GateResult("sources", None) == FALLBACK


def test_classify_keeps_the_space_context_for_the_request_log() -> None:
    seen: list[Any] = []

    class Recording(ScriptedChat):
        def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResult:
            seen.append(current_space.get())
            return super().chat(messages, **kwargs)

    with space_context("sp1", "Acme"):
        classify("hi", lambda: Recording('{"decision": "sources", "confidence": 0.5}'))
    assert seen and seen[0] is not None and seen[0].id == "sp1"


def test_gate_logs_every_decision(store: GateLogStore) -> None:
    gate = Gate(
        store, adapter=lambda: ScriptedChat('{"decision": "brainstorm", "confidence": 0.7}')
    )
    result = gate.route("Ideas for a launch?")
    assert (result.decision, result.confidence) == ("brainstorm", 0.7) and result.log_id
    off = Gate(store, adapter=None).route("How do proxies work?")
    assert (off.decision, off.confidence) == ("sources", None)
    timed_out = Gate(store, adapter=lambda: Slow(1.0), timeout_s=0.05).route("hello")
    assert (timed_out.decision, timed_out.confidence) == ("sources", None)

    entries = store.list()
    assert [(e.input, e.decision, e.confidence) for e in entries] == [
        ("hello", "sources", None),
        ("How do proxies work?", "sources", None),
        ("Ideas for a launch?", "brainstorm", 0.7),
    ]
    assert all(e.user_correction is None and e.created for e in entries)
    assert [e.id for e in store.list(limit=1)] == [entries[0].id]


def test_correction_is_stored(store: GateLogStore) -> None:
    entry = store.add("hello there", GateResult("sources", None))
    corrected = store.correct(entry.id, "chitchat")
    assert corrected.user_correction == "chitchat" and corrected.decision == "sources"
    assert store.list()[0].user_correction == "chitchat"
    assert store.correct(entry.id, None).user_correction is None
    with pytest.raises(GateLogNotFoundError):
        store.correct(9999, "sources")


@pytest.mark.parametrize(
    ("message", "decision"),
    [
        ("hi", "chitchat"),
        ("Hello!", "chitchat"),
        ("thanks", "chitchat"),
        ("Thank you!", "chitchat"),
        ("Good morning", "chitchat"),
        ("Let's brainstorm product names", "brainstorm"),
        ("Any ideas for a workshop?", "brainstorm"),
        ("What if we dropped sessions?", "brainstorm"),
        ("How are proxies configured?", "sources"),
        ("hi, how are proxies configured?", "sources"),
        ("", "sources"),
    ],
)
def test_mock_gate_classification(message: str, decision: str) -> None:
    assert gate_decision(message)["decision"] == decision
    reply = MockChatAdapter().chat(
        [{"role": "user", "content": message}], temperature=0, json_schema=GATE_SCHEMA
    )
    assert parse_gate(reply.text).decision == decision
