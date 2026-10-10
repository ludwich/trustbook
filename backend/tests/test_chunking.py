from __future__ import annotations

from pathlib import Path

from app.chunking.code import chunk_code
from app.chunking.dispatch import chunk_snapshot
from app.chunking.document import chunk_document, pack_units
from app.chunking.tokens import CharEstimate, load_token_counter
from app.chunking.transcript import chunk_transcript
from app.chunking.units import Unit, split_units
from app.parse.snapshot import FileEntry, Segment, Snapshot, SnapshotBuilder
from app.parse.text import text_snapshot


class WordCounter:
    """One token per whitespace-separated word (easy to reason about in tests)."""

    def count(self, text: str) -> int:
        return len(text.split())


def _sentence(i: int, words: int = 10) -> str:
    return " ".join(["word"] * (words - 1) + [f"end{i}."])


def test_char_estimate_and_missing_tokenizer(tmp_path: Path) -> None:
    assert CharEstimate().count("abcdefgh") == 2
    assert CharEstimate().count("abcdefghi") == 3
    counter = load_token_counter(tmp_path / "missing" / "tokenizer.json")
    assert isinstance(counter, CharEstimate)


def test_units_split_sentences_headings_and_tables() -> None:
    text = "# Title\n\nFirst one. Second one e.g. here. Third!\n\n| a | b |\n| 1 | 2 |"
    units = split_units(text, 0, len(text), WordCounter(), max_tokens=800)
    pieces = [text[u.start : u.end] for u in units]
    assert pieces == [
        "# Title",
        "First one.",
        "Second one e.g. here.",
        "Third!",
        "| a | b |",
        "| 1 | 2 |",
    ]
    assert units[0].heading == "Title"
    assert all(u.heading is None for u in units[1:])


def test_units_hard_split_overlong_sentence() -> None:
    text = " ".join(f"w{i}" for i in range(50))
    units = split_units(text, 0, len(text), WordCounter(), max_tokens=12)
    assert len(units) > 1
    assert all(u.tokens <= 12 for u in units)
    assert " ".join(text[u.start : u.end] for u in units) == text


def test_pack_respects_target_overlap_and_headings() -> None:
    units = [Unit(i * 10, i * 10 + 9, 100) for i in range(12)]
    groups = pack_units(units, target=500, max_tokens=800, overlap=80)
    assert all(sum(u.tokens for u in g) <= 800 for g in groups)
    # 80-token overlap cannot fit a 100-token sentence, so no overlap here.
    assert groups[0][-1] != groups[1][0]

    small = [Unit(i * 10, i * 10 + 9, 40) for i in range(30)]
    groups = pack_units(small, target=500, max_tokens=800, overlap=80)
    assert groups[1][0] == groups[0][-2]  # two 40-token sentences carried over
    assert all(sum(u.tokens for u in g) <= 500 for g in groups)

    with_heading = [
        Unit(0, 1, 150),
        Unit(2, 3, 5, heading="Next"),
        Unit(4, 5, 10),
    ]
    groups = pack_units(with_heading, target=500, max_tokens=800, overlap=80)
    assert [len(g) for g in groups] == [1, 2]
    tiny_section = [Unit(0, 1, 20), Unit(2, 3, 5, heading="Next"), Unit(4, 5, 10)]
    assert len(pack_units(tiny_section)) == 1  # small sections merge


def test_document_chunks_end_at_sentences_and_overlap() -> None:
    text = " ".join(_sentence(i) for i in range(200))  # 2,000 words
    drafts = chunk_document(
        text, 0, len(text), WordCounter(), lambda s, e: {"offset": s}, target=100, overlap=20
    )
    assert len(drafts) > 10
    for draft in drafts:
        assert draft.text == text[draft.start : draft.end]
        assert draft.text.endswith(".")
        assert draft.token_count <= 120
    assert drafts[1].start < drafts[0].end  # overlap
    assert drafts[-1].end == len(text)


def test_snapshot_locators_pages_and_sections() -> None:
    builder = SnapshotBuilder()
    builder.add("Page one text. " * 5, page=1)
    builder.separate()
    builder.add("Page two text. " * 5, page=2)
    snap = Snapshot(builder.build(), "pages", builder.segments)
    drafts = chunk_snapshot(snap, WordCounter())
    assert drafts[0].locator == {"pages": [1, 2]}

    md = "Intro line.\n\n# Install\n\nRun pip install.\n\n# Usage\n\n" + " ".join(
        _sentence(i) for i in range(100)
    )
    drafts = chunk_snapshot(text_snapshot(md), WordCounter())
    # Named by the section holding most of it (D-053), listing every section it spans.
    assert drafts[0].locator["heading"] == "Usage"
    assert drafts[0].locator["headings"] == ["Install", "Usage"]
    assert drafts[0].locator["offset"] == 0
    assert any(d.locator["heading"] == "Usage" for d in drafts)


