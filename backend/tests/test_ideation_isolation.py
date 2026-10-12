"""Idea briefs never become sources and never appear in chat citations (Gate 4, D-099).

(a) Static: nothing reachable from ``app.ideation`` imports ingestion, parsing, search, scraping or
    fetch code, and no ideation module imports the index, the job queue or source storage.
(b) A linked folder skips a Trustbook space folder and the data folder inside it, and any file
    that starts with the brief marker; linking a space folder itself fails with a plain reason.
(c) An uploaded brief fails at once with a plain reason; a pasted brief is refused.
(d) After a finished session whose brief holds a phrase no source has, chat asked about that
    phrase cites nothing, and the space's sources and index are unchanged.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.ideation.marker import BRIEF_MARKER, IDEA_BRIEF_REASON, is_idea_brief
from app.ingest import errors
from app.ingest.errors import IngestError
from app.ingest.folder import folder_snapshot, walk_folder
from app.parse.documents import DocumentParser
from tests.chat_helpers import parse_sse
from tests.test_api_ideation import Env, env  # noqa: F401  (the fixture)
from tests.test_grounding_boundary import (
    APP_ROOT,
    FORBIDDEN_ANYWHERE,
    FORBIDDEN_DIRECT,
    _imports,
    _matches,
    _module_file,
    _package_modules,
)

ROOT = "app.ideation"
# Ideation reads chunks only through the injected retrieval; it never writes sources or the index.
NOT_DIRECTLY = ("app.index", "app.jobs", "app.spaces.sources", "app.ingest", "app.parse")
BANNED_NAMES = frozenset(
    {"IndexedChunk", "SourceRepository", "write_snapshot", "embed_documents", "IngestService"}
)


def _reachable() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    todo = list(_package_modules(ROOT))
    while todo:
        name = todo.pop()
        if name in graph:
            continue
        path = _module_file(name)
        if path is None:
            continue
        graph[name] = _imports(name, path)
        todo.extend(i for i in graph[name] if i.startswith("app.") and i not in graph)
    return graph


def test_ideation_cannot_reach_ingestion_search_or_fetch() -> None:
    graph = _reachable()
    assert {"app.ideation.engine", "app.ideation.service", "app.ideation.brief"} <= set(graph)
    problems = [
        f"{module} imports {name}"
        for module, imported in sorted(graph.items())
        for name in sorted(imported)
        if _matches(name, FORBIDDEN_ANYWHERE)
    ]
    assert problems == []


def test_ideation_modules_never_touch_sources_or_the_index() -> None:
    problems: list[str] = []
    for module in _package_modules(ROOT):
        path = _module_file(module)
        assert path is not None
        for name in _imports(module, path):
            if _matches(name, NOT_DIRECTLY + FORBIDDEN_DIRECT):
                problems.append(f"{module} imports {name}")
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            used = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
            if used in BANNED_NAMES:
                problems.append(f"{module} uses {used}")
    assert problems == []
    assert (APP_ROOT / "ideation" / "marker.py").read_text("utf-8").count("import ") == 1


def test_marker_detection() -> None:
    assert is_idea_brief(f"{BRIEF_MARKER}\n# Idea brief")
    assert is_idea_brief(f"﻿\n  {BRIEF_MARKER}\n")
    assert not is_idea_brief(f"# Notes\n{BRIEF_MARKER}")  # only at the start
    assert not is_idea_brief("<!-- trustbook -->")


# --- linked folders --------------------------------------------------------------------------


def _space_folder(root: Path) -> None:
    space = root / "Trustbook" / "spaces" / "trips"
    (space / "ideas" / "abc").mkdir(parents=True)
    (space / "space.json").write_text("{}")
    (space / "space.db").write_bytes(b"")
    (space / "ideas" / "abc" / "brief.md").write_text(f"{BRIEF_MARKER}\n# Idea brief: Trips\n")
    (space / "sources" / "s1").mkdir(parents=True)
    (space / "sources" / "s1" / "snapshot.md").write_text("A snapshot.")
    data = root / "Trustbook"
    (data / "config").mkdir()
    (data / "config" / "settings.json").write_text('{"api_key": "x"}')
    (data / "global.db").write_bytes(b"")


def _snapshot(root: Path, tmp_path: Path) -> Any:
    return folder_snapshot(
        root,
        root.name,
        browse_root=tmp_path,
        scanner=lambda _root: [],
        documents=DocumentParser(tmp_path, lambda: 1, available=False),
        report_folder=tmp_path,
        progress=lambda _f: None,
        check_cancelled=lambda: None,
    )


def test_folder_walk_skips_trustbook_folders_and_briefs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "notes").mkdir(parents=True)
    (home / "notes" / "plan.md").write_text("# Plan\n\nBuild a bird hide.")
    (home / "notes" / "exported-brief.md").write_text(f"{BRIEF_MARKER}\n# Idea brief: Hide\n")
    _space_folder(home)
    walk = walk_folder(home)
    assert walk.files == ["notes/exported-brief.md", "notes/plan.md"]
    assert walk.skipped_trustbook == 1  # the data folder (its spaces are below it)
    snapshot = _snapshot(home, tmp_path)
    assert [f.path for f in snapshot.files] == ["notes/plan.md"]
    assert snapshot.stats["skipped_briefs"] == 1
    assert snapshot.stats["skipped_trustbook_folders"] == 1
    assert errors.skipped_briefs_warning(1) in snapshot.warnings
    assert "Idea brief" not in snapshot.markdown and "api_key" not in snapshot.markdown


def test_linking_a_space_or_data_folder_fails(tmp_path: Path) -> None:
    _space_folder(tmp_path)
    for folder in (tmp_path / "Trustbook", tmp_path / "Trustbook" / "spaces" / "trips"):
        with pytest.raises(IngestError) as caught:
            walk_folder(folder)
        assert caught.value.reason == errors.TRUSTBOOK_FOLDER[0]


# --- uploads and paste -----------------------------------------------------------------------


def test_uploaded_or_pasted_brief_is_refused(env: Env) -> None:  # noqa: F811
    sources = f"/api/spaces/{env.space_id}/sources"
    brief = f"﻿{BRIEF_MARKER}\n# Idea brief: Trips\n".encode()
    files = [
        ("files", ("brief.md", brief, "text/markdown")),
        ("files", ("notes.md", b"# Notes\n\nPlain text.", "text/markdown")),
    ]
    created = env.client.post(f"{sources}/upload", files=files).json()
    assert [s["status"] for s in created] == ["failed", "queued"]
    assert created[0]["failure_reason"] == IDEA_BRIEF_REASON
    assert created[0]["failure_next_step"] == errors.IDEA_BRIEF[1]
    folder = env.services.spaces.get(env.space_id).folder / "sources" / created[0]["id"]
    assert not any(folder.glob("original.*"))  # never stored
    pasted = env.client.post(f"{sources}/paste", json={"text": f"{BRIEF_MARKER}\nHello"})
    assert pasted.status_code == 422 and pasted.json() == {"detail": IDEA_BRIEF_REASON}


# --- chat never cites a brief ----------------------------------------------------------------

PHRASE = "Quillon zephyr quartet"  # no word of it is in any source


def test_chat_never_cites_a_brief(env: Env) -> None:  # noqa: F811
    env.sources()
    sid = env.create()["id"]
    session = env.turn(sid)
    session = env.answer(sid, session["current_node_id"], "answer", f"Hire the {PHRASE}.")
    finished = env.client.post(env.url(f"/sessions/{sid}/finish"))
    assert finished.status_code == 200
    brief = env.client.get(env.url(f"/sessions/{sid}/brief")).text
    assert PHRASE in brief
    sources_before = env.client.get(f"/api/spaces/{env.space_id}/sources").json()
    indexed_before = len(env.index._rows)

    chat = f"/api/spaces/{env.space_id}/conversations"
    cid = env.client.post(chat, json={}).json()["id"]
    response = env.client.post(f"{chat}/{cid}/messages", json={"content": f"Who is the {PHRASE}?"})
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert "sentence" not in names and names[-2:] == ["refused", "done"]
    message = events[-1][1]["message"]
    assert message["sources_used"] == [] and message["content"]["sentences"] == []
    assert env.client.get(f"/api/spaces/{env.space_id}/sources").json() == sources_before
    assert len(env.index._rows) == indexed_before
