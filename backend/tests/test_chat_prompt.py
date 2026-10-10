from __future__ import annotations

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
from app.chat.text import content_terms, topic_of, truncate
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
    parsed, question = parse_prompt(answer_messages("What is alpha?", chunks)[1]["content"])
    assert question == "What is alpha?"
    assert [(c.label, c.source, c.text) for c in parsed] == [
        ("c1", "Doc", "Alpha line.\n\nMore alpha."),
        ("c2", "Other", "Beta."),
    ]
    parsed, question = parse_prompt(overview_messages(chunks)[1]["content"])
    assert question is None
    assert [c.text for c in parsed] == ["Alpha line.\n\nMore alpha.", "Beta."]


def test_text_helpers() -> None:
    assert content_terms("What is the Retry policy of the retry adapter?") == [
        "retry",
        "policy",
        "adapter",
    ]
    assert topic_of("How do I configure proxies in requests?") == "configure proxies requests"
    assert topic_of("What is it?") == "What is it"
    assert truncate("short", 10) == "short"
    assert truncate("one two three four five six", 12) == "one two…"
    assert estimate_tokens(0) == 0 and estimate_tokens(9) == 3
