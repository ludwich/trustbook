"""The growing branch map (SPEC §10): pure functions over a session's nodes.

Statuses: ``tentative`` (on the map, not asked yet: drawn dashed), ``open`` (its question is
waiting for an answer), ``resolved`` (answered, accepted or skipped: drawn solid), ``dismissed``
(left out of everything). Progress reads "X resolved, Y open", where open counts tentative and
open branches (D-097). A session is complete when it has branches and none is open.

Order (D-093): the server, not the model, picks the next branch. Depth first: the tentative
branches under the most recently answered one come first, then the first tentative branch in
tree pre-order (seeds in their order, children in theirs).

Caps keep the map readable and the interview finite: 3-4 seeds, at most 2 new branches per
answer, at most 3 levels, at most 30 live branches.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.chat.text import truncate

MIN_SEEDS = 3
MAX_SEEDS = 4
MAX_NEW_BRANCHES = 2
MAX_LEVELS = 3  # seeds are level 1
MAX_LIVE_NODES = 30
TITLE_CHARS = 80
MERMAID_TITLE_CHARS = 60


@dataclass(frozen=True)
class MapNode:
    """What the map needs to know about a node."""

    id: str
    parent_id: str | None
    ordinal: int
    title: str
    status: str
    answered_at: str | None = None


class DismissError(ValueError):
    """The branch cannot be dismissed. ``message`` is plain English."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


NOT_TENTATIVE = "Only branches that have not been asked yet can be dismissed."
HAS_ANSWERS = "This branch has answered questions under it, so it cannot be dismissed."


def clean_title(text: str, limit: int = TITLE_CHARS) -> str:
    """A branch or session title: one line, whitespace collapsed, cut at ``limit``."""
    return truncate(text, limit)


def _children(nodes: Iterable[MapNode]) -> dict[str | None, list[MapNode]]:
    by_id = {n.id for n in nodes}
    children: dict[str | None, list[MapNode]] = {}
    for node in nodes:
        parent = node.parent_id if node.parent_id in by_id else None
        children.setdefault(parent, []).append(node)
    for items in children.values():
        items.sort(key=lambda n: (n.ordinal, n.id))
    return children


def tree_order(nodes: Sequence[MapNode]) -> list[tuple[MapNode, int]]:
    """Every node with its depth (0 for seeds), in pre-order. A node whose parent is missing is
    treated as a seed, so no node is ever lost."""
    children = _children(nodes)
    out: list[tuple[MapNode, int]] = []
    stack: list[tuple[MapNode, int]] = [(n, 0) for n in reversed(children.get(None, []))]
    seen: set[str] = set()
    while stack:
        node, depth = stack.pop()
        if node.id in seen:  # a cycle cannot come from the API; never loop on one
            continue
        seen.add(node.id)
        out.append((node, depth))
        stack.extend((c, depth + 1) for c in reversed(children.get(node.id, [])))
    return out


def live(nodes: Iterable[MapNode]) -> list[MapNode]:
    return [n for n in nodes if n.status != "dismissed"]


def progress(nodes: Iterable[MapNode]) -> tuple[int, int]:
    """(resolved, open): open counts tentative and open branches; dismissed ones are left out."""
    resolved = opened = 0
    for node in nodes:
        if node.status == "resolved":
            resolved += 1
        elif node.status in ("tentative", "open"):
            opened += 1
    return resolved, opened


def complete(nodes: Sequence[MapNode]) -> bool:
    """The map has branches and none is open."""
    resolved, opened = progress(nodes)
    return resolved > 0 and opened == 0


def current(nodes: Sequence[MapNode]) -> MapNode | None:
    """The branch whose question waits for an answer (the first in tree order)."""
    return next((n for n, _ in tree_order(nodes) if n.status == "open"), None)


def last_answered(nodes: Iterable[MapNode]) -> MapNode | None:
    """The most recently answered branch (skips included)."""
    answered = [n for n in nodes if n.status == "resolved" and n.answered_at]
    return max(answered, key=lambda n: (n.answered_at or "", n.id), default=None)


def descendants(nodes: Sequence[MapNode], node_id: str) -> list[MapNode]:
    """The node's descendants in pre-order (not the node itself)."""
    children = _children(nodes)
    out: list[MapNode] = []
    stack = list(reversed(children.get(node_id, [])))
    seen: set[str] = {node_id}
    while stack:
        node = stack.pop()
        if node.id in seen:
            continue
        seen.add(node.id)
        out.append(node)
        stack.extend(reversed(children.get(node.id, [])))
    return out


