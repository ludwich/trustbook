"""Nothing that is only in the code outline can verify a claim (D-077, D-079).

(a) A static ban: no grounding module, neither the citation choice, the citation numbering nor
    the reply parser, and neither of the pipeline's verification helpers mentions the outline,
    parent keys, structure records or expansions.
(b) Capture: every grounding checker premise, every judge item and prompt, and every layer 5 search
    over Smoke-like contexts holds only cited chunk text; no outline line reaches any of them.
(c) Sentences stating outline-only facts (base class, import origin, member, module constant)
    fail, with a checker that passes everything and with a word-overlap checker.
(d) The D-074 feedback says such a name is in none of the chunks.
(e) A promoted chunk is cited with its own locator only.
(f) Transcript windows (D-091) the same way: a fact only a window shows fails, citing the child;
    no window line reaches a premise, a judge item or prompt, or a layer 5 search; a citation
    carries the cited chunk's own times.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import Engine

import app
from app.capabilities.adapters.mock import MockChatAdapter
from app.chat.pipeline import Answered, Refused, answer
from app.chat.prompt import OUTLINE_HEADING, TRANSCRIPT_HEADING
from app.chat.text import content_terms
from app.db.engine import get_engine
from app.db.migrate import migrate
from app.grounding import verify
from app.grounding.code_judge import CodeJudge, JudgeItem, JudgeOutcome
from app.grounding.verify import Verifier
from app.index.memory import InMemorySpaceIndex
from app.retrieval.parents import WINDOW_PLACEHOLDER, expand
from app.retrieval.service import Retrieval, RetrievedChunk
from app.retrieval.store import FileRows, chunks_at_ordinals
from tests.chat_helpers import ScriptedChat, add_source, answer_json
from tests.parent_helpers import AllFiles, adapters_specs, add_rows, meeting_specs, turn

APP = Path(app.__file__).parent
BANNED_EXACT = frozenset({"structure", "parent_key", "OutlineBlock", "expansion", "outline"})
BANNED_PARTS = ("outline", "parent_key", "expansion", "promot")
# Transcript windows (D-091). Not the bare word "window": grounding has its own support windows.
BANNED_PARTS += ("transcript_window", "transcriptwindow", "window_tokens")
BANNED_MODULES = ("app.retrieval.parents", "app.chunking.structure")
PIPELINE_HELPERS = ("check_sentences", "failed_sentences")


def _banned(name: str) -> bool:
    lowered = name.lower()
    return name in BANNED_EXACT or any(part in lowered for part in BANNED_PARTS)


def banned_names(tree: ast.AST) -> list[str]:
    """Identifiers, attributes, imported names and modules of ``tree`` that touch parent data."""
    found: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.append(node.arg)
        elif isinstance(node, ast.alias):
            names.extend([node.name, node.asname or ""])
        elif isinstance(node, ast.ImportFrom) and node.module:
            if any(node.module.startswith(m) for m in BANNED_MODULES):
                found.append(node.module)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            names.append(node.name)
        found.extend(n for n in names if n and _banned(n))
    return found


def checked_trees() -> dict[str, ast.AST]:
    trees: dict[str, ast.AST] = {}
    for path in sorted((APP / "grounding").rglob("*.py")):
        trees[str(path.relative_to(APP))] = ast.parse(path.read_text("utf-8"))
    # Citation choice, the citation numbering (locators) and the reply parser (valid labels).
    for name in ("citation_choice.py", "citations.py", "output.py"):
        path = APP / "chat" / name
        trees[str(path.relative_to(APP))] = ast.parse(path.read_text("utf-8"))
    pipeline = ast.parse((APP / "chat" / "pipeline.py").read_text("utf-8"))
    helpers = [
        node
        for node in pipeline.body
        if isinstance(node, ast.FunctionDef) and node.name in PIPELINE_HELPERS
    ]
    assert {h.name for h in helpers} == set(PIPELINE_HELPERS)
    for helper in helpers:
        trees[f"chat/pipeline.py:{helper.name}"] = helper
    return trees


def test_grounding_code_never_mentions_the_outline_or_parent_data() -> None:
    trees = checked_trees()
    problems = {where: banned_names(tree) for where, tree in trees.items() if banned_names(tree)}
    assert problems == {}
    assert len(trees) >= 8  # grounding modules, three chat modules, two helpers


def test_the_ban_catches_each_kind_of_reference() -> None:
    probe = ast.parse(
        "from app.retrieval.parents import expand\n"
        "def f(chunk, outline=()):\n"
        "    return chunk.parent_key, chunk.structure, g(expansion=1), promoted\n"
    )
    found = banned_names(probe)
    for name in ("app.retrieval.parents", "outline", "parent_key", "structure", "expansion"):
        assert name in found
    assert "promoted" in found
    windows = ast.parse(
        "def f(r, transcript_windows=()):\n    return r.window_tokens, TranscriptWindow\n"
    )
    assert sorted(banned_names(windows)) == [
        "TranscriptWindow",
        "transcript_windows",
        "window_tokens",
    ]
    assert banned_names(ast.parse("support_window = window = 1\n")) == []


# --- Smoke-like context ---------------------------------------------------------------------


@pytest.fixture
def found(tmp_path: Path) -> Retrieval:
    """``HTTPAdapter.send`` as the top child, its class head too large to promote, so the class
    declaration, the module names and the members are only in the outline."""
    db = tmp_path / "space.db"
    migrate(db, "space")
    engine: Engine = get_engine(db)
    source = add_source(engine, "requests")
    add_rows(engine, InMemorySpaceIndex(), source, adapters_specs(head_tokens=300))
    rows = chunks_at_ordinals(engine, source, [5, 6], [source])
    context = [RetrievedChunk.from_row(rows[5], 0.9), RetrievedChunk.from_row(rows[6], 0.5)]
    expansion = expand(
        engine,
        context,
        context,
        file_rows=FileRows(engine, [source]),
        allowed_source_ids=[source],
    )
    assert expansion.outline and not expansion.promoted
    return Retrieval(chunks=context, ranked=context, source_ids=[source], outline=expansion.outline)


def outline_lines(found: Retrieval) -> list[str]:
    return [line.strip() for block in found.outline for line in block.render().splitlines()]


OUTLINE_ONLY = [
    "`HTTPAdapter` subclasses `BaseAdapter`.",  # base class (class declaration)
    "`urlparse` is imported from `.compat`.",  # import origin
    "`HTTPAdapter` also has an `init_poolmanager` method.",  # member name
    "The module defines `DEFAULT_POOLSIZE`.",  # module constant
    "HTTPAdapter subclasses BaseAdapter.",  # no backticks: layer 5 checks the names too (D-084)
]


class Recorder:
    """A grounding checker recording every (premise, hypothesis) pair."""

    def __init__(self, *, overlap: bool) -> None:
        self.overlap = overlap
        self.pairs: list[tuple[str, str]] = []

    def __call__(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.pairs.extend(pairs)
        if not self.overlap:
            return [1.0 for _ in pairs]  # passes everything: layer 5 must still fail them
        return [1.0 if set(content_terms(h)) <= set(content_terms(p)) else 0.0 for p, h in pairs]


class JudgeRecorder:
    """The real judge over the mock chat model, recording its items and prompts."""

    def __init__(self) -> None:
        self.chat = MockChatAdapter()
        self.judge = CodeJudge(lambda: self.chat)
        self.items: list[JudgeItem] = []

    def __call__(self, items: Sequence[JudgeItem]) -> list[JudgeOutcome] | None:
        self.items.extend(items)
        return self.judge(items)


def run(
    found: Retrieval, replies: Sequence[str], *, overlap: bool
) -> tuple[Answered | Refused, Recorder, JudgeRecorder, ScriptedChat]:
    checker, judge = Recorder(overlap=overlap), JudgeRecorder()
    chat = ScriptedChat(*replies)
    outcome = answer(
        "How does HTTPAdapter send a request?",
        retrieve=lambda _q: found,
        chat_adapter=lambda: chat,
        refuse_threshold=0.15,
        verifier=Verifier(checker, threshold=0.5, files=AllFiles(), code_judge=judge),
    )
    return outcome, checker, judge, chat


def assert_no_outline_text(found: Retrieval, texts: Sequence[str]) -> None:
    chunk_texts = [c.text for c in found.chunks]
    for text in texts:
        assert OUTLINE_HEADING not in text
        for line in outline_lines(found):
            if any(line in chunk for chunk in chunk_texts):
                continue  # e.g. the file path, which every chunk header holds
            assert line not in text, line


@pytest.mark.parametrize("overlap", [False, True])
def test_outline_only_facts_fail_and_reach_no_premise_or_judge_item(
    found: Retrieval, overlap: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    searched: list[str] = []
    real_identifiers, real_literals = verify.missing_identifiers, verify.missing_literals

    def identifiers(text: str, chunk_texts: Sequence[str]) -> list[str]:
        texts = list(chunk_texts)
        searched.extend(texts)
        return real_identifiers(text, texts)

    def literals(text: str, chunk_texts: Sequence[str]) -> list[str]:
        texts = list(chunk_texts)
        searched.extend(texts)
        return real_literals(text, texts)

    monkeypatch.setattr(verify, "missing_identifiers", identifiers)
    monkeypatch.setattr(verify, "missing_literals", literals)
    true = "It calls `self.get_connection(request.url)`."
    reply = answer_json((true, ["c1"]), *((s, ["c1"]) for s in OUTLINE_ONLY))
    outcome, checker, judge, _ = run(found, [reply, reply], overlap=overlap)

    # (c) every outline-only sentence fails, on both attempts (the reply is the same).
    assert isinstance(outcome, Refused) and outcome.diagnostics.regenerated
    failed = {f.text for f in outcome.diagnostics.failed_sentences}
    assert set(OUTLINE_ONLY) <= failed
    assert judge.items  # the judge was asked (confirmation or rescue) and saw chunks only
    # (b) premises, judge items, judge prompts and layer 5 searches hold chunk text only.
    chunk_texts = {c.text for c in found.chunks}
    for premise, _ in checker.pairs:
        assert all(line in "\n".join(chunk_texts) for line in premise.splitlines() if line)
    assert all(c.text in chunk_texts for _, cited in judge.items for c in cited)
    assert all(text in chunk_texts for text in searched) and searched
    prompts = [m["content"] for call in judge.chat.calls for m in call]
    assert_no_outline_text(found, [p for p, _ in checker.pairs] + prompts + searched)
    # All five name the outline's names, with or without backticks (layer 5 reports them
    # missing, D-084).
    assert outcome.diagnostics.outline_only == [5, 5]


def test_the_regeneration_feedback_says_an_outline_only_name_is_in_no_chunk(
    found: Retrieval,
) -> None:
    bad = answer_json(("The module defines `DEFAULT_POOLSIZE`.", ["c1"]))
    outcome, _, _, chat = run(found, [bad, bad], overlap=False)
    assert isinstance(outcome, Refused) and outcome.diagnostics.regenerated
    second_user = chat.calls[1]["messages"][1]["content"]
    assert "`DEFAULT_POOLSIZE` is in none of the chunks" in second_user
    assert outcome.diagnostics.outline_only == [1, 1]


def test_a_promoted_chunk_is_cited_with_its_own_locator(found: Retrieval) -> None:
    child = found.chunks[0]
    head_locator = {
        "file": child.locator["file"],
        "start_line": 32,
        "end_line": 60,
        "kind": "class",
        "symbol": "HTTPAdapter",
    }
    head = RetrievedChunk(
        "head",
        child.source_id,
        child.source_title,
        f"File: {child.locator['file']} · HTTPAdapter (lines 32-60)\n"
        "class HTTPAdapter(BaseAdapter):\n    pass",
        head_locator,
        20,
        0.0,
        referrer=child.chunk_id,
        parent_key=child.parent_key,
        ordinal=2,
        expansion="class_head",
    )
    promoted = Retrieval(
        chunks=[*found.chunks, head], ranked=found.ranked, source_ids=found.source_ids
    )
    reply = answer_json(("`HTTPAdapter` subclasses `BaseAdapter`.", ["c3"]))
    outcome, _, _, _ = run(promoted, [reply], overlap=False)
    assert isinstance(outcome, Answered), outcome
    (sentence,) = outcome.sentences
    (citation,) = sentence.citations
    assert citation.chunk_id == "head" and citation.locator == head_locator
    assert outcome.diagnostics.promoted_cited == [1, 0]
    assert [e.model_dump() for e in outcome.diagnostics.expansions] == [
        {"chunk_id": "head", "kind": "class_head", "referrer": child.chunk_id}
    ]
    assert json.dumps(citation.locator).count("::") == 0  # no parent key in a locator


# --- transcript windows (D-091) ---------------------------------------------------------------


def transcript_retrieval(
    tmp_path: Path, ordinals: Sequence[int], texts: dict[int, str]
) -> Retrieval:
    """A planning call, the turns at ``ordinals`` as the context, windows from :func:`expand`."""
    db = tmp_path / "talk.db"
    migrate(db, "space")
    engine: Engine = get_engine(db)
    source = add_source(engine, "Planning call", source_type="upload")
    specs = [
        turn(s.ordinal, texts.get(s.ordinal, s.text), speaker=s.locator["speaker"])
        for s in meeting_specs()
    ]
    add_rows(engine, InMemorySpaceIndex(), source, specs)
    rows = chunks_at_ordinals(engine, source, ordinals, [source])
    context = [RetrievedChunk.from_row(rows[o], 0.9 - n * 0.1) for n, o in enumerate(ordinals)]
    expansion = expand(
        engine, context, context, file_rows=FileRows(engine, [source]), allowed_source_ids=[source]
    )
    assert expansion.windows and not expansion.promoted and not expansion.outline
    return Retrieval(
        chunks=context,
        ranked=context,
        source_ids=[source],
        transcript_windows=expansion.windows,
    )


@pytest.fixture
def talk(tmp_path: Path) -> Retrieval:
    """The child is turn 3, "Carla will write the release notes."; turns 1, 2, 4, 5 are only in
    its window."""
    return transcript_retrieval(tmp_path, [3], {})


def window_lines(found: Retrieval) -> tuple[list[str], list[str]]:
    """Each neighbour's text as the window shows it, and each rendered window line."""
    texts: list[str] = []
    rendered: list[str] = []
    for window in found.transcript_windows:
        texts.extend(p.text for p in window.parts if p.text is not None)
        rendered.extend(line.strip() for line in window.render().splitlines()[1:])
    return texts, rendered


