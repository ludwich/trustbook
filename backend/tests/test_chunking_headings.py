"""Section context for document files (D-068): headings, heading paths, headers and locators."""

from __future__ import annotations

from app.chunking.dispatch import chunk_file, chunk_snapshot
from app.chunking.document import pack_units
from app.chunking.header import document_header, header_context, section_at
from app.chunking.headings import Heading, HeadingPaths, find_headings, path_text
from app.chunking.units import Unit, split_units
from app.index.base import search_text
from app.parse.snapshot import FileEntry, Snapshot, SnapshotBuilder
from tests.test_chunking import WordCounter


def titles(text: str, *, markdown: bool) -> list[tuple[str, int]]:
    return [(h.title, h.level) for h in find_headings(text, 0, len(text), markdown=markdown)]


# --- finding headings -------------------------------------------------------------------------


def test_markdown_atx_and_setext_headings() -> None:
    text = (
        "---\ntitle: front matter\n---\n\nRelease History\n===============\n\n"
        "dev\n---\n\n- a change\n\n## 2.32.0 (2024-05-20)\n\nText.\n\n### Bugfixes ###\n"
    )
    assert titles(text, markdown=True) == [
        ("Release History", 1),
        ("dev", 2),
        ("2.32.0 (2024-05-20)", 2),
        ("Bugfixes", 3),
    ]


def test_markdown_lines_that_are_not_headings() -> None:
    text = (
        "A paragraph line\nthat continues\n---\n\n"  # setext needs a blank line before the title
        "| a | b |\n|---|---|\n\n"  # table
        "- item\n---\n\n"  # a list item and a rule
        "Tilde\n~~~~~\nCode\n===\n~~~~~\n\n"  # in Markdown ~~~~~ opens a code block
        "```\n# not a heading\nCode\n===\n```\n\n"  # fenced code
        "Real\n====\n"
    )
    assert titles(text, markdown=True) == [("Real", 1)]


def test_restructuredtext_levels_follow_first_appearance() -> None:
    text = (
        "=========\nRequests\n=========\n\n"  # over- and underlined: its own style
        "Intro.\n\nInstall\n~~~~~~~\n\nText.\n\n"
        "From source\n^^^^^^^^^^^\n\nText.\n\n"
        "Usage\n~~~~~\n\n"
        "Short\n***\n\n"  # an adornment other than = / - must be as long as its title
        "   Centred\n=============\n   Centred\n=============\n"  # indented title, overlined
    )
    found = titles(text, markdown=False)
    assert found == [("Requests", 1), ("Install", 2), ("From source", 3), ("Usage", 2)]


def test_restructuredtext_overline_of_tildes_is_not_a_fence() -> None:
    text = "~~~~~~~\nPart A\n~~~~~~~\n\nText.\n\nSection\n=======\n\nMore.\n"
    assert titles(text, markdown=False) == [("Part A", 1), ("Section", 2)]


def test_heading_paths_sections_and_long_paths() -> None:
    text = "# A\n\nx\n\n## B\n\ny\n\n# C\n\nz\n"
    headings = find_headings(text, 0, len(text), markdown=True)
    paths = HeadingPaths(headings)
    assert path_text(paths.at(0)) == "A"
    assert path_text(paths.at(text.index("y"))) == "A › B"
    assert path_text(paths.at(text.index("z"))) == "C"
    assert paths.at(-1) == ()
    spans = paths.sections(text.index("x"), text.index("z") + 1)
    assert [h.title for _, h in spans if h is not None] == ["A", "B", "C"]
    deep = (Heading(0, 1, "x" * 210, 1), Heading(2, 3, "Inner", 2))
    assert path_text(deep) == "Inner"  # outer headings give way past 200 characters


