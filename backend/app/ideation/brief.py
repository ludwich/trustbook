"""The idea brief (SPEC §10): ``ideas/<session>/brief.md``, written by "Finish now" (D-098).

Deterministic Markdown, no model call: the idea; the questions and answers in the order they
were answered (accepted recommendations and skips marked); the cited anchors (only verified
recommendation sentences, with source title and place); the open risks; the open questions
(branches not answered yet, the waiting question, and skipped questions); and the map as a
Mermaid ``mindmap`` block.

The first line is :data:`~app.ideation.marker.BRIEF_MARKER`: ingestion refuses or skips any file
that starts with it, so an exported brief never becomes a source (D-099).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.chat.prompt import describe_locator
from app.fsutil import atomic_write_text
from app.ideation import map as branch_map
from app.ideation.marker import BRIEF_MARKER
from app.ideation.schemas import NodeOut, SessionOut

IDEAS_DIR = "ideas"
BRIEF_FILE = "brief.md"
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")

NO_ANSWERS = "_No questions answered yet._"
NO_ANCHORS = "_No recommendation cited your sources._"
NO_RISKS = "_None recorded._"
NO_OPEN = "_None. Every branch was answered._"


def session_folder(space_folder: Path, session_id: str) -> Path:
    """``<space>/ideas/<session id>``; the id must be a 32-character hex string."""
    if not _SESSION_ID.match(session_id):
        raise ValueError("not a session id")
    return space_folder / IDEAS_DIR / session_id


def brief_path(space_folder: Path, session_id: str) -> Path:
    return session_folder(space_folder, session_id) / BRIEF_FILE


def _line(text: str) -> str:
    """One line of user or model text: whitespace collapsed."""
    return " ".join(text.split())


def _quote(text: str) -> list[str]:
    """Text as a Markdown blockquote (keeps its line breaks, cannot start a heading)."""
    lines = [line.rstrip() for line in text.strip().splitlines()] or [""]
    return [f"> {line}" if line else ">" for line in lines]


def _answered(nodes: list[NodeOut]) -> list[NodeOut]:
    done = [n for n in nodes if n.status == "resolved" and n.question]
    return sorted(done, key=lambda n: (n.answered_at or "", n.id))


def _qa(nodes: list[NodeOut]) -> list[str]:
    answered = _answered(nodes)
    if not answered:
        return [NO_ANSWERS]
    out: list[str] = []
    for node in answered:
        out += [f"### {_line(node.title)}", "", f"**Question:** {_line(node.question or '')}", ""]
        if node.answer_source == "skipped":
            out += ["_Skipped._", ""]
            continue
        label = (
            "**Answer** (accepted recommendation):"
            if node.answer_source == "accepted"
            else ("**Answer:**")
        )
        out += [label, "", *_quote(node.final_answer or ""), ""]
    return out[:-1]


def _anchors(nodes: list[NodeOut]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if node.status == "dismissed" or node.recommendation is None:
            continue
        for sentence in node.recommendation.sentences:
            if sentence.kind != "cited" or not sentence.citations:
                continue
            text = _line(sentence.text)
            if text in seen:
                continue
            seen.add(text)
            places: list[str] = []
            for citation in sentence.citations:
                where = describe_locator(citation.locator)
                title = _line(citation.source_title) or "Untitled"
                places.append(f"*{title}*" + (f", {where}" if where else ""))
            out.append(f"- {text} ({'; '.join(dict.fromkeys(places))})")
    return out or [NO_ANCHORS]


def _risks(session: SessionOut, titles: dict[str, str]) -> list[str]:
    out: list[str] = []
    for risk in session.risks:
        origin = titles.get(risk.node_id or "")
        out.append(f"- {_line(risk.text)}" + (f" (from “{origin}”)" if origin else ""))
    return out or [NO_RISKS]


def _open_questions(nodes: list[NodeOut]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        title = f"**{_line(node.title)}**"
        if node.status == "open":
            out.append(f"- {title}: {_line(node.question or '')} (waiting for an answer)")
        elif node.status == "tentative":
            out.append(f"- {title} (not asked yet)")
        elif node.status == "resolved" and node.answer_source == "skipped":
            out.append(f"- {title}: {_line(node.question or '')} (skipped)")
    return out or [NO_OPEN]


def render_brief(session: SessionOut, written_at: str) -> str:
    """The brief's Markdown. Only ``written_at`` varies between two renders of the same state."""
    nodes = session.nodes  # tree pre-order
    titles = {n.id: _line(n.title) for n in nodes}
    graph = [
        branch_map.MapNode(n.id, n.parent_id, n.ordinal, n.title, n.status, n.answered_at)
        for n in nodes
    ]
    progress = session.progress
    lines = [
        BRIEF_MARKER,
        f"# Idea brief: {_line(session.title) or 'Untitled idea'}",
        "",
        f"Written by Trustbook on {written_at}. {progress.resolved} resolved, "
        f"{progress.open} open.",
        "Idea briefs are never used as sources.",
        "",
        "## Idea",
        "",
        *_quote(session.idea),
        "",
        "## Questions and answers",
        "",
        *_qa(nodes),
        "",
        "## Cited anchors",
        "",
        *_anchors(nodes),
        "",
        "## Open risks",
        "",
        *_risks(session, titles),
        "",
        "## Open questions",
        "",
        *_open_questions(nodes),
        "",
        "## Map",
        "",
        "```mermaid",
        branch_map.mermaid_mindmap(session.title or session.idea, graph),
        "```",
        "",
    ]
    return "\n".join(lines)


def write_brief(space_folder: Path, session: SessionOut, written_at: str) -> Path:
    """Write (or rewrite) the brief atomically and return its path."""
    path = brief_path(space_folder, session.id)
    atomic_write_text(path, render_brief(session, written_at))
    return path