def run_talk(
    found: Retrieval, replies: Sequence[str], *, overlap: bool, question: str = "Who writes notes?"
) -> tuple[Answered | Refused, Recorder, JudgeRecorder, ScriptedChat]:
    checker, judge = Recorder(overlap=overlap), JudgeRecorder()
    chat = ScriptedChat(*replies)
    outcome = answer(
        question,
        retrieve=lambda _q: found,
        chat_adapter=lambda: chat,
        refuse_threshold=0.15,
        verifier=Verifier(checker, threshold=0.5, files=AllFiles(), code_judge=judge),
    )
    return outcome, checker, judge, chat


WINDOW_ONLY = [
    "The budget for the bridge project is forty thousand euros.",  # turn 1 (-2)
    "We agreed to ship the release on Friday.",  # turn 2 (-1), shares "release" with the child
    "The venue for the launch party is the harbour hall.",  # turn 4 (+1)
]


@pytest.mark.parametrize("overlap", [False, True])
def test_window_only_facts_fail_and_reach_no_premise_or_judge_item(
    talk: Retrieval, overlap: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    searched: list[str] = []
    real_identifiers = verify.missing_identifiers

    def identifiers(text: str, chunk_texts: Sequence[str]) -> list[str]:
        texts = list(chunk_texts)
        searched.extend(texts)
        return real_identifiers(text, texts)

    monkeypatch.setattr(verify, "missing_identifiers", identifiers)
    true = "Carla will write the release notes."
    reply = answer_json((true, ["c1"]), *((s, ["c1"]) for s in WINDOW_ONLY))
    outcome, checker, judge, chat = run_talk(talk, [reply, reply], overlap=overlap)

    # The model saw the window (after the chunks, under its own heading) ...
    first_user = chat.calls[0]["messages"][1]["content"]
    assert TRANSCRIPT_HEADING in first_user and WINDOW_ONLY[2] in first_user
    # ... and every window-only sentence fails, on both attempts.
    assert isinstance(outcome, Refused) and outcome.diagnostics.regenerated
    failed = {f.text for f in outcome.diagnostics.failed_sentences}
    assert set(WINDOW_ONLY) <= failed and true not in failed
    if not overlap:
        assert judge.items  # the checker passed everything: the judge's confirmation vetoed them
    chunk_texts = {c.text for c in talk.chunks}
    for premise, _ in checker.pairs:
        assert premise in chunk_texts or all(
            line in "\n".join(chunk_texts) for line in premise.splitlines() if line
        )
        assert TRANSCRIPT_HEADING not in premise
        assert not any(sentence in premise for sentence in WINDOW_ONLY)
    assert all(c.text in chunk_texts for _, cited in judge.items for c in cited)
    assert all(text in chunk_texts for text in searched) and searched
    prompts = [m["content"] for call in judge.chat.calls for m in call]
    neighbours, rendered = window_lines(talk)
    for text in [p for p, _ in checker.pairs] + searched:
        assert not any(line in text for line in neighbours + rendered)
    for text in prompts + searched:
        assert TRANSCRIPT_HEADING not in text and WINDOW_PLACEHOLDER not in text
        # A judge prompt quotes the statements (these sentences), never a window line.
        assert not any(line in text for line in rendered)
    assert outcome.diagnostics.window_only == [3, 3]
    assert outcome.diagnostics.outline_only == [0, 0]


def test_a_transcript_citation_carries_the_childs_own_times(talk: Retrieval) -> None:
    child = talk.chunks[0]
    reply = answer_json(("Carla will write the release notes.", ["c1"]))
    outcome, _, _, _ = run_talk(talk, [reply], overlap=True)
    assert isinstance(outcome, Answered), outcome
    (citation,) = outcome.sentences[0].citations
    assert citation.chunk_id == child.chunk_id and citation.locator == child.locator
    assert (citation.locator["start_s"], citation.locator["end_s"]) == (180.0, 240.0)
    (window,) = talk.transcript_windows
    spans = {(p.start_s, p.end_s) for p in window.parts if p.text is not None}
    assert (citation.locator["start_s"], citation.locator["end_s"]) not in spans
    d = outcome.diagnostics
    assert d.window_tokens == window.tokens and d.window_only == [0]
    assert [w.model_dump() for w in d.windows] == [
        {
            "source_id": window.source_id,
            "numbered": [child.chunk_id],
            "chunk_ids": list(window.chunk_ids),
            "tokens": window.tokens,
        }
    ]
    assert isinstance(d.window_fingerprint, str) and len(d.window_fingerprint) == 16


def test_a_neighbour_that_is_also_numbered_is_cited_by_its_own_locator(tmp_path: Path) -> None:
    found = transcript_retrieval(tmp_path, [3, 4], {})
    (window,) = found.transcript_windows
    assert window.numbered_ids == tuple(c.chunk_id for c in found.chunks)
    venue = "The venue for the launch party is the harbour hall."
    reply = answer_json(("Carla will write the release notes.", ["c1"]), (venue, ["c2"]))
    outcome, _, _, _ = run_talk(found, [reply], overlap=True)
    assert isinstance(outcome, Answered), outcome
    locators = [s.citations[0].locator for s in outcome.sentences]
    assert locators == [found.chunks[0].locator, found.chunks[1].locator]
    assert locators[1]["start_s"] == 240.0 and locators[1]["end_s"] == 300.0


def test_the_regeneration_feedback_says_a_window_only_name_is_in_no_chunk(
    tmp_path: Path,
) -> None:
    found = transcript_retrieval(
        tmp_path, [3], {2: "We keep the numbers in the `bridge_budget` sheet."}
    )
    assert any("`bridge_budget`" in line for line in window_lines(found)[0])
    bad = answer_json(("Carla keeps the notes in `bridge_budget`.", ["c1"]))
    outcome, _, _, chat = run_talk(found, [bad, bad], overlap=False)
    assert isinstance(outcome, Refused) and outcome.diagnostics.regenerated
    second_user = chat.calls[1]["messages"][1]["content"]
    assert "`bridge_budget` is in none of the chunks" in second_user
    assert outcome.diagnostics.window_only == [1, 1]