def test_document_header_and_its_context() -> None:
    header = document_header("HISTORY.md", "Release History › 2.32.0 (2024-05-20)", (159, 199))
    assert header == "File: HISTORY.md · Release History › 2.32.0 (2024-05-20) (lines 159-199)"
    assert header_context(header + "\nbody", "HISTORY.md") == (
        "Release History › 2.32.0 (2024-05-20)"
    )
    assert document_header("a.md", "", (1, 2)) == "File: a.md (lines 1-2)"
    assert document_header("a.pdf", "Results", None) == "File: a.pdf · Results"
    assert header_context("File: a.md (lines 1-2)\nx", "a.md") == ""
    assert header_context("no header", "a.md") == ""
    # The section of a body line: the header's, until a heading inside the chunk takes over.
    chunk = header + "\n2.32.0 (2024-05-20)\n---\n- entry\n\n2.31.0 (2023-05-22)\n---\n- old"
    assert section_at(chunk, "HISTORY.md", 159, 161) == "Release History › 2.32.0 (2024-05-20)"
    assert section_at(chunk, "HISTORY.md", 159, 163) == "2.31.0 (2023-05-22)"
    assert section_at(chunk, "HISTORY.md", 159, 165) == "2.31.0 (2023-05-22)"
    # Full-text search keeps the heading path and drops only the line numbers (D-051).
    assert search_text(header + "\nbody") == (
        "File: HISTORY.md · Release History › 2.32.0 (2024-05-20)\nbody"
    )


# --- units and packing ------------------------------------------------------------------------


def test_an_underlined_heading_is_one_unit_and_does_not_open_a_fence() -> None:
    text = "Intro line.\n\nTitle\n~~~~~\n\nBody one.\nBody two.\n"
    headings = find_headings(text, 0, len(text), markdown=False)
    units = split_units(
        text, 0, len(text), WordCounter(), max_tokens=800, lines=True, headings=headings
    )
    pieces = [(text[u.start : u.end], u.heading, u.clean_start) for u in units]
    assert pieces == [
        ("Intro line.", None, True),
        ("Title\n~~~~~", "Title", True),
        ("Body one.", None, True),
        ("Body two.", None, True),  # still prose after the "~~~~~" line, not fenced code
    ]


def test_a_heading_inside_a_paragraph_starts_its_own_unit() -> None:
    text = "Some text.\n# Heading\nMore text."
    headings = find_headings(text, 0, len(text), markdown=True)
    units = split_units(text, 0, len(text), WordCounter(), max_tokens=800, headings=headings)
    assert [(text[u.start : u.end], u.heading) for u in units] == [
        ("Some text.", None),
        ("# Heading", "Heading"),
        ("More text.", None),
    ]


def test_a_small_section_joins_the_chunk_before_it() -> None:
    def unit(i: int, tokens: int, heading: str | None = None) -> Unit:
        return Unit(i * 10, i * 10 + 9, tokens, heading=heading, line_start=True, clean_start=True)

    units = [
        unit(0, 5, "A"),
        unit(1, 150),
        unit(2, 5, "B"),  # a small section (20 tokens)
        unit(3, 15),
        unit(4, 5, "C"),
        unit(5, 150),
    ]
    forward = pack_units(units, target=200, max_tokens=300, overlap=0)
    assert [[u.heading for u in g if u.heading] for g in forward] == [["A"], ["B", "C"]]
    back = pack_units(units, target=200, max_tokens=300, overlap=0, join_back=True)
    assert [[u.heading for u in g if u.heading] for g in back] == [["A", "B"], ["C"]]
    # Not when the chunk before would pass the target: then it joins the next one as before.
    big = [unit(0, 5, "A"), unit(1, 190), *units[2:]]
    assert pack_units(big, target=200, max_tokens=300, overlap=0, join_back=True)[1][0].heading == (
        "B"
    )
    # A small last section joins back too.
    tail = [unit(0, 5, "A"), unit(1, 150), unit(2, 5, "B"), unit(3, 15)]
    assert len(pack_units(tail, target=200, max_tokens=300, overlap=0, join_back=True)) == 1


# --- chunks of document files -----------------------------------------------------------------


def _entry_lines(i: int) -> str:
    """25 entries of 9 words."""
    return "\n".join(f"- Entry {i}.{k} changed something in the library code." for k in range(25))


