"""Test hooks (only with TRUSTBOOK_MOCK_LLM=1): TB-UNSUPPORTED, TB-SEEDS4, the mock code judge,
mock search."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.capabilities.adapters.base import AdapterError
from app.capabilities.adapters.factory import build_search_adapter
from app.capabilities.adapters.mock import (
    GARBLED_FRAGMENT,
    SEEDS4_BRANCH,
    SEEDS4_TRIGGER,
    UNSUPPORTED_SENTENCE,
    WINDOW_FACT_TRIGGER,
    MockChatAdapter,
    MockSearchAdapter,
)
from app.capabilities.bundled import ProviderRegistry
from app.capabilities.http import OutboundHttp
from app.capabilities.registry import CapabilityRegistry, ConfigError
from app.capabilities.schemas import CapabilityConfigUpdate as Update
from app.chat.pipeline import Answered, answer
from app.chat.prompt import ANSWER_SCHEMA, answer_messages
from app.config import Paths, SettingsStore
from app.grounding.code_judge import CodeJudge, judge_messages, prepare
from app.grounding.verify import Verifier
from app.ideation import map as branch_map
from app.ideation.output import parse_seed
from app.ideation.prompt import SEED_SCHEMA, seed_messages
from app.retrieval.parents import TranscriptWindow, WindowPart
from app.retrieval.service import Retrieval, RetrievedChunk
from tests.fakes import FakeGrounding

PROSE = RetrievedChunk(
    "k1", "s1", "Guide", "Proxies are configured with the proxies argument.", {}, 12, 0.9
)
CODE = RetrievedChunk(
    "k2",
    "s1",
    "requests",
    "File: src/requests/adapters.py (lines 10-12)\n"
    "DEFAULT_POOLSIZE = 10\n"
    "DEFAULT_RETRIES = 0\n"
    "DEFAULT_POOL_TIMEOUT = None",
    {"file": "src/requests/adapters.py", "start_line": 10, "end_line": 12},
    20,
    0.9,
)


def test_tb_unsupported_adds_a_failing_sentence_and_the_answer_is_partially_verified() -> None:
    mock = MockChatAdapter()
    question = "How are proxies configured? TB-UNSUPPORTED"
    reply = json.loads(
        mock.chat(answer_messages(question, [PROSE]), json_schema=ANSWER_SCHEMA).text
    )
    assert reply["sentences"][-1] == {"text": UNSUPPORTED_SENTENCE, "chunk_ids": ["c1"]}
    without = json.loads(
        mock.chat(
            answer_messages("How are proxies configured?", [PROSE]), json_schema=ANSWER_SCHEMA
        ).text
    )
    assert UNSUPPORTED_SENTENCE not in [s["text"] for s in without["sentences"]]

    found = Retrieval(chunks=[PROSE], ranked=[PROSE], source_ids=["s1"])
    outcome = answer(
        question,
        retrieve=lambda _q: found,
        chat_adapter=MockChatAdapter,
        refuse_threshold=0.15,
        verifier=Verifier(FakeGrounding().score, threshold=0.5),
    )
    assert isinstance(outcome, Answered)
    assert outcome.badge == "partially_verified"
    assert outcome.removed == [UNSUPPORTED_SENTENCE]


def test_tb_garbled_reply_is_shown_once_without_the_fragment() -> None:
    """D-117, ref-near-05's shape through the whole pipeline with the mock adapter: the
    fragment is dropped before verification, the repeats are merged, nothing is removed."""
    mock = MockChatAdapter()
    question = "How are proxies configured? TB-GARBLED"
    reply = json.loads(
        mock.chat(answer_messages(question, [PROSE]), json_schema=ANSWER_SCHEMA).text
    )
    answer_sentence = {"text": PROSE.text, "chunk_ids": ["c1"]}
    fragment = {"text": GARBLED_FRAGMENT, "chunk_ids": ["c1"]}
    assert reply["sentences"] == [answer_sentence, fragment, answer_sentence]

    grounding = FakeGrounding()
    found = Retrieval(chunks=[PROSE], ranked=[PROSE], source_ids=["s1"])
    outcome = answer(
        question,
        retrieve=lambda _q: found,
        chat_adapter=MockChatAdapter,
        refuse_threshold=0.15,
        verifier=Verifier(grounding.score, threshold=0.5),
    )
    assert isinstance(outcome, Answered)
    assert [s.text for s in outcome.sentences] == [PROSE.text]
    assert outcome.badge == "verified" and outcome.removed == []
    checked = {hypothesis for call in grounding.calls for _premise, hypothesis in call}
    assert checked == {PROSE.text}  # the fragment never reached the checker
    assert outcome.diagnostics.syntax_fragments == [GARBLED_FRAGMENT]
    assert outcome.diagnostics.duplicate_sentences == 1
    assert len(outcome.diagnostics.sentence_scores) == 1


def test_mock_judge_supports_two_shared_words_and_names_the_lines() -> None:
    sentences: list[tuple[str, list[RetrievedChunk]]] = [
        ("DEFAULT_POOLSIZE is 10 and DEFAULT_RETRIES is 0.", [CODE]),
        ("Retries are unlimited.", [CODE]),
    ]
    chunks, statements = prepare(sentences)
    reply = MockChatAdapter().chat(
        judge_messages(list(chunks.values()), statements),
        json_schema={"properties": {"verdicts": {}}},
    )
    data = json.loads(reply.text)
    assert data["verdicts"][0] == {
        "index": 1,
        "supported": True,
        "evidence": [{"chunk": "c1", "start_line": 10, "end_line": 12}],  # the whole cited chunk
    }
    assert data["verdicts"][1]["supported"] is False  # no word shared
    # Through the real acceptance rules: the first passes, the second fails.
    outcomes = CodeJudge(MockChatAdapter)(sentences)
    assert outcomes is not None and [o.accepted for o in outcomes] == [True, False]


def registry_for(paths: Paths) -> CapabilityRegistry:
    return CapabilityRegistry(paths, SettingsStore(paths), ProviderRegistry(paths.models_dir))


def test_mock_search_provider_is_offered_only_in_mock_mode(
    mock_paths: Paths, no_mock_paths: Paths
) -> None:
    mock = registry_for(mock_paths)
    assert "mock" in [o.value for o in mock.get("web_search").providers]
    search = mock.update("web_search", Update(provider="mock"))
    assert (search.status, search.locality) == ("ready", "local")
    assert search.config.provider == "mock"
    real = registry_for(no_mock_paths)
    assert "mock" not in [o.value for o in real.get("web_search").providers]
    field = next(f for f in real.get("web_search").fields if f.name == "provider")
    assert "mock" not in [o.value for o in field.options or []]
    with pytest.raises(ConfigError):
        real.update("web_search", Update(provider="mock"))
    # A setting left over from a test run does not work without mock mode.
    assert real.get("web_search").status == "not_configured"


def test_mock_search_adapter_needs_the_environment_flag(
    monkeypatch: pytest.MonkeyPatch, http: OutboundHttp
) -> None:
    monkeypatch.delenv("TRUSTBOOK_MOCK_LLM", raising=False)
    with pytest.raises(AdapterError):
        build_search_adapter("mock", None, http, searxng_url="http://searxng:8080")
    monkeypatch.setenv("TRUSTBOOK_MOCK_LLM", "1")
    adapter = build_search_adapter("mock", None, http, searxng_url="http://searxng:8080")
    assert isinstance(adapter, MockSearchAdapter)


def test_mock_search_results_demote_homepages_for_specific_queries() -> None:
    adapter = MockSearchAdapter()
    specific = [r.url for r in adapter.search("AB-731 certificate exam", 10)]
    assert specific == [
        "https://docs.example.com/certificates/ab-731",
        "https://learn.example.com/exams/ab-731",
        "https://www.example.com/sv-se/",
        "https://example.org/",
    ]
    assert [r.url for r in adapter.search("example", 2)] == [
        "https://www.example.com/sv-se/",
        "https://example.org/",
    ]


def test_mock_web_search_through_the_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRUSTBOOK_MOCK_LLM", "1")
    put = client.put("/api/capabilities/web_search", json={"provider": "mock"})
    assert put.status_code == 200, put.text
    assert (put.json()["status"], put.json()["config"]["provider"]) == ("ready", "mock")
    tested = client.post("/api/capabilities/web_search/test", json={})
    assert tested.status_code == 200 and tested.json()["ok"] is True, tested.text
    assert client.get("/api/capabilities/web_search").json()["status"] == "ready"
    space: dict[str, Any] = client.post("/api/spaces", json={"name": "Hooks"}).json()
    response = client.post(
        f"/api/spaces/{space['id']}/web-search", json={"query": "AB-731 exam skills"}
    )
    assert response.status_code == 200
    urls = [r["url"] for r in response.json()["results"]]
    assert urls[:2] == [
        "https://docs.example.com/certificates/ab-731",
        "https://learn.example.com/exams/ab-731",
    ]


def test_mock_answers_never_quote_a_code_chunk_header() -> None:
    from app.capabilities.adapters.mock import grounded_answer
    from app.chat.prompt import PromptChunk

    chunk = PromptChunk(
        "c1",
        "pool.py",
        "File: pool.py · make_pool (lines 354-356)\ndef make_pool(size=DEFAULT_POOLSIZE):\n"
        '    """Create a pool. The default pool size is DEFAULT_POOLSIZE, which is 10."""',
    )
    reply = grounded_answer([chunk], "What is the default pool size in make_pool?")
    assert reply["sentences"]
    assert all("354" not in s["text"] and "File:" not in s["text"] for s in reply["sentences"])


TALK = RetrievedChunk(
    "t3",
    "s3",
    "Call",
    "Carla will write the release notes.",
    {"start_s": 180.0, "end_s": 240.0},
    9,
    0.9,
)
TALK_WINDOW = TranscriptWindow(
    "s3",
    "Call",
    (
        WindowPart("t2", 2, 120.0, 180.0, "Anna", "Carla will write it. Ship it on Friday.", 15),
        WindowPart("t3", 3, 180.0, 240.0, None, None, 6),
    ),
    25,
)


def mock_reply(question: str, windows: tuple[TranscriptWindow, ...]) -> list[dict[str, Any]]:
    messages = answer_messages(question, [TALK], windows=windows)
    text = MockChatAdapter().chat(messages, json_schema=ANSWER_SCHEMA).text
    sentences: list[dict[str, Any]] = json.loads(text)["sentences"]
    return sentences


def test_tb_window_fact_quotes_a_window_only_sentence_citing_c1() -> None:
    question = f"Who will write the release notes? {WINDOW_FACT_TRIGGER}"
    sentences = mock_reply(question, (TALK_WINDOW,))
    # The first window sentence is only words the chunk has; the second is window-only.
    assert sentences[-1] == {"text": "Ship it on Friday.", "chunk_ids": ["c1"]}
    assert [s["text"] for s in sentences[:-1]] == ["Carla will write the release notes."]
    # Without the trigger, or without a window, the mock never quotes a window.
    assert mock_reply("Who will write the release notes?", (TALK_WINDOW,)) == sentences[:-1]
    assert mock_reply(question, ()) == sentences[:-1]


def test_tb_seeds4_seeds_a_fourth_branch_within_the_cap() -> None:
    idea = f"Guided puffin walks on Gull Island {SEEDS4_TRIGGER} for birdwatchers"
    result = MockChatAdapter().chat(seed_messages(idea), json_schema=SEED_SCHEMA)
    data = json.loads(result.text)
    assert data["branches"] == [
        "Who it is for",
        "What your sources say about guided",
        SEEDS4_BRANCH,
        "Risks and open problems",
    ]
    assert data["title"] == "Guided puffin walks on Gull Island for…"
    parsed = parse_seed(result.text)
    assert len(parsed.branches) == branch_map.MAX_SEEDS == 4  # the map keeps all four
    assert all(SEEDS4_TRIGGER not in t for t in [parsed.title, *parsed.branches])

    plain = MockChatAdapter().chat(seed_messages("Guided puffin walks"), json_schema=SEED_SCHEMA)
    assert SEEDS4_BRANCH not in json.loads(plain.text)["branches"]
