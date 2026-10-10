"""Folder reading: thread pool, Docling lane, order, cancellation, generated files (Gate 1 G2)."""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path

import pytest

from app.ingest.folder import (
    MAX_CODE_LINE_CHARS,
    folder_snapshot,
    looks_generated,
    read_files,
    walk_folder,
)
from app.jobs.handlers import JobCancelledError
from app.parse.documents import DocumentParser, ParsedDocument


class SlowConverter:
    """A Docling stand-in that records its threads and how many conversions overlap."""

    def __init__(self) -> None:
        self.threads: set[str] = set()
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def __call__(self, path: Path) -> ParsedDocument:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.threads.add(threading.current_thread().name)
        time.sleep(0.01)
        with self._lock:
            self.active -= 1
        return ParsedDocument(pages=[(1, f"Text of {path.name}.")], used_ocr=False)


def _parser(tmp_path: Path, converter: SlowConverter) -> DocumentParser:
    return DocumentParser(tmp_path, lambda: 1, converter=converter, available=True)


def _project(root: Path, count: int = 40) -> list[str]:
    files: list[str] = []
    for i in range(count):
        if i % 10 == 3:
            name = f"docs/manual_{i:02}.pdf"
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(b"%PDF-1.4 fake")
        else:
            name = f"src/mod_{i:02}.py"
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(f"def f{i}():\n    return {i}\n")
        files.append(name)
    return sorted(files)


def test_read_files_keeps_order_and_uses_lanes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    files = _project(root)
    converter = SlowConverter()
    # Text reads finish in random order; the results must still follow ``files``.
    import app.ingest.folder as folder

    original = folder._read_one

    def jittery(path: Path, ext: str) -> folder._FileRead:
        time.sleep(random.random() / 200)
        return original(path, ext)

    monkeypatch.setattr(folder, "_read_one", jittery)
    progress: list[float] = []
    reads = read_files(
        root, files, _parser(tmp_path, converter), progress.append, lambda: None, workers=4
    )
    assert len(reads) == len(files)
    for rel, read in zip(files, reads, strict=True):
        if rel.endswith(".pdf"):
            assert read.document is not None and read.document.markdown == f"Text of {rel[5:]}."
        else:
            number = int(rel[-5:-3])
            assert read.text == f"def f{number}():\n    return {number}"  # normalised
    # Docling runs one conversion at a time, in its own lane.
    assert converter.max_active == 1
    assert len(converter.threads) == 1 and next(iter(converter.threads)).startswith("read-doc")
    assert progress == sorted(progress) and progress[-1] == 1.0


def test_read_files_cancels_between_files(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    files = _project(root, 60)
    seen: list[float] = []

    def check() -> None:
        if len(seen) >= 5:
            raise JobCancelledError

    with pytest.raises(JobCancelledError):
        read_files(root, files, _parser(tmp_path, SlowConverter()), seen.append, check, workers=2)
    assert len(seen) == 5


def test_generated_files_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "ext").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main():\n    return 1\n")
    (root / "ext" / "logo.svg").write_text("<svg>" + "M0 0 L1 1 " * 100 + "</svg>")
    (root / "ext" / "logo.ai").write_text("%PDF-1.5\n%AI12 drawing data\n" * 50)
    (root / "ext" / "art.txt.bak").write_text("%!PS-Adobe-3.0\nshowpage\n")
    (root / "src" / "bundle.js").write_text("var a=1;" * (MAX_CODE_LINE_CHARS // 8 + 10))
    (root / "notes.md").write_text("A long markdown line. " * 600)  # documents may be long
    walked = walk_folder(root).files
    assert "ext/logo.svg" not in walked and "ext/logo.ai" not in walked
    snap = folder_snapshot(
        root,
        "proj",
        browse_root=tmp_path,
        scanner=lambda _root: [],
        documents=_parser(tmp_path, SlowConverter()),
        report_folder=tmp_path,
        progress=lambda _f: None,
        check_cancelled=lambda: None,
    )
    assert [f.path for f in snap.files] == ["notes.md", "src/app.py"]
    assert snap.stats["skipped_generated"] == 1  # bundle.js; the PostScript file is not text


def test_looks_generated() -> None:
    assert not looks_generated("x = 1\n" * 1000)
    assert looks_generated("short\n" + "y" * (MAX_CODE_LINE_CHARS + 1))