def changelog() -> str:
    """HISTORY.md's shape: setext release headings, tiny and large entries."""
    parts = ["Release History\n===============\n\ndev\n---\n\n- \\[Short description.\\]\n"]
    parts.append("2.33.0 (2026-03-25)\n-------------------\n\n" + _entry_lines(0))
    parts.append("2.32.1 (2024-05-20)\n-------------------\n\n**Bugfixes**\n- Add test certs.\n")
    parts.append(
        "2.32.0 (2024-05-20)\n-------------------\n\n**Deprecations**\n"
        + _entry_lines(1)
        + "\n- Requests has officially added support for CPython 3.12 (#6503)\n"
        + _entry_lines(2)
        + "\n"
    )
    parts.append("2.31.0 (2023-05-22)\n-------------------\n\n**Security**\n" + _entry_lines(3))
    return "\n\n".join(parts)


def _files_snapshot(path: str, body: str, **where: object) -> Snapshot:
    builder = SnapshotBuilder()
    builder.text(f"# {path}\n\n")
    start, end = builder.add(body, file=path, **where)
    entry = FileEntry(path, start, end, "document")
    return Snapshot(builder.build(), "files", builder.segments, files=[entry])


def test_a_changelog_entry_carries_its_release_heading() -> None:
    body = changelog()
    file_lines = body.split("\n")
    snap = _files_snapshot("HISTORY.md", body, start_line=1, end_line=len(file_lines))
    drafts = chunk_snapshot(snap, WordCounter())
    entry_line = next(i for i, ln in enumerate(file_lines, 1) if "CPython 3.12" in ln)
    holding = [d for d in drafts if d.locator["start_line"] <= entry_line <= d.locator["end_line"]]
    assert holding
    for draft in holding:
        first = draft.text.split("\n", 1)[0]
        assert first.startswith("File: HISTORY.md · Release History › 2.32.0 (2024-05-20) ")
        assert draft.locator["heading"] == "2.32.0 (2024-05-20)"
    for draft in drafts:
        start, end = draft.locator["start_line"], draft.locator["end_line"]
        header, _, rest = draft.text.partition("\n")
        assert header.endswith(f" (lines {start}-{end})")
        assert rest == "\n".join(file_lines[start - 1 : end])  # the body is exactly its lines
        assert draft.locator["kind"] is None and draft.locator["symbol"] is None
    # The tiny 2.32.1 entry joined the chunk before it, so 2.32.0 starts a chunk of its own.
    starts = {d.locator["start_line"] for d in drafts}
    assert file_lines.index("2.32.0 (2024-05-20)") + 1 in starts
    assert any(d.locator.get("headings") for d in drafts)


def test_a_chunk_starting_in_a_section_is_named_by_it_and_keeps_indentation() -> None:
    body = "Guide\n=====\n\n" + "\n".join(
        f"    indented line {i} of a long literal block." for i in range(200)
    )
    snap = _files_snapshot("docs/guide.rst", body, start_line=1, end_line=201)
    drafts = chunk_file(snap, snap.files[0], WordCounter())
    assert len(drafts) > 1
    later = drafts[-1]
    assert later.text.startswith("File: docs/guide.rst · Guide (lines ")
    assert later.text.split("\n")[1].startswith("    indented line")  # whole first line


def test_text_before_the_first_heading_names_only_the_file() -> None:
    snap = _files_snapshot("notes.txt", "Just a note.\nAnother line.", start_line=1, end_line=2)
    (draft,) = chunk_file(snap, snap.files[0], WordCounter())
    assert draft.text == "File: notes.txt (lines 1-2)\nJust a note.\nAnother line."
    assert draft.locator["heading"] is None


def test_paged_document_files_get_the_heading_without_line_numbers() -> None:
    snap = _files_snapshot("report.pdf", "# Results\n\nRevenue rose.", page=3)
    (draft,) = chunk_file(snap, snap.files[0], WordCounter())
    assert draft.text.split("\n", 1)[0] == "File: report.pdf · Results"
    assert draft.locator == {"file": "report.pdf", "pages": [3], "heading": "Results"}


def test_web_and_pasted_documents_are_unchanged() -> None:
    """Section sources keep their behaviour: no File header, Markdown # headings only."""
    text = "# Install\n\nRun pip.\n\nUsage\n-----\n\nCall it."
    builder = SnapshotBuilder()
    builder.add(text, heading="Install")
    snap = Snapshot(builder.build(), "sections", builder.segments)
    (draft,) = chunk_snapshot(snap, WordCounter())
    assert draft.text == text
    assert draft.locator["heading"] == "Install"
