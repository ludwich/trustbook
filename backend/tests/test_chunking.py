from __future__ import annotations

import itertools
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
    # Every heading starts a chunk (D-104): the text before the first heading has no section and
    # no first line; a chunk under a heading starts with its "Section:" line (D-105).
    assert drafts[0].text == "Intro line."
    assert drafts[0].locator == {"heading": None, "offset": 0}
    assert drafts[1].text == "Section: Install\n# Install\n\nRun pip install."
    assert drafts[1].locator == {"heading": "Install", "offset": md.index("# Install")}
    assert len(drafts) > 3
    for draft in drafts[2:]:
        assert draft.text.startswith("Section: Usage\n")
        assert draft.locator["heading"] == "Usage"
        assert draft.text.endswith(md[draft.start : draft.end])
    assert all("headings" not in d.locator for d in drafts)


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
        {"file": "a.py", "start_line": 1, "end_line": 2, "symbol": "f", "kind": "function"},
        {"file": "a.py", "start_line": 5, "end_line": 6, "symbol": "g", "kind": "function"},
    ]
    readme = drafts[-1]
    assert readme.locator == {
        "file": "README.md",
        "start_line": 1,
        "end_line": 2,
        "symbol": None,
        "kind": None,
        "heading": None,
    }
    assert readme.text.startswith("File: README.md (lines 1-2)\n")


# --- file documents end on whole lines (ref-near-07, D-056) ------------------------------------


def _rst_file() -> str:
    """Wrapped prose (sentences run over line ends) with some long one-line paragraphs."""
    paragraphs: list[str] = []
    for p in range(40):
        if p % 4 == 3:  # one 45-word line holding three sentences, like advanced.rst:693
            paragraphs.append(" ".join(_sentence(p * 10 + k, 15) for k in range(3)))
            continue
        words = " ".join(_sentence(p * 10 + k, 9) for k in range(5)).split()
        paragraphs.append("\n".join(" ".join(words[i : i + 12]) for i in range(0, len(words), 12)))
    return "\n\n".join(paragraphs)


def _file_snapshot(path: str, body: str) -> tuple[Snapshot, str]:
    builder = SnapshotBuilder()
    builder.text(f"# {path}\n\n")
    start, end = builder.add(body, file=path)
    snap = Snapshot(
        builder.build(), "files", builder.segments, files=[FileEntry(path, start, end, "document")]
    )
    return snap, snap.markdown


def test_file_document_chunks_hold_whole_lines() -> None:
    body = _rst_file()
    file_lines = body.split("\n")
    snap, text = _file_snapshot("docs/advanced.rst", body)
    drafts = chunk_snapshot(snap, WordCounter())
    assert len(drafts) > 3
    for d in drafts:
        claimed = "\n".join(file_lines[d.locator["start_line"] - 1 : d.locator["end_line"]])
        # The cited lines hold exactly the chunk's text: no line is claimed in part.
        assert text[d.start : d.end] == claimed.strip()
        assert d.token_count <= 800 + 3
    assert any(b.start < a.end for a, b in itertools.pairwise(drafts))  # overlap is kept
    # Without whole lines some chunk would end inside a line (and claim all of it).
    entry = snap.files[0]
    units = split_units(text, entry.start, entry.end, WordCounter(), max_tokens=800)
    ends = [g[-1].end for g in pack_units(units)]
    assert any(e < entry.end and text[e] != "\n" for e in ends)


def test_file_document_line_longer_than_the_maximum_is_still_located() -> None:
    line = " ".join(_sentence(i, 20) for i in range(120))  # one 2,400-word line
    body = "Intro line.\n\n" + line + "\n\nOutro line."
    snap, text = _file_snapshot("notes.txt", body)
    drafts = chunk_snapshot(snap, WordCounter())
    inside = [d for d in drafts if d.locator["start_line"] == d.locator["end_line"] == 3]
    assert inside  # cut inside the line: the only place left
    for d in drafts:
        claimed = "\n".join(body.split("\n")[d.locator["start_line"] - 1 : d.locator["end_line"]])
        assert text[d.start : d.end] in claimed  # every cited line range contains the text


def test_line_aligned_packing_prefers_line_and_sentence_ends() -> None:
    def unit(i: int, tokens: int, line: bool, clean: bool = False) -> Unit:
        return Unit(i * 10, i * 10 + 9, tokens, line_start=line, clean_start=clean)

    units = [
        unit(0, 40, True, True),
        unit(1, 40, False),
        unit(2, 40, True, True),  # a sentence starts this line
        unit(3, 40, False),
        unit(4, 40, True),  # a line starting mid-sentence
        unit(5, 40, False),
        unit(6, 40, False),
    ]
    groups = pack_units(units, target=200, max_tokens=300, overlap=0, align_lines=True)
    assert [[u.start // 10 for u in g] for g in groups] == [[0, 1, 2, 3], [4, 5, 6]]
    plain = pack_units(units, target=200, max_tokens=300, overlap=0)
    assert [len(g) for g in plain] == [5, 2]  # cut mid-line without alignment
    clean_first = [unit(0, 40, True, True), unit(1, 40, False), unit(2, 40, False)]
    clean_first += [unit(3, 40, True, True), unit(4, 40, True), unit(5, 40, False)]
    groups = pack_units(clean_first, target=200, max_tokens=300, overlap=0, align_lines=True)
    assert groups[0][-1].start // 10 == 2  # the clean start wins over the later line start
    overlap = pack_units(units, target=200, max_tokens=300, overlap=80, align_lines=True)
    assert overlap[1][0].line_start  # an overlap starts on a line too


def test_split_units_lines_marks_line_starts() -> None:
    text = "One two.\nThree four. Five\nsix seven.\n\n- item one\n- item two"
    units = split_units(text, 0, len(text), WordCounter(), max_tokens=800, lines=True)
    pieces = [(text[u.start : u.end], u.line_start, u.clean_start) for u in units]
    assert pieces == [
        ("One two.", True, True),
        ("Three four.", True, True),
        ("Five", False, False),
        ("six seven.", True, False),  # the line starts mid-sentence
        ("- item one", True, True),
        ("- item two", True, True),
    ]
    assert [u.tokens for u in units] == [2, 3, 1, 3, 4, 4]  # + 1 for each line break