def next_node(nodes: Sequence[MapNode]) -> MapNode | None:
    """The branch to ask about now: the open one, else the first tentative branch under the most
    recently answered one, else the first tentative branch in pre-order; None when none is left."""
    waiting = current(nodes)
    if waiting is not None:
        return waiting
    recent = last_answered(nodes)
    if recent is not None:
        under = next((n for n in descendants(nodes, recent.id) if n.status == "tentative"), None)
        if under is not None:
            return under
    return next((n for n, _ in tree_order(nodes) if n.status == "tentative"), None)


def depth_of(nodes: Sequence[MapNode], node_id: str) -> int | None:
    return next((d for n, d in tree_order(nodes) if n.id == node_id), None)


def dismissible(nodes: Sequence[MapNode], node: MapNode) -> bool:
    """Tentative, with nothing asked or answered under it."""
    if node.status != "tentative":
        return False
    return all(d.status in ("tentative", "dismissed") for d in descendants(nodes, node.id))


def dismiss_ids(nodes: Sequence[MapNode], node_id: str) -> list[str]:
    """The ids to mark dismissed: the node and its live descendants. Raises :class:`DismissError`
    when the node is not tentative or a question under it was asked."""
    node = next((n for n in nodes if n.id == node_id), None)
    if node is None or node.status != "tentative":
        raise DismissError(NOT_TENTATIVE)
    if not dismissible(nodes, node):
        raise DismissError(HAS_ANSWERS)
    return [node.id] + [d.id for d in descendants(nodes, node_id) if d.status != "dismissed"]


def _key(title: str) -> str:
    return " ".join(re.findall(r"\w+", title.casefold()))


def seed_titles(proposed: Iterable[str]) -> list[str]:
    """The first 3-4 distinct, non-empty branch titles (fewer when fewer were proposed)."""
    out: list[str] = []
    keys: set[str] = set()
    for raw in proposed:
        title = clean_title(raw)
        key = _key(title)
        if not key or key in keys:
            continue
        keys.add(key)
        out.append(title)
        if len(out) == MAX_SEEDS:
            break
    return out


def place_branches(
    nodes: Sequence[MapNode],
    proposed: Iterable[tuple[str, str | None]],
    default_parent: str | None,
) -> list[tuple[str, str | None]]:
    """New branches to add: ``(title, parent id)``, after the caps.

    A proposal whose parent is unknown or dismissed goes under ``default_parent`` (the branch just
    answered); one that would sit below the third level goes under the deepest allowed ancestor.
    Titles already on the live map (case and punctuation ignored) are left out. At most
    :data:`MAX_NEW_BRANCHES`, and never past :data:`MAX_LIVE_NODES` live branches.
    """
    alive = live(nodes)
    depth = {n.id: d for n, d in tree_order(alive)}
    parents = {n.id: n.parent_id for n in alive}
    keys = {_key(n.title) for n in alive}
    room = max(0, MAX_LIVE_NODES - len(alive))
    out: list[tuple[str, str | None]] = []
    for raw_title, raw_parent in proposed:
        if len(out) >= min(MAX_NEW_BRANCHES, room):
            break
        title = clean_title(raw_title)
        key = _key(title)
        if not key or key in keys:
            continue
        parent = raw_parent if raw_parent in depth else default_parent
        if parent is not None and parent not in depth:
            parent = None
        while parent is not None and depth[parent] >= MAX_LEVELS - 1:
            parent = parents.get(parent)
        keys.add(key)
        out.append((title, parent))
    return out


def labels(nodes: Sequence[MapNode]) -> dict[str, str]:
    """Short prompt labels for the live branches: node id → ``n1``, ``n2`` … in pre-order."""
    return {n.id: f"n{i}" for i, (n, _) in enumerate(tree_order(live(nodes)), start=1)}


# --- Mermaid ---------------------------------------------------------------------------------

_MERMAID_KEEP = re.compile(r"[^\w\s.,!?'&+%/-]", re.UNICODE)


def mermaid_text(text: str, limit: int = MERMAID_TITLE_CHARS) -> str:
    """A title safe as Mermaid mindmap node text: letters, digits, spaces and . , ! ? ' & + % / -
    only (brackets, colons and the like would be read as shapes, icons or classes)."""
    cleaned = _MERMAID_KEEP.sub(" ", text)
    cleaned = " ".join(cleaned.split()).strip(" -")
    return truncate(cleaned, limit) or "Untitled"


def mermaid_mindmap(title: str, nodes: Sequence[MapNode]) -> str:
    """A Mermaid ``mindmap`` block body (no fence): the idea as root, live branches below it."""
    lines = ["mindmap", f"  root(({mermaid_text(title)}))"]
    for node, depth in tree_order(live(nodes)):
        lines.append("  " * (depth + 2) + mermaid_text(node.title))
    return "\n".join(lines)


def by_id(nodes: Iterable[MapNode]) -> Mapping[str, MapNode]:
    return {n.id: n for n in nodes}
