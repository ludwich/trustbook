from __future__ import annotations

import json

from app.capabilities.adapters.mock import MockChatAdapter
from app.chat.output import parse_answer, parse_overview
from app.chat.prompt import ANSWER_SCHEMA, OVERVIEW_SCHEMA, answer_messages, overview_messages
from app.retrieval.service import RetrievedChunk

CHUNKS = [
    RetrievedChunk(
        "k1",
        "s1",
        "Requests guide",
        "Requests is an HTTP library. Proxies are configured with the proxies argument.",
        {},
        20,
        0.9,
    ),
    RetrievedChunk(
        "k2", "s2", "Changelog", "Version 2.0 added proxy support for SOCKS proxies.", {}, 12, 0.5
    ),
    RetrievedChunk(
        "k3", "s1", "Requests guide", "Sessions keep cookies between calls.", {}, 8, 0.3
    ),
]


def ask(question: str) -> dict[str, object]:
    mock = MockChatAdapter()
    reply = mock.chat(answer_messages(question, CHUNKS), json_schema=ANSWER_SCHEMA)
    data: dict[str, object] = json.loads(reply.text)
    return data


def test_mock_quotes_the_best_matching_chunk_and_cites_it() -> None:
    data = ask("How are proxies configured?")
    assert data == {
        "sentences": [
            {
                "text": "Proxies are configured with the proxies argument.",
                "chunk_ids": ["c1"],
            },
            {"text": "Version 2.0 added proxy support for SOCKS proxies.", "chunk_ids": ["c2"]},
        ]
    }
    parsed = parse_answer(json.dumps(data), {"c1", "c2", "c3"})
    assert len(parsed.sentences) == 2 and parsed.dropped == []


def test_mock_is_deterministic_and_answers_with_one_sentence_when_one_chunk_matches() -> None:
    assert ask("What do sessions keep?") == ask("What do sessions keep?")
    assert ask("What do sessions keep?") == {
        "sentences": [{"text": "Sessions keep cookies between calls.", "chunk_ids": ["c3"]}]
    }


def test_mock_returns_no_sentences_without_meaningful_overlap() -> None:
    assert ask("What is the capital of France?") == {"sentences": []}
    assert ask("What is it?") == {"sentences": []}  # only stopwords


def test_mock_overview_cites_chunks_and_suggests_three_questions() -> None:
    mock = MockChatAdapter()
    reply = mock.chat(overview_messages(CHUNKS), json_schema=OVERVIEW_SCHEMA)
    parsed = parse_overview(reply.text, {"c1", "c2", "c3"})
    assert [s.chunk_ids for s in parsed.answer.sentences] == [("c1",), ("c2",), ("c3",)]
    assert parsed.answer.sentences[0].text == "Requests is an HTTP library."
    assert len(parsed.questions) == 3
    assert all(q.endswith("?") for q in parsed.questions)
    assert reply.text == mock.chat(overview_messages(CHUNKS), json_schema=OVERVIEW_SCHEMA).text


def test_mock_overview_pads_questions_for_tiny_sources() -> None:
    tiny = [RetrievedChunk("k", "s", "T", "Hi.", {}, 1, 0.1)]
    reply = MockChatAdapter().chat(overview_messages(tiny), json_schema=OVERVIEW_SCHEMA)
    assert len(parse_overview(reply.text, {"c1"}).questions) == 3


def test_mock_keeps_ping_and_echo_behaviour() -> None:
    mock = MockChatAdapter()
    assert mock.chat([{"role": "user", "content": "Reply with OK"}]).text == "OK"
    assert mock.chat([{"role": "user", "content": "hi"}]).text.startswith("Mock answer ")