def test_transcript_chunks_group_turns_and_never_split_them() -> None:
    builder = SnapshotBuilder()
    for i in range(40):
        builder.separate()
        text = f"Turn {i} says a few words here and stops."
        builder.add(text, start_s=float(i * 5), end_s=float(i * 5 + 5), speaker="Anna")
    snap = Snapshot(builder.build(), "timestamps", builder.segments)
    drafts = chunk_transcript(snap.markdown, snap.segments, WordCounter(), target=50)
    assert len(drafts) > 3
    assert drafts[0].locator == {
        "start_s": 0.0,
        "end_s": drafts[0].locator["end_s"],
        "speaker": "Anna",
    }
    starts = [s.start for s in snap.segments]
    ends = [s.end for s in snap.segments]
    for draft in drafts:
        assert draft.start in starts and draft.end in ends
    assert drafts[-1].locator["end_s"] == 200.0


def test_transcript_without_punctuation_closes_at_soft_limit() -> None:
    segments: list[Segment] = []
    parts: list[str] = []
    offset = 0
    for i in range(100):
        line = "words without any punctuation at all"
        segments.append(Segment(offset, offset + len(line), start_s=float(i), end_s=float(i + 1)))
        parts.append(line)
        offset += len(line) + 1
    text = "\n".join(parts)
    drafts = chunk_transcript(text, segments, WordCounter(), target=50, soft_max=60)
    assert all(d.token_count <= 66 for d in drafts)


def test_code_chunker_blocks_lines_and_prefix() -> None:
    lines = []
    for i in range(30):
        lines += [f"procedure Proc{i}()", "begin", f"    DoThing({i});", "end;", ""]
    code = "\n".join(lines)
    drafts = chunk_code(code, 0, len(code), "src/Codeunit.al", WordCounter(), target=40)
    assert len(drafts) > 3
    first = drafts[0]
    assert first.locator["file"] == "src/Codeunit.al"
    assert first.locator["start_line"] == 1
    assert first.text.startswith("File: src/Codeunit.al (lines 1-")
    body = code[first.start : first.end]
    assert body.splitlines()[0] == "procedure Proc0()"
    assert len(body.splitlines()) == first.locator["end_line"] - first.locator["start_line"] + 1
    for draft in drafts:
        start_line = code[: draft.start].count("\n") + 1
        assert start_line == draft.locator["start_line"]


def test_code_chunker_splits_huge_block_between_lines() -> None:
    code = "\n".join(f"x{i} = {i}" for i in range(500))  # one block, ~1,500 words
    drafts = chunk_code(code, 0, len(code), "big.py", WordCounter(), max_tokens=200)
    assert len(drafts) >= 7
    assert all(d.token_count <= 220 for d in drafts)


def test_files_snapshot_uses_code_and_document_chunkers() -> None:
    builder = SnapshotBuilder()
    builder.text("# a.py\n\n")
    s1, e1 = builder.add("def f():\n    return 1\n\n\ndef g():\n    return 2", file="a.py")
    builder.separate()
    builder.text("# README.md\n\n")
    s2, e2 = builder.add("Line one.\nLine two is here.", file="README.md")
    snap = Snapshot(
        builder.build(),
        "files",
        builder.segments,
        files=[FileEntry("a.py", s1, e1, "code"), FileEntry("README.md", s2, e2, "document")],
    )
    drafts = chunk_snapshot(snap, WordCounter())
    # .py goes to tree-sitter (one chunk per function); README.md to the document chunker
    assert [d.locator for d in drafts[:2]] == [
        {"file": "a.py", "start_line": 1, "end_line": 2, "symbol": "f"},
        {"file": "a.py", "start_line": 5, "end_line": 6, "symbol": "g"},
    ]
    readme = drafts[-1]
    assert readme.locator == {
        "file": "README.md",
        "start_line": 1,
        "end_line": 2,
        "symbol": None,
    }
    assert readme.text.startswith("File: README.md\n")
