"""Ideation sessions and nodes in ``space.db``, and their API shapes.

Every write that a turn makes (the first branches, a question with its recommendation, new
branches and risks) happens in one transaction that first checks the session is still active
and the branch still waits for it, so a turn that ends after "Finish now", a dismissal or a
delete changes nothing (D-098).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine
from sqlmodel import Session, col, delete, select

from app.chat.text import truncate
from app.db.models import IdeationNode, IdeationSession
from app.ideation import map as branch_map
from app.ideation.schemas import (
    AnswerSource,
    NodeOrigin,
    NodeOut,
    NodeStatus,
    Progress,
    Recommendation,
    Risk,
    SessionOut,
    SessionStatus,
    SessionSummary,
)
from app.timeutil import utc_now_iso

MAX_RISKS = 20
RISK_CHARS = 300
SESSION_TITLE_CHARS = 60

_risks = TypeAdapter(list[Risk])


class SessionNotFoundError(LookupError):
    """No ideation session with this id in the space."""


class NodeNotFoundError(LookupError):
    """No node with this id in the session."""


@dataclass(frozen=True)
class AskedQuestion:
    """A question for a branch, with its checked recommendation."""

    question: str
    recommendation: Recommendation
    citations: list[dict[str, Any]] = field(default_factory=list)  # [{chunk_id, verifier_score}]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def recommended_answer(self) -> str:
        return " ".join(s.text for s in self.recommendation.sentences)


def map_node(row: IdeationNode) -> branch_map.MapNode:
    return branch_map.MapNode(
        id=row.id,
        parent_id=row.parent_id,
        ordinal=row.ordinal,
        title=row.title,
        status=row.status,
        answered_at=row.answered_at,
    )


def map_nodes(rows: Sequence[IdeationNode]) -> list[branch_map.MapNode]:
    return [map_node(r) for r in rows]


def decode_recommendation(raw: str | None) -> Recommendation | None:
    if not raw:
        return None
    try:
        return Recommendation.model_validate_json(raw)
    except ValidationError:
        return None


def decode_risks(raw: str | None) -> list[Risk]:
    try:
        return _risks.validate_json(raw or "[]")
    except ValidationError:
        return []


def encode_risk(risk: Risk) -> dict[str, Any]:
    """The stored shape: the API's ``{"text", "node_id"}`` plus ``branch_source`` when known
    (it is excluded from the API, D-116)."""
    stored: dict[str, Any] = risk.model_dump()
    if risk.branch_source is not None:
        stored["branch_source"] = risk.branch_source
    return stored


_PUNCTUATION = re.compile(r"[^\w\s]")


def risk_key(text: str) -> str:
    """``text`` for duplicate detection (D-116): lower case, punctuation as spaces, whitespace
    collapsed. Exact match on this only, no fuzzy matching."""
    return " ".join(_PUNCTUATION.sub(" ", text.casefold()).split())


def _status(value: str) -> NodeStatus:
    if value in ("open", "resolved", "dismissed"):
        return value  # type: ignore[return-value]  # CHECK constraint in SQL
    return "tentative"


def _origin(value: str) -> NodeOrigin:
    return "seed" if value == "seed" else "answer"


def _source(value: str | None) -> AnswerSource | None:
    if value in ("user", "accepted", "skipped"):
        return value  # type: ignore[return-value]  # CHECK constraint in SQL
    return None


def _session_status(value: str) -> SessionStatus:
    return "finished" if value == "finished" else "active"


def nodes_out(rows: Sequence[IdeationNode]) -> list[NodeOut]:
    """The nodes in tree pre-order, with depth and whether each can be dismissed."""
    graph = map_nodes(rows)
    by_id = {r.id: r for r in rows}
    out: list[NodeOut] = []
    for node, depth in branch_map.tree_order(graph):
        row = by_id[node.id]
        out.append(
            NodeOut(
                id=row.id,
                parent_id=row.parent_id,
                ordinal=row.ordinal,
                depth=depth,
                title=row.title,
                status=_status(row.status),
                origin=_origin(row.origin),
                question=row.question,
                recommendation=decode_recommendation(row.recommendation),
                final_answer=row.final_answer,
                answer_source=_source(row.answer_source),
                asked_at=row.asked_at,
                answered_at=row.answered_at,
                dismissible=branch_map.dismissible(graph, node),
            )
        )
    return out


def summary_out(row: IdeationSession, nodes: Sequence[IdeationNode]) -> SessionSummary:
    graph = map_nodes(nodes)
    resolved, opened = branch_map.progress(graph)
    return SessionSummary(
        id=row.id,
        title=row.title,
        idea=row.idea,
        status=_session_status(row.status),
        progress=Progress(resolved=resolved, open=opened),
        complete=branch_map.complete(graph),
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
        brief_at=row.brief_at,
    )


def session_out(row: IdeationSession, nodes: Sequence[IdeationNode]) -> SessionOut:
    summary = summary_out(row, nodes)
    waiting = branch_map.current(map_nodes(nodes))
    return SessionOut(
        **summary.model_dump(),
        nodes=nodes_out(nodes),
        current_node_id=waiting.id if waiting is not None else None,
        risks=decode_risks(row.risks),
    )


def default_title(idea: str) -> str:
    """A session's title until the model names it: the idea's start."""
    return truncate(idea, SESSION_TITLE_CHARS)


class IdeationStore:
    """Every call opens its own SQLite session (safe across the request and stream threads)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reads -----------------------------------------------------------------------------

    def list_sessions(self) -> list[SessionSummary]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(IdeationSession).order_by(
                    col(IdeationSession.updated_at).desc(), col(IdeationSession.created_at).desc()
                )
            ).all()
            nodes = session.exec(select(IdeationNode)).all()
        per_session: dict[str, list[IdeationNode]] = {}
        for node in nodes:
            per_session.setdefault(node.session_id, []).append(node)
        return [summary_out(row, per_session.get(row.id, [])) for row in rows]

    def session(self, session_id: str) -> IdeationSession:
        with Session(self._engine) as session:
            row = session.get(IdeationSession, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)
            return row

    def nodes(self, session_id: str) -> list[IdeationNode]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(IdeationNode).where(IdeationNode.session_id == session_id)
                ).all()
            )

    def get(self, session_id: str) -> SessionOut:
        return session_out(self.session(session_id), self.nodes(session_id))

    def node(self, session_id: str, node_id: str) -> IdeationNode:
        with Session(self._engine) as session:
            row = session.get(IdeationNode, node_id)
            if row is None or row.session_id != session_id:
                raise NodeNotFoundError(node_id)
            return row

    # --- writes ----------------------------------------------------------------------------

    def create(self, idea: str) -> SessionOut:
        now = utc_now_iso()
        row = IdeationSession(
            id=uuid.uuid4().hex,
            idea=idea,
            title=default_title(idea),
            status="active",
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return session_out(row, [])

    @staticmethod
    def _active(session: Session, session_id: str) -> IdeationSession | None:
        row = session.get(IdeationSession, session_id)
        return row if row is not None and row.status == "active" else None

    @staticmethod
    def _add_nodes(
        session: Session,
        session_id: str,
        branches: Sequence[tuple[str, str | None]],
        origin: NodeOrigin,
        now: str,
    ) -> None:
        existing = session.exec(
            select(IdeationNode).where(IdeationNode.session_id == session_id)
        ).all()
        next_ordinal: dict[str | None, int] = {}
        for node in existing:
            key = node.parent_id
            next_ordinal[key] = max(next_ordinal.get(key, 0), node.ordinal + 1)
        for title, parent in branches:
            ordinal = next_ordinal.get(parent, 0)
            next_ordinal[parent] = ordinal + 1
            session.add(
                IdeationNode(
                    id=uuid.uuid4().hex,
                    session_id=session_id,
                    parent_id=parent,
                    ordinal=ordinal,
                    title=title,
                    status="tentative",
                    origin=origin,
                    created_at=now,
                    updated_at=now,
                )
            )

    def seed(self, session_id: str, title: str | None, seeds: Sequence[str]) -> bool:
        """Add the first branches (and the model's title), once; False when the session is no
        longer active or already has branches."""
        now = utc_now_iso()
        with Session(self._engine) as session:
            row = self._active(session, session_id)
            if row is None:
                return False
            has_nodes = session.exec(
                select(IdeationNode.id).where(IdeationNode.session_id == session_id)
            ).first()
            if has_nodes is not None:
                return False
            self._add_nodes(session, session_id, [(t, None) for t in seeds], "seed", now)
            if title:
                row.title = truncate(title, SESSION_TITLE_CHARS)
            row.updated_at = now
            session.add(row)
            session.commit()
        return True

    def apply_turn(
        self,
        session_id: str,
        node_id: str | None,
        asked: AskedQuestion | None,
        branches: Sequence[tuple[str, str | None]],
        risks: Sequence[Risk],
    ) -> bool:
        """Store a turn: the question for ``node_id`` (which must still be tentative or open),
        the new branches and the risks. False (nothing stored) when the session is finished or
        gone, or the branch was dismissed or answered meanwhile."""
        now = utc_now_iso()
        with Session(self._engine) as session:
            row = self._active(session, session_id)
            if row is None:
                return False
            if node_id is not None and asked is not None:
                node = session.get(IdeationNode, node_id)
                if node is None or node.session_id != session_id:
                    return False
                if node.status not in ("tentative", "open"):
                    return False
                node.status = "open"
                node.question = asked.question
                node.recommendation = asked.recommendation.model_dump_json()
                node.recommended_answer = asked.recommended_answer or None
                node.citations = json.dumps(asked.citations)
                node.diagnostics = json.dumps(asked.diagnostics, ensure_ascii=False)
                node.asked_at = now
                node.updated_at = now
                session.add(node)
            if branches:
                self._add_nodes(session, session_id, branches, "answer", now)
            if risks:
                current = decode_risks(row.risks)
                # The same text anywhere, or the same normalised text on the same branch (a
                # general risk's branch is None), is not added again (D-116).
                seen = {r.text.casefold() for r in current}
                keys = {(r.node_id, risk_key(r.text)) for r in current}
                for risk in risks:
                    text = truncate(risk.text, RISK_CHARS)
                    key = (risk.node_id, risk_key(text))
                    if not text or text.casefold() in seen or key in keys:
                        continue
                    if len(current) < MAX_RISKS:
                        seen.add(text.casefold())
                        keys.add(key)
                        current.append(risk.model_copy(update={"text": text}))
                row.risks = json.dumps([encode_risk(r) for r in current], ensure_ascii=False)
            row.updated_at = now
            session.add(row)
            session.commit()
        return True

    def answer(
        self, session_id: str, node_id: str, text: str | None, source: AnswerSource
    ) -> IdeationNode:
        """Resolve the open branch ``node_id`` (the caller checked it is open)."""
        now = utc_now_iso()
        with Session(self._engine) as session:
            node = session.get(IdeationNode, node_id)
            row = session.get(IdeationSession, session_id)
            if node is None or row is None or node.session_id != session_id:
                raise NodeNotFoundError(node_id)
            node.status = "resolved"
            node.final_answer = text
            node.answer_source = source
            node.answered_at = now
            node.updated_at = now
            row.updated_at = now
            session.add(node)
            session.add(row)
            session.commit()
            session.refresh(node)
            return node

    def dismiss(self, session_id: str, node_ids: Sequence[str]) -> None:
        now = utc_now_iso()
        with Session(self._engine) as session:
            for node_id in node_ids:
                node = session.get(IdeationNode, node_id)
                if node is not None and node.session_id == session_id:
                    node.status = "dismissed"
                    node.updated_at = now
                    session.add(node)
            row = session.get(IdeationSession, session_id)
            if row is not None:
                row.updated_at = now
                session.add(row)
            session.commit()

    def set_status(self, session_id: str, status: SessionStatus, brief_at: str | None) -> None:
        """Finish (``brief_at``: when the brief was written) or reopen a session."""
        now = utc_now_iso()
        with Session(self._engine) as session:
            row = session.get(IdeationSession, session_id)
            if row is None:
                raise SessionNotFoundError(session_id)
            row.status = status
            row.finished_at = now if status == "finished" else None
            if brief_at is not None:
                row.brief_at = brief_at
            row.updated_at = now
            session.add(row)
            session.commit()

    def delete(self, session_id: str) -> None:
        """Remove the session and all its nodes (one statement each; no partial tree is left)."""
        with Session(self._engine) as session:
            session.exec(delete(IdeationNode).where(col(IdeationNode.session_id) == session_id))
            session.exec(delete(IdeationSession).where(col(IdeationSession.id) == session_id))
            session.commit()
