"""The branch map: order, progress, completion, dismissal, caps and the Mermaid mindmap."""

from __future__ import annotations

import pytest

from app.ideation import map as m
from app.ideation.map import MapNode


def node(
    nid: str,
    parent: str | None = None,
    ordinal: int = 0,
    status: str = "tentative",
    answered_at: str | None = None,
    title: str | None = None,
) -> MapNode:
    return MapNode(nid, parent, ordinal, title or nid.upper(), status, answered_at)


def seeds(*statuses: str) -> list[MapNode]:
    return [node(f"s{i}", ordinal=i, status=s) for i, s in enumerate(statuses)]


def test_tree_order_is_pre_order_with_depth_and_keeps_orphans() -> None:
    nodes = [
        node("b", ordinal=1),
        node("a", ordinal=0),
        node("a2", "a", ordinal=1),
        node("a1", "a", ordinal=0),
        node("a1x", "a1"),
        node("orphan", "missing", ordinal=5),
    ]
    order = [(n.id, d) for n, d in m.tree_order(nodes)]
    assert order == [("a", 0), ("a1", 1), ("a1x", 2), ("a2", 1), ("b", 0), ("orphan", 0)]


def test_progress_counts_tentative_as_open_and_ignores_dismissed() -> None:
    nodes = seeds("resolved", "tentative", "open", "dismissed")
    assert m.progress(nodes) == (1, 2)
    assert not m.complete(nodes)
    assert m.complete(seeds("resolved", "dismissed", "resolved"))
    assert not m.complete([])  # nothing on the map yet
    assert not m.complete(seeds("dismissed"))


def test_next_node_prefers_the_open_branch_then_children_of_the_last_answer() -> None:
    nodes = [
        node("s0", ordinal=0, status="resolved", answered_at="2026-01-01T00:00:01Z"),
        node("s1", ordinal=1, status="resolved", answered_at="2026-01-01T00:00:02Z"),
        node("s2", ordinal=2),
        node("c0", "s0"),  # under an older answer
        node("c1", "s1"),  # under the latest answer
    ]
    assert m.next_node(nodes) == nodes[4]
    resolved_c1 = [
        *nodes[:4],
        node("c1", "s1", status="resolved", answered_at="2026-01-01T00:00:03Z"),
    ]
    # c1 answered and has no children: back to the first tentative branch in pre-order (c0).
    assert m.next_node(resolved_c1) == nodes[3]
    with_open = [*nodes, node("s3", ordinal=3, status="open")]
    assert m.next_node(with_open) == with_open[-1]
    assert m.current(with_open) == with_open[-1]
    assert m.next_node(seeds("resolved", "dismissed")) is None


def test_dismissal_cascades_and_refuses_asked_branches() -> None:
    nodes = [
        node("s0"),
        node("c", "s0"),
        node("g", "c"),
        node("s1", ordinal=1, status="open"),
        node("s2", ordinal=2),
        node("d", "s2", status="resolved", answered_at="x"),
    ]
    assert m.dismiss_ids(nodes, "s0") == ["s0", "c", "g"]
    assert m.dismissible(nodes, nodes[0])
    with pytest.raises(m.DismissError, match="not been asked"):
        m.dismiss_ids(nodes, "s1")
    with pytest.raises(m.DismissError, match="answered questions under it"):
        m.dismiss_ids(nodes, "s2")
    assert not m.dismissible(nodes, nodes[4])
    with pytest.raises(m.DismissError):
        m.dismiss_ids(nodes, "nope")


def test_seed_titles_are_distinct_and_capped() -> None:
    assert m.seed_titles(["Users", " users! ", "", "Cost", "Risks", "Timing", "Extra"]) == [
        "Users",
        "Cost",
        "Risks",
        "Timing",
    ]


def test_place_branches_applies_parent_depth_duplicate_and_count_caps() -> None:
    nodes = [
        node("s0", title="Users"),
        node("c", "s0", title="Pricing"),
        node("g", "c", title="Discounts"),  # third level
        node("x", ordinal=1, status="dismissed", title="Old"),
    ]
    placed = m.place_branches(
        nodes,
        [
            ("Pricing", "s0"),  # already on the map
            ("Volume tiers", "g"),  # would be level 4: goes under g's parent
            ("Hosting", "unknown"),  # unknown parent: under the default
            ("Third", "s0"),  # over the per-answer cap
        ],
        default_parent="s0",
    )
    assert placed == [("Volume tiers", "c"), ("Hosting", "s0")]
    assert m.place_branches(nodes, [("Under old", "x")], default_parent=None) == [
        ("Under old", None)
    ]
    full = [node(f"n{i}", title=f"T{i}") for i in range(m.MAX_LIVE_NODES - 1)]
    assert m.place_branches(full, [("A", None), ("B", None)], None) == [("A", None)]


def test_labels_cover_live_branches_in_order() -> None:
    nodes = [node("s0"), node("c", "s0"), node("s1", ordinal=1, status="dismissed")]
    assert m.labels(nodes) == {"s0": "n1", "c": "n2"}


def test_mermaid_mindmap_escapes_titles() -> None:
    nodes = [
        node("s0", title="Who (really) pays? [B2B]"),
        node("c", "s0", title="Icons ::icon(fa fa-book) and `code`"),
        node("s1", ordinal=1, title="((((("),
        node("s2", ordinal=2, status="dismissed", title="Gone"),
    ]
    text = m.mermaid_mindmap('Idea: "Gull" {island}', nodes)
    assert text == (
        "mindmap\n"
        "  root((Idea Gull island))\n"
        "    Who really pays? B2B\n"
        "      Icons icon fa fa-book and code\n"
        "    Untitled"
    )
    for bad in '()[]{}:`"':
        assert all(bad not in line for line in text.splitlines()[2:])
