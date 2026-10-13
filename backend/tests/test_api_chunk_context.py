"""Gate 1 G6: ``GET /chunks/{id}/context`` slices on the server and returns strings and lines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.config import Paths
from app.db.engine import get_engine
from app.db.models import Chunk
from app.parse.documents import DocumentParser
from app.spaces.chunk_context import CONTEXT_CHARS, label_for, language_for
from tests.conftest import AppFactory
from tests.ingest_helpers import fake_scanner, run_jobs, services_of

EMOJI = "Launch 🚀 day 🎉 with 👩‍👩‍👧 families and 🇸🇪 flags."


@pytest.fixture
def client(make_client: AppFactory, tmp_path: Path) -> TestClient:
    client = make_client()
    tools = services_of(client).ingest.tools
    tools.scanner = fake_scanner()
    tools.documents = DocumentParser(tmp_path, lambda: 1, available=False)
    return client


@pytest.fixture
def sid(client: TestClient) -> str:
    space_id: str = client.post("/api/spaces", json={"name": "Acme"}).json()["id"]
    return space_id


def _chunks(client: TestClient, sid: str, source_id: str) -> list[Chunk]:
    record = services_of(client).spaces.get(sid)
    with Session(get_engine(record.db_path)) as session:
        query = select(Chunk).where(Chunk.source_id == source_id).order_by(col(Chunk.ordinal))
        return list(session.exec(query).all())


def _snapshot(client: TestClient, sid: str, source_id: str) -> str:
    markdown: str = client.get(f"/api/spaces/{sid}/sources/{source_id}/snapshot").json()["markdown"]
    return markdown


def _context(client: TestClient, sid: str, chunk_id: str) -> dict[str, Any]:
    response = client.get(f"/api/spaces/{sid}/chunks/{chunk_id}/context")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


# --- text view ---------------------------------------------------------------------------------


def _paste_long(client: TestClient, sid: str) -> str:
    paragraphs = [f"# Notes\n\n{EMOJI} " * 3]
    paragraphs += [
        f"## Part {i}\n\n" + " ".join(f"Part {i} sentence {j} about pumps." for j in range(40))
        for i in range(12)
    ]
    response = client.post(
        f"/api/spaces/{sid}/sources/paste", json={"text": "\n\n".join(paragraphs)}
    )
    source_id: str = response.json()["id"]
    run_jobs(services_of(client))
    return source_id


def test_text_context_is_exact_even_after_emoji(client: TestClient, sid: str) -> None:
    source_id = _paste_long(client, sid)
    markdown = _snapshot(client, sid, source_id)
    chunks = _chunks(client, sid, source_id)
    assert len(chunks) >= 3
    for chunk in chunks:
        data = _context(client, sid, chunk.id)
        assert (data["view"], data["source_type"]) == ("text", "paste")
        assert "code" not in data
        assert data["chunk"]["id"] == chunk.id
        text = data["text"]
        assert text["highlight"] == markdown[chunk.start_offset : chunk.end_offset]
        assert text["before"] + text["highlight"] + text["after"] in markdown
        assert markdown.index(text["before"] + text["highlight"]) + len(text["before"]) == (
            chunk.start_offset
        )
        assert len(text["before"]) <= CONTEXT_CHARS and len(text["after"]) <= CONTEXT_CHARS
    # A later chunk: the emoji are in the snapshot before it, the highlight is still exact.
    later = chunks[2]
    assert "🚀" in markdown[: later.start_offset]
    text = _context(client, sid, later.id)["text"]
    assert text["label"] is None or text["label"].startswith("Section: ")
    # The windows end on line or word boundaries, not mid-word.
    if len(text["before"]) < later.start_offset:
        cut = markdown[later.start_offset - len(text["before"]) - 1]
        assert cut in "\n "
    end = later.end_offset + len(text["after"])
    if end < len(markdown):
        assert markdown[end] in "\n "


def test_text_context_without_a_matching_snapshot_shows_the_chunk(
    client: TestClient, sid: str
) -> None:
    source_id = _paste_long(client, sid)
    chunk = _chunks(client, sid, source_id)[1]
    folder = services_of(client).ingest.repo(sid).folder(source_id)
    (folder / "snapshot.md").write_text("rewritten by a refresh", "utf-8")
    text = _context(client, sid, chunk.id)["text"]
    # The stored text without its "Section:" line, which the label names (D-105).
    body = chunk.text.partition("\n")[2]
    assert chunk.text.startswith("Section: ")
    assert (text["before"], text["highlight"], text["after"]) == ("", body, "")
    (folder / "snapshot.md").unlink()
    assert _context(client, sid, chunk.id)["text"]["highlight"] == body


def test_unknown_chunk_is_404(client: TestClient, sid: str) -> None:
    response = client.get(f"/api/spaces/{sid}/chunks/nope/context")
    assert (response.status_code, response.json()) == (404, {"detail": "Passage not found."})


# --- code view ---------------------------------------------------------------------------------


def _code_project(paths: Paths, long_lines: int = 0) -> None:
    root = paths.browse_root / "work" / "proj"
    (root / "src").mkdir(parents=True)
    # Sorted before api.py: emoji in an earlier file shift every later offset in JS.
    (root / "src" / "aaa.py").write_text(f'BANNER = "{EMOJI}"\n')
    blocks = [f"# {EMOJI}\nDEFAULT_POOLSIZE = 10\n"]
    blocks += [
        f"def handler_{i}(request):\n" + "".join(f"    step_{k} = {k}\n" for k in range(30))
        for i in range(8)
    ]
    (root / "src" / "api.py").write_text("\n\n".join(blocks))
    if long_lines:
        body = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(long_lines // 3))
        (root / "src" / "long.py").write_text(body)
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("c" * 40 + "\n")


def _add_folder(client: TestClient, sid: str) -> str:
    response = client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "work/proj"})
    source_id: str = response.json()["id"]
    run_jobs(services_of(client))
    return source_id


def test_code_context_returns_the_files_lines(client: TestClient, sid: str, paths: Paths) -> None:
    _code_project(paths)
    source_id = _add_folder(client, sid)
    file_lines = (paths.browse_root / "work/proj/src/api.py").read_text().splitlines()
    chunks = [c for c in _chunks(client, sid, source_id) if '"src/api.py"' in c.locator]
    assert len(chunks) >= 2
    for chunk in chunks:
        data = _context(client, sid, chunk.id)
        assert data["view"] == "code" and "text" not in data
        code = data["code"]
        locator = data["chunk"]["locator"]
        assert (code["file"], code["commit"], code["language"]) == (
            "src/api.py",
            "c" * 40,
            "python",
        )
        assert (code["first_line"], code["lines"]) == (1, file_lines)
        assert code["highlight"] == [locator["start_line"], locator["end_line"]]
        first, last = code["highlight"]
        assert "\n".join(code["lines"][first - 1 : last]) == chunk.text.partition("\n")[2]


def test_long_files_return_a_window_around_the_chunk(
    client: TestClient, sid: str, paths: Paths
) -> None:
    _code_project(paths, long_lines=3_000)
    source_id = _add_folder(client, sid)
    chunks = [c for c in _chunks(client, sid, source_id) if '"src/long.py"' in c.locator]
    middle = chunks[len(chunks) // 2]
    code = _context(client, sid, middle.id)["code"]
    first, last = code["highlight"]
    assert first > 1_000
    assert code["first_line"] == first - 200
    assert code["first_line"] + len(code["lines"]) - 1 == last + 200
    offset = code["first_line"]
    assert (
        "\n".join(code["lines"][first - offset : last - offset + 1])
        == (middle.text.partition("\n")[2])
    )
    head = _context(client, sid, chunks[0].id)["code"]
    assert head["first_line"] == 1


def test_code_context_falls_back_to_the_chunk_when_the_snapshot_changed(
    client: TestClient, sid: str, paths: Paths
) -> None:
    _code_project(paths)
    source_id = _add_folder(client, sid)
    chunk = next(c for c in _chunks(client, sid, source_id) if '"src/api.py"' in c.locator)
    folder = services_of(client).ingest.repo(sid).folder(source_id)
    (folder / "snapshot.md").write_text("changed", "utf-8")
    code = _context(client, sid, chunk.id)["code"]
    body = chunk.text.partition("\n")[2].split("\n")
    assert code["lines"] == body
    assert code["highlight"] == [code["first_line"], code["first_line"] + len(body) - 1]


# --- helpers -----------------------------------------------------------------------------------


def test_labels_and_languages() -> None:
    assert label_for({"pages": [3]}) == "Page 3"
    assert label_for({"pages": [4, 3]}) == "Pages 3–4"
    assert label_for({"heading": "Install", "offset": 12}) == "Section: Install"
    assert label_for({"heading": None, "offset": 0}) is None
    assert label_for({"start_s": 62.4, "end_s": 105.0, "speaker": None}) == "01:02–01:45"
    assert label_for({"start_s": 3725.0, "end_s": 3725.0}) == "1:02:05"
    assert label_for({"file": "docs/guide.pdf", "pages": [2]}) == "docs/guide.pdf · Page 2"
    assert label_for({}) is None
    assert language_for("src/requests/api.py") == "python"
    assert language_for("app/Page.TSX") == "tsx"
    assert language_for("Dockerfile") == "dockerfile"
    assert language_for("data/x.unknown") is None and language_for("LICENSE") is None
