from __future__ import annotations

import pytest

from app.chat.prompt import (
    ANSWER_SCHEMA,
    ANSWER_SYSTEM,
    OVERVIEW_SCHEMA,
    answer_messages,
    describe_locator,
    estimate_tokens,
    labelled,
    overview_messages,
    parse_prompt,
)
from app.chat.text import content_terms, question_topic, truncate
from app.retrieval.service import RetrievedChunk


def chunk(cid: str, text: str, title: str = "Doc", **locator: object) -> RetrievedChunk:
    return RetrievedChunk(cid, f"src-{title}", title, text, dict(locator), 10, 0.9)


def test_answer_prompt_numbers_chunks_and_states_the_rules() -> None:
    chunks = [
        chunk("x1", "First text.", "Guide", pages=[3, 4]),
        chunk("x2", "Second text.", "Repo", file="api.py", start_line=12, end_line=58),
    ]
    messages = answer_messages("  How does\nit work? ", chunks)
    assert [m["role"] for m in messages] == ["system", "user"]
    system, user = messages[0]["content"], messages[1]["content"]
    assert system == ANSWER_SYSTEM
    assert "Answer only from the numbered chunks" in system
    assert "Do not use outside knowledge" in system
    assert '{"sentences": []}' in system
    assert "[c1] Source: Guide (p. 3–4)\nFirst text." in user
    assert "[c2] Source: Repo (api.py, lines 12–58)\nSecond text." in user
    assert user.endswith("\n\nQuestion: How does it work?")
    assert list(labelled(chunks)) == ["c1", "c2"]
    assert labelled(chunks)["c2"].chunk_id == "x2"


def test_schemas_require_sentences_with_chunk_ids() -> None:
    item = ANSWER_SCHEMA["properties"]["sentences"]["items"]
    assert item["required"] == ["text", "chunk_ids"]
    assert ANSWER_SCHEMA["additionalProperties"] is False
    assert OVERVIEW_SCHEMA["required"] == ["sentences", "questions"]


def test_describe_locator_variants() -> None:
    assert describe_locator({"pages": [7]}) == "p. 7"
    assert describe_locator({"start_s": 61.2, "end_s": 95.0, "speaker": None}) == "1:01–1:35"
    assert describe_locator({"start_s": 3700, "end_s": 3725, "speaker": "Ann"}) == (
        "1:01:40–1:02:05, Ann"
    )
    assert describe_locator({"heading": "Install", "offset": 3}) == "section “Install”"
    assert describe_locator({"file": "a.py", "start_line": 4, "end_line": 4}) == "a.py, line 4"
    assert describe_locator({"file": "a.py"}) == "a.py"
    assert describe_locator({}) == ""


def test_parse_prompt_round_trips_chunks_and_question() -> None:
    chunks = [chunk("x1", "Alpha line.\n\nMore alpha."), chunk("x2", "Beta.", "Other")]
    parts = parse_prompt(answer_messages("What is alpha?", chunks)[1]["content"])
    assert parts.question == "What is alpha?" and parts.outline == ""
    assert [(c.label, c.source, c.text) for c in parts.chunks] == [
        ("c1", "Doc", "Alpha line.\n\nMore alpha."),
        ("c2", "Other", "Beta."),
    ]
    parts = parse_prompt(overview_messages(chunks)[1]["content"])
    assert parts.question is None
    assert [c.text for c in parts.chunks] == ["Alpha line.\n\nMore alpha.", "Beta."]


def test_text_helpers() -> None:
    assert content_terms("What is the Retry policy of the retry adapter?") == [
        "retry",
        "policy",
        "adapter",
    ]
    assert question_topic("How do I configure proxies in requests?") == (
        "how to configure proxies in requests"
    )
    assert question_topic("What is it?") == "it"
    assert question_topic("  ?? ") == ""
    assert truncate("short", 10) == "short"
    assert truncate("one two three four five six", 12) == "one two…"
    assert estimate_tokens(0) == 0 and estimate_tokens(9) == 3


@pytest.mark.parametrize(
    ("question", "topic"),
    [
        ("How do I set a base URL for Session calls?", "how to set a base URL for Session calls"),
        ("how can we mount a custom adapter", "how to mount a custom adapter"),
        ("What is the default pool size?", "the default pool size"),
        ("What's the default timeout?", "the default timeout"),
        ("Can you tell me how do I stream a download?", "how to stream a download"),
        ("Could you explain HTTPAdapter retries?", "HTTPAdapter retries"),
        ("Does requests support HTTP/2?", "requests support HTTP/2"),
        ("Is there a way to retry failed requests?", "a way to retry failed requests"),
        ("Please, describe the Session object", "the Session object"),
        ("Why does Session.send raise?", "why does Session.send raise"),
        ("Retry policy", "retry policy"),
        ("JSON body encoding?", "JSON body encoding"),
        ("Is", "is"),
    ],
)
def test_question_topic_keeps_the_users_words(question: str, topic: str) -> None:
    assert question_topic(question) == topic


def test_question_topic_is_cut_on_a_word_boundary() -> None:
    long = "Why " + " ".join(f"word{i}" for i in range(40)) + "?"
    topic = question_topic(long)
    assert len(topic) <= 80 and topic.endswith("…")
    assert topic.startswith("why word0 word1") and "word3…" not in topic[:-1]
    assert topic[:-1].split(" ")[-1].startswith("word")
