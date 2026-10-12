"""Ideation sessions for one space: turns (streamed), answers, dismissal, finish, delete.

A turn runs in the stream's worker thread under a per-session lock, so one session never has
two turns at once. "Finish now" does not wait for a running turn: it marks the session finished
first, and the turn's results are then thrown away when it tries to store them (D-098).
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session

from app.chat.llm import ChatAdapterFactory
from app.chat.output import InvalidOutputError
from app.chat.prompt import CHUNKS_HEADING, estimate_tokens
from app.chat.schemas import Stage
from app.db.models import IdeationNode
from app.grounding.verify import Verifier
from app.ideation import brief as briefs
from app.ideation import engine
from app.ideation import map as branch_map
from app.ideation.prompt import IDEA_HEADING, SEED_SYSTEM, TURN_SYSTEM, Exchange
from app.ideation.repository import (
    IdeationStore,
    NodeNotFoundError,
    SessionNotFoundError,
    decode_recommendation,
    map_nodes,
)
from app.ideation.schemas import AnswerCreate, SessionOut
from app.retrieval.fusion import CONTEXT_CHUNKS, CONTEXT_TOKENS
from app.retrieval.service import Retrieval
from app.retrieval.store import total_tokens, usable_sources
from app.timeutil import utc_now_iso

logger = logging.getLogger("trustbook.ideation")

EventSink = Callable[[str, dict[str, Any]], None]

SESSION_NOT_FOUND = "Idea session not found."
NODE_NOT_FOUND = "Question not found."
FINISHED = "This session is finished. Press Continue to add to it."
BUSY = "The next question is still being prepared. Wait a moment."
ALREADY_ANSWERED = "This question was already answered."
NOT_ASKED = "This branch has not been asked yet."
NOTHING_TO_ACCEPT = "There is no recommended answer to accept. Type an answer or skip."
NO_BRIEF = "There is no brief yet. Press Finish now to write it."
UNUSABLE_REPLY = "The chat model's reply could not be used. Press Ask again."
SESSION_GONE = "This idea session was deleted."

# Estimates (SPEC §11): the idea, map and recent answers a turn sends besides the chunks, and the
# characters of each chunk's "[cN] Source: title (where)" line.
_TURN_CONTEXT_CHARS = 6_000
_SEED_IDEA_CHARS = 1_000
_HEADER_CHARS = 90


class IdeationError(Exception):
    """A request that cannot be done now. ``message`` is plain English; ``status`` the HTTP code."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class TurnLocks:
    """Which sessions have a turn running (keyed by space and session id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy: set[tuple[str, str]] = set()

    def busy(self, key: tuple[str, str]) -> bool:
        with self._lock:
            return key in self._busy

    def acquire(self, key: tuple[str, str]) -> bool:
        with self._lock:
            if key in self._busy:
                return False
            self._busy.add(key)
            return True

    def release(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._busy.discard(key)


TURN_LOCKS = TurnLocks()


def _exchanges(nodes: Sequence[IdeationNode]) -> list[Exchange]:
    """Answered branches with a question, oldest answer first."""
    answered = [n for n in nodes if n.status == "resolved" and n.question and n.answered_at]
    answered.sort(key=lambda n: (n.answered_at or "", n.id))
    return [
        Exchange(n.id, n.question or "", n.final_answer, n.answer_source or "user")
        for n in answered
    ]


class IdeationService:
    """Commands and the turn runner for one space's sessions."""

    def __init__(
        self,
        store: IdeationStore,
        *,
        space_id: str,
        space_folder: Path,
        retrieve: Callable[[str], Retrieval],
        chat_adapter: ChatAdapterFactory,
        refuse_threshold: float,
        verifier: Callable[[], Verifier],
        locks: TurnLocks = TURN_LOCKS,
    ) -> None:
        self._store = store
        self._space_id = space_id
        self._folder = space_folder
        self._retrieve = retrieve
        self._chat_adapter = chat_adapter
        self._refuse_threshold = refuse_threshold
        self._verifier = verifier
        self._locks = locks

    # --- helpers ---------------------------------------------------------------------------

    def _key(self, session_id: str) -> tuple[str, str]:
        return (self._space_id, session_id)

    def busy(self, session_id: str) -> bool:
        return self._locks.busy(self._key(session_id))

    def get(self, session_id: str) -> SessionOut:
        try:
            return self._store.get(session_id)
        except SessionNotFoundError as exc:
            raise IdeationError(SESSION_NOT_FOUND, 404) from exc

    def _active(self, session_id: str) -> SessionOut:
        session = self.get(session_id)
        if session.status == "finished":
            raise IdeationError(FINISHED)
        return session

    # --- turns -----------------------------------------------------------------------------

    def run_turn(self, session_id: str, emit: EventSink, *, after_answer: bool = False) -> None:
        """Seed the map when it is empty, then ask the next question; ends with ``done``.

        With ``after_answer`` and nothing left to ask, one call still adds the new branches and
        risks of the last answer (and then asks the first new branch). Raises
        :class:`IdeationError` (busy, gone) and lets adapter errors through.
        """
        key = self._key(session_id)
        if not self._locks.acquire(key):
            raise IdeationError(BUSY)
        try:
            self._run(session_id, emit, after_answer)
        except InvalidOutputError as exc:
            raise IdeationError(UNUSABLE_REPLY) from exc
        except SessionNotFoundError as exc:
            raise IdeationError(SESSION_GONE, 404) from exc
        finally:
            self._locks.release(key)

    def _run(self, session_id: str, emit: EventSink, after_answer: bool) -> None:
        stages: list[Stage] = []

        def on_stage(stage: Stage) -> None:
            if not stages or stages[-1] != stage:
                stages.append(stage)
                emit("status", {"stage": stage})

        row = self._store.session(session_id)
        if row.status == "active" and not self._store.nodes(session_id):
            parsed = engine.seed(row.idea, chat_adapter=self._chat_adapter, on_stage=on_stage)
            self._store.seed(session_id, parsed.title, parsed.branches)

        follow_up = after_answer
        for _ in range(2):  # the second round only asks a branch the last answer opened
            row = self._store.session(session_id)
            nodes = self._store.nodes(session_id)
            graph = map_nodes(nodes)
            if row.status != "active" or branch_map.current(graph) is not None:
                break
            target = branch_map.next_node(graph)
            history = _exchanges(nodes)
            last = history[-1] if history and follow_up else None
            if target is None and last is None:
                break  # complete: nothing to ask, no answer to follow up
            result = engine.turn(
                engine.TurnContext(row.idea, graph, target, history=history, last=last),
                retrieve=self._retrieve,
                chat_adapter=self._chat_adapter,
                refuse_threshold=self._refuse_threshold,
                verifier=self._verifier(),
                on_stage=on_stage,
            )
            stored = self._store.apply_turn(
                session_id,
                target.id if target else None,
                result.asked,
                result.branches,
                result.risks,
            )
            if not stored:
                logger.info("Discarded a turn of session %s (finished or changed)", session_id)
                break
            if target is not None or not result.branches:
                break
            follow_up = False
        emit("done", {"session": self._store.get(session_id).model_dump()})

    # --- commands --------------------------------------------------------------------------

    def answer(self, session_id: str, body: AnswerCreate) -> None:
        """Store an answer, an accepted recommendation or a skip for the waiting question."""
        self._active(session_id)
        if self.busy(session_id):
            raise IdeationError(BUSY)
        try:
            node = self._store.node(session_id, body.node_id)
        except NodeNotFoundError as exc:
            raise IdeationError(NODE_NOT_FOUND, 404) from exc
        if node.status == "resolved":
            raise IdeationError(ALREADY_ANSWERED)
        if node.status != "open":
            raise IdeationError(NOT_ASKED)
        if body.action == "accept":
            recommendation = decode_recommendation(node.recommendation)
            text = node.recommended_answer
            if recommendation is None or not recommendation.sentences or not text:
                raise IdeationError(NOTHING_TO_ACCEPT)
            self._store.answer(session_id, node.id, text, "accepted")
        elif body.action == "skip":
            self._store.answer(session_id, node.id, None, "skipped")
        else:
            self._store.answer(session_id, node.id, body.text, "user")

    def dismiss(self, session_id: str, node_id: str) -> SessionOut:
        self._active(session_id)
        nodes = self._store.nodes(session_id)
        if not any(n.id == node_id for n in nodes):
            raise IdeationError(NODE_NOT_FOUND, 404)
        try:
            ids = branch_map.dismiss_ids(map_nodes(nodes), node_id)
        except branch_map.DismissError as exc:
            raise IdeationError(exc.message) from exc
        self._store.dismiss(session_id, ids)
        return self.get(session_id)

    def finish(self, session_id: str) -> SessionOut:
        """Mark the session finished, then (re)write its brief from what is stored."""
        self.get(session_id)
        self._store.set_status(session_id, "finished", None)
        written_at = utc_now_iso()
        briefs.write_brief(self._folder, self.get(session_id), written_at)
        self._store.set_status(session_id, "finished", written_at)
        logger.info("Wrote the brief of idea session %s", session_id)
        return self.get(session_id)

    def reopen(self, session_id: str) -> SessionOut:
        session = self.get(session_id)
        if session.status == "finished":
            self._store.set_status(session_id, "active", None)
        return self.get(session_id)

    def delete(self, session_id: str) -> None:
        self.get(session_id)
        self._store.delete(session_id)
        folder = briefs.session_folder(self._folder, session_id)
        if folder.is_dir() and not folder.is_symlink():
            shutil.rmtree(folder)

    def brief(self, session_id: str) -> tuple[SessionOut, Path]:
        session = self.get(session_id)
        path = briefs.brief_path(self._folder, session_id)
        if not path.is_file():
            raise IdeationError(NO_BRIEF, 404)
        return session, path


def estimate(engine_: Engine) -> tuple[int, int]:
    """Approximate tokens sent per turn and for the first branches (SPEC §11).

    A turn: its instructions, the idea, map and recent answers, and the chunks within the
    context budget (none when the space has no ready, ticked source). The judge's calls for
    code sentences come on top.
    """
    with Session(engine_) as session:
        source_ids = [s.id for s in usable_sources(session)]
    count, tokens = total_tokens(engine_, source_ids) if source_ids else (0, 0)
    chunk_part = 0
    if count:
        chunk_part = estimate_tokens(
            len(CHUNKS_HEADING) + min(count, CONTEXT_CHUNKS) * _HEADER_CHARS
        ) + min(tokens, CONTEXT_TOKENS)
    turn = estimate_tokens(len(TURN_SYSTEM) + _TURN_CONTEXT_CHARS) + chunk_part
    seed = estimate_tokens(len(SEED_SYSTEM) + len(IDEA_HEADING) + _SEED_IDEA_CHARS)
    return turn, seed
