"""The idea brief: deterministic Markdown (golden), marker first, Mermaid escaping, the path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.chat.schemas import CitationOut
from app.ideation.brief import brief_path, render_brief, write_brief
from app.ideation.marker import BRIEF_MARKER, is_idea_brief
from app.ideation.schemas import (
    NodeOut,
    Progress,
    Recommendation,
    RecommendationSentence,
    Risk,
    SessionOut,
)

SID = "0123456789abcdef0123456789abcdef"
FERRY = "The ferry runs twice a day from May to September."


def node(nid: str, title: str, status: str, **extra: Any) -> NodeOut:
    values: dict[str, Any] = {
        "id": nid,
        "parent_id": None,
        "ordinal": 0,
        "depth": 0,
        "title": title,
        "status": status,
        "origin": "seed",
        "question": None,
        "recommendation": None,
        "final_answer": None,
        "answer_source": None,
        "asked_at": None,
        "answered_at": None,
        "dismissible": False,
    }
    values.update(extra)
    return NodeOut(**values)


def cited(text: str, page: int) -> RecommendationSentence:
    citation = CitationOut(
        n=1,
        chunk_id="c",
        source_id="s",
        source_title="Gull Island guide",
        locator={"pages": [page]},
    )
    return RecommendationSentence(text=text, citations=[citation], kind="cited")


def session() -> SessionOut:
    nodes = [
        node(
            "a",
            "When it runs",
            "resolved",
            question="Which months?",
            final_answer=f"{FERRY} Pack rain gear.",
            answer_source="accepted",
            answered_at="2026-10-12T10:00:02Z",
            recommendation=Recommendation(
                grounding="partly_cited",
                badge="verified",
                sentences=[
                    cited(FERRY, 3),
                    RecommendationSentence(text="Pack rain gear.", kind="suggestion"),
                ],
                removed=["The hostel is free."],
            ),
        ),
        node(
            "a1",
            "Rain plan (indoor?)",
            "tentative",
            parent_id="a",
            depth=1,
            origin="answer",
        ),
        node(
            "b",
            "Who it is for",
            "resolved",
            ordinal=1,
            question="Who books?",
            final_answer="Birdwatchers.\n# not a heading",
            answer_source="user",
            answered_at="2026-10-12T10:00:01Z",
        ),
        node(
            "c",
            "Costs",
            "resolved",
            ordinal=2,
            question="What does it cost?",
            answer_source="skipped",
            answered_at="2026-10-12T10:00:03Z",
        ),
        node("d", "Boats", "open", ordinal=3, question="Which boat?"),
        node("e", "Gone", "dismissed", ordinal=4),
    ]
    return SessionOut(
        id=SID,
        title="Bird trips",
        idea="A guided weekend trip\nto Gull Island",
        status="finished",
        progress=Progress(resolved=3, open=2),
        complete=False,
        created_at="2026-10-12T10:00:00Z",
        updated_at="2026-10-12T10:00:03Z",
        finished_at="2026-10-12T10:00:04Z",
        brief_at=None,
        nodes=nodes,
        current_node_id="d",
        risks=[Risk(text="Storms cancel the ferry", node_id="a"), Risk(text="Cost", node_id=None)],
    )


GOLDEN = f"""{BRIEF_MARKER}
# Idea brief: Bird trips

Written by Trustbook on 2026-10-12T10:00:05Z. 3 resolved, 2 open.
Idea briefs are never used as sources.

## Idea

> A guided weekend trip
> to Gull Island

## Questions and answers

### Who it is for

**Question:** Who books?

**Answer:**

> Birdwatchers.
> # not a heading

### When it runs

**Question:** Which months?

**Answer** (accepted recommendation):

> {FERRY} Pack rain gear.

### Costs

**Question:** What does it cost?

_Skipped._

## Cited anchors

- {FERRY} (*Gull Island guide*, p. 3)

## Open risks

- Storms cancel the ferry (from “When it runs”)
- Cost

## Open questions

- **Rain plan (indoor?)** (not asked yet)
- **Costs**: What does it cost? (skipped)
- **Boats**: Which boat? (waiting for an answer)

## Map

```mermaid
mindmap
  root((Bird trips))
    When it runs
      Rain plan indoor?
    Who it is for
    Costs
    Boats
```
"""


def test_brief_matches_the_golden_markdown() -> None:
    assert render_brief(session(), "2026-10-12T10:00:05Z") == GOLDEN


def test_removed_and_suggestion_sentences_are_never_anchors() -> None:
    text = render_brief(session(), "x")
    anchors = text.split("## Cited anchors")[1].split("## Open risks")[0]
    assert "The hostel is free." not in anchors and "Pack rain gear." not in anchors


def test_empty_session_brief() -> None:
    empty = session().model_copy(update={"nodes": [], "risks": [], "title": ""})
    text = render_brief(empty, "x")
    for line in (
        "# Idea brief: Untitled idea",
        "_No questions answered yet._",
        "_No recommendation cited your sources._",
        "_None recorded._",
        "_None. Every branch was answered._",
        "  root((A guided weekend trip to Gull Island))",
    ):
        assert line in text.splitlines()


def test_write_brief_path_and_marker(tmp_path: Path) -> None:
    path = write_brief(tmp_path, session(), "x")
    assert path == tmp_path / "ideas" / SID / "brief.md" == brief_path(tmp_path, SID)
    assert is_idea_brief(path.read_text("utf-8"))
    with pytest.raises(ValueError):
        brief_path(tmp_path, "../escape")
