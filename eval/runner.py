#!/usr/bin/env python3
"""Trustbook evaluation runner (SPEC §8 layer 6, §13).

Asks every question in one or more YAML question files (format: ``eval/format.md``) against a
space of a running Trustbook, then prints a Markdown report ready to paste into ``docs/GATES.md``:
citation precision, correct-refusal rate, ``must_contain`` hit rate, false refusals (overall, per
category and on code questions), answer latency p50/p95, judge calls and time per answer, answers
over 60 s and regenerations (D-080) and, with ``--code-root``, whether every cited file/line range
exists in a checkout of the corpus.

Usage (from the repo root, with the backend's virtualenv)::

    uv run --project backend python eval/runner.py --base-url http://localhost:8484 \\
        --space "requests smoke" --questions eval/smoke/questions.yaml \\
        --setup eval/smoke/corpus.yaml --report eval-report.md

With ``--report out.md`` the runner also writes ``out.json`` next to it: per-question outcome,
top reranker score, sentence verifier scores (from the answer's ``diagnostics``) and the threshold
sweep (see ``eval/format.md``, "Threshold sweep"). Nothing is written anywhere else, so a private
question set never ends up in the repository unless the report path is inside it.

Exit codes: 0 = report written; 1 = a ``--min-*`` / ``--max-*`` target was not met, or (with
``--setup``) an ingest job did not end as expected; 2 = the API is unreachable or the input
(questions, corpus, space, code root, chat override) is invalid; 130 = interrupted. A job that
ended before the run is noted, not judged; ``--refresh`` re-ingests the sources already in the
space so that their jobs run on, and are checked against, the running image.

Generation: every question is sent with ``"generation": {"temperature": 0, "seed": 42}`` so runs
are comparable (``--temperature``, ``--seed``); ``--app-default`` sends nothing (a real-use run with
the app's own settings). ``--chat-endpoint`` / ``--chat-model`` switch the chat capability for the
run and restore it afterwards, also on an error or Ctrl-C. ``--compare A.json B.json`` lists the
per-question differences between two sidecars and exits.

Only ``httpx``, ``PyYAML`` and the CLI's API client (``cli/``, package ``trustbook-cli``, installed
with the backend and in the image; D-087) are used. The runner talks to the API contract in
``docs/ARCHITECTURE.md`` ("Phase 1: sources, index, retrieval and chat").
"""

from __future__ import annotations

import argparse
import json
import math
import re
import signal
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, TextIO
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml
from trustbook_cli.client import (
    ApiUnreachableError,
    TrustbookError,
    find_space,
    iter_sse,
    norm_text,
    request_json,
    sse_lines,
)
from trustbook_cli.client import (
    SseEvent as SseEvent,  # re-exported (tests)
)

QUESTION_TYPES = ("answerable", "unanswerable")
CATEGORIES = ("docs", "code", "multi", "unanswerable")
FALSE_REFUSAL_GROUPS = ("docs", "code", "multi", "other")
# p95 answer latency above this is flagged in the report even without --max-p95 (Gate 3).
P95_FLAG_S = 60.0
# A file locator is a code file unless its extension marks documentation or plain text.
DOC_EXTENSIONS = frozenset(
    "md markdown mdx rst txt text adoc asciidoc html htm pdf docx pptx xlsx odt rtf".split()
)
SOURCE_DONE = ("ready", "failed")
JOB_DONE = ("done", "failed", "cancelled")
# Eval runs ask with a fixed generation override so runs can be compared (D-055). The app's own
# default (SPEC: temperature 0.2, no seed) applies only with --app-default.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 42
SEED_CAVEAT = (
    "Some endpoints do not honour a seed (llama-server with several parallel slots or speculative "
    "decoding, most hosted APIs), so answers can still differ between runs."
)
EXIT_OK = 0
EXIT_THRESHOLD = 1
EXIT_UNAVAILABLE = 2
EXIT_INTERRUPTED = 130


# Invalid input or an unusable API answer (exit code 2). The shared client's errors are kinds of
# it: ApiError (an HTTP error status) and ApiUnreachableError (no connection, D-087).
EvalError = TrustbookError


class GenerationUnsupportedError(EvalError):
    """The API rejected the per-request ``generation`` override (an image without it)."""


# ---------------------------------------------------------------------------------------------
# Question files
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Locator:
    """One expected locator. Only the keys that were given are set."""

    file: str | None = None
    lines: tuple[int, int] | None = None
    pages: frozenset[int] | None = None
    heading: str | None = None
    seconds: tuple[float, float] | None = None
    source: str | None = None  # optional: only applies to citations of this expected source

    @property
    def kind(self) -> str:
        if self.file is not None:
            return "file"
        if self.pages is not None:
            return "pages"
        if self.heading is not None:
            return "heading"
        return "seconds"

    def describe(self) -> str:
        parts: list[str] = []
        if self.file is not None:
            parts.append(self.file)
        if self.lines is not None:
            parts.append(f"lines {self.lines[0]}-{self.lines[1]}")
        if self.pages is not None:
            parts.append("page " + ",".join(str(p) for p in sorted(self.pages)))
        if self.heading is not None:
            parts.append(f'heading "{self.heading}"')
        if self.seconds is not None:
            parts.append(f"{self.seconds[0]:g}-{self.seconds[1]:g} s")
        return " ".join(parts)


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    type: str
    expected_sources: tuple[str, ...] = ()
    expected_locators: tuple[Locator, ...] = ()
    must_contain: tuple[str, ...] = ()
    category: str | None = None
    origin_file: str = ""

    @property
    def answerable(self) -> bool:
        return self.type == "answerable"


def _int_pair(value: Any, what: str) -> tuple[int, int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return (value, value)
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and value[0] <= value[1]
    ):
        return (value[0], value[1])
    raise EvalError(f"{what}: expected an integer or [start, end], got {value!r}")


def parse_locator(raw: Any, where: str) -> Locator:
    if not isinstance(raw, dict):
        raise EvalError(f"{where}: each expected_locators entry must be a mapping")
    known = {"file", "lines", "page", "pages", "heading", "start_s", "end_s", "source"}
    unknown = set(raw) - known
    if unknown:
        raise EvalError(f"{where}: unknown locator keys {sorted(unknown)}")
    file = raw.get("file")
    lines = raw.get("lines")
    page = raw.get("page", raw.get("pages"))
    heading = raw.get("heading")
    start_s = raw.get("start_s")
    source = raw.get("source")
    kinds = sum(x is not None for x in (file, page, heading, start_s))
    if kinds != 1:
        raise EvalError(f"{where}: a locator needs exactly one of file, page(s), heading, start_s")
    if lines is not None and file is None:
        raise EvalError(f"{where}: 'lines' needs 'file'")
    if source is not None and not isinstance(source, str):
        raise EvalError(f"{where}: 'source' must be a string")
    if file is not None:
        if not isinstance(file, str) or not file.strip():
            raise EvalError(f"{where}: 'file' must be a non-empty string")
        return Locator(
            file=file,
            lines=None if lines is None else _int_pair(lines, f"{where}.lines"),
            source=source,
        )
    if page is not None:
        values = page if isinstance(page, list) else [page]
        if not values or not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            raise EvalError(f"{where}: 'page' must be an integer or a list of integers")
        return Locator(pages=frozenset(values), source=source)
    if heading is not None:
        if not isinstance(heading, str) or not heading.strip():
            raise EvalError(f"{where}: 'heading' must be a non-empty string")
        return Locator(heading=heading, source=source)
    start, end = _num(start_s), _num(raw.get("end_s", start_s))
    if start is None or end is None or end < start:
        raise EvalError(f"{where}: 'start_s'/'end_s' must be numbers with start <= end")
    return Locator(seconds=(start, end), source=source)


def _str_list(raw: Any, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        raise EvalError(f"{where}: expected a list of non-empty strings")
    return tuple(raw)


def parse_questions(data: Any, origin: str) -> list[Question]:
    """Validate one loaded YAML document (a list, or a mapping with a ``questions`` list)."""
    if isinstance(data, dict) and "questions" in data:
        data = data["questions"]
    if not isinstance(data, list):
        raise EvalError(f"{origin}: expected a list of questions")
    out: list[Question] = []
    for i, raw in enumerate(data):
        where = f"{origin}[{i}]"
        if not isinstance(raw, dict):
            raise EvalError(f"{where}: each question must be a mapping")
        qid = raw.get("id")
        text = raw.get("question")
        qtype = raw.get("type")
        if not isinstance(qid, str | int) or isinstance(qid, bool) or not str(qid).strip():
            raise EvalError(f"{where}: 'id' is required")
        where = f"{origin}:{qid}"
        if not isinstance(text, str) or not text.strip():
            raise EvalError(f"{where}: 'question' is required")
        if qtype not in QUESTION_TYPES:
            raise EvalError(f"{where}: 'type' must be one of {', '.join(QUESTION_TYPES)}")
        category = raw.get("category")
        if category is not None and category not in CATEGORIES:
            raise EvalError(f"{where}: 'category' must be one of {', '.join(CATEGORIES)}")
        sources = _str_list(raw.get("expected_sources"), f"{where}.expected_sources")
        if qtype == "answerable" and not sources:
            raise EvalError(f"{where}: answerable questions need expected_sources")
        locs_raw = raw.get("expected_locators") or []
        if not isinstance(locs_raw, list):
            raise EvalError(f"{where}: expected_locators must be a list")
        locators = tuple(
            parse_locator(loc, f"{where}.expected_locators[{j}]") for j, loc in enumerate(locs_raw)
        )
        out.append(
            Question(
                id=str(qid),
                question=text.strip(),
                type=qtype,
                expected_sources=sources,
                expected_locators=locators,
                must_contain=_str_list(raw.get("must_contain"), f"{where}.must_contain"),
                category=category,
                origin_file=origin,
            )
        )
    return out


def load_questions(paths: Sequence[str]) -> list[Question]:
    """Load question files; a directory means every ``*.yaml``/``*.yml`` in it (sorted)."""
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(f for f in path.iterdir() if f.suffix in (".yaml", ".yml")))
        elif path.is_file():
            files.append(path)
        else:
            raise EvalError(f"question path not found: {p}")
    questions: list[Question] = []
    for f in files:
        if f.name == "corpus.yaml":  # a corpus file sitting next to the questions
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise EvalError(f"{f}: invalid YAML: {exc}") from exc
        questions.extend(parse_questions(data, str(f)))
    if not questions:
        raise EvalError("no questions found")
    seen: set[str] = set()
    for q in questions:
        if q.id in seen:
            raise EvalError(f"duplicate question id: {q.id}")
        seen.add(q.id)
    return questions


# ---------------------------------------------------------------------------------------------
# Matching (sources and locators)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceInfo:
    id: str
    title: str
    origin: str | None
    type: str
    status: str
    failure_reason: str | None = None
    progress: float | None = None
    job_id: str | None = None  # the source's latest ingest job

    @staticmethod
    def from_api(raw: dict[str, Any]) -> SourceInfo:
        return SourceInfo(
            id=str(raw.get("id", "")),
            title=str(raw.get("title") or ""),
            origin=raw.get("origin") if isinstance(raw.get("origin"), str) else None,
            type=str(raw.get("type", "")),
            status=str(raw.get("status", "")),
            failure_reason=raw.get("failure_reason")
            if isinstance(raw.get("failure_reason"), str)
            else None,
            progress=raw.get("progress") if isinstance(raw.get("progress"), int | float) else None,
            job_id=raw["job_id"] if isinstance(raw.get("job_id"), str) and raw["job_id"] else None,
        )


def is_url(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value.strip()))


def norm_url(value: str) -> str:
    parts = urlsplit(value.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def norm_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    if value.startswith("./"):
        value = value[2:]
    return value.strip("/")


def paths_match(a: str, b: str) -> bool:
    """Equal, or one is a path-suffix of the other (``src/x.py`` ~ ``requests/src/x.py``)."""
    x, y = norm_path(a), norm_path(b)
    if not x or not y:
        return False
    return x == y or x.endswith("/" + y) or y.endswith("/" + x)


def entry_matches_source(entry: str, title: str, origin: str | None) -> bool:
    """An ``expected_sources`` entry matches by title, by origin URL, or by origin path."""
    if norm_text(entry) == norm_text(title):
        return True
    if origin is None:
        return False
    if is_url(entry) or is_url(origin):
        return is_url(entry) and is_url(origin) and norm_url(entry) == norm_url(origin)
    return paths_match(entry, origin)


@dataclass(frozen=True)
class Citation:
    n: int
    chunk_id: str
    source_id: str
    source_title: str
    locator: dict[str, Any]

    @staticmethod
    def from_api(raw: Any) -> Citation:
        if not isinstance(raw, dict):
            raise EvalError(f"citation is not an object: {raw!r}")
        loc = raw.get("locator")
        n = raw.get("n")
        return Citation(
            n=n if isinstance(n, int) else 0,
            chunk_id=str(raw.get("chunk_id", "")),
            source_id=str(raw.get("source_id", "")),
            source_title=str(raw.get("source_title", "")),
            locator=loc if isinstance(loc, dict) else {},
        )

    @property
    def key(self) -> str:
        return self.chunk_id or f"n{self.n}"


def describe_locator(loc: dict[str, Any]) -> str:
    if not loc:
        return "no locator"
    if "file" in loc:
        text = str(loc["file"])
        if loc.get("start_line") is not None:
            text += f" lines {loc.get('start_line')}-{loc.get('end_line', loc.get('start_line'))}"
        if loc.get("symbol"):
            text += f" ({loc['symbol']})"
        return text
    if "pages" in loc or "page" in loc:
        pages = loc.get("pages", loc.get("page"))
        values = pages if isinstance(pages, list) else [pages]
        return "page " + ",".join(str(p) for p in values)
    if "heading" in loc:
        text = f'heading "{loc["heading"]}"'
        if loc.get("offset") is not None:
            text += f" offset {loc['offset']}"
        return text
    if "start_s" in loc:
        return f"{loc.get('start_s')}-{loc.get('end_s')} s"
    return ", ".join(f"{k}={v}" for k, v in sorted(loc.items()))


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _heading_parts(value: str) -> list[str]:
    return [norm_text(p) for p in re.split(r"\s*(?:>|/|»|\|)\s*", value) if p.strip()]


def locator_kind(loc: dict[str, Any]) -> str | None:
    if "file" in loc:
        return "file"
    if "pages" in loc or "page" in loc:
        return "pages"
    if "heading" in loc:
        return "heading"
    if "start_s" in loc:
        return "seconds"
    return None


def locator_matches(expected: Locator, loc: dict[str, Any]) -> bool:
    """Does a citation's locator overlap one expected locator of the same kind?"""
    if expected.file is not None:
        if not isinstance(loc.get("file"), str) or not paths_match(expected.file, loc["file"]):
            return False
        if expected.lines is None:
            return True
        start, end = _num(loc.get("start_line")), _num(loc.get("end_line"))
        if start is None:
            return False
        lines = (float(expected.lines[0]), float(expected.lines[1]))
        return _overlap(lines, (start, end if end is not None else start))
    if expected.pages is not None:
        raw = loc.get("pages", loc.get("page"))
        values = raw if isinstance(raw, list) else [raw]
        return any(isinstance(v, int) and v in expected.pages for v in values)
    if expected.heading is not None:
        # A chunk names its main section in "heading" and, when it spans several, lists them all
        # in "headings" (D-053); any of them matches.
        raw_all = loc.get("headings")
        candidates = [
            h for h in (raw_all if isinstance(raw_all, list) else []) if isinstance(h, str)
        ]
        if isinstance(loc.get("heading"), str):
            candidates.insert(0, loc["heading"])
        want = norm_text(expected.heading)
        return any(want == norm_text(h) or want in _heading_parts(h) for h in candidates)
    if expected.seconds is not None:
        start, end = _num(loc.get("start_s")), _num(loc.get("end_s"))
        if start is None:
            return False
        return _overlap(expected.seconds, (start, end if end is not None else start))
    return False


def citation_correct(
    question: Question, citation: Citation, sources: dict[str, SourceInfo]
) -> bool:
    """SPEC §13 citation precision: right source and (if given) an overlapping locator.

    The source matches if any ``expected_sources`` entry matches the cited source's title or
    origin. Locators are compared only with expected locators of the same kind (file, page,
    heading, time) and, if a locator names a ``source``, only for citations of that source.
    When no expected locator applies to the citation, the source check alone decides.
    """
    info = sources.get(citation.source_id)
    title = info.title if info else citation.source_title
    origin = info.origin if info else None

    def matches(entry: str) -> bool:
        return entry_matches_source(entry, title, origin) or entry_matches_source(
            entry, citation.source_title, None
        )

    if not any(matches(e) for e in question.expected_sources):
        return False
    kind = locator_kind(citation.locator)
    applicable = [
        loc
        for loc in question.expected_locators
        if loc.kind == kind and (loc.source is None or matches(loc.source))
    ]
    if not applicable:
        return True
    return any(locator_matches(loc, citation.locator) for loc in applicable)


def resolve_expected(question: Question, sources: dict[str, SourceInfo]) -> set[str]:
    """Source ids in the space that the question's ``expected_sources`` entries refer to."""
    return {
        s.id
        for s in sources.values()
        for e in question.expected_sources
        if entry_matches_source(e, s.title, s.origin)
    }


def unmatched_entries(question: Question, sources: dict[str, SourceInfo]) -> list[str]:
    return [
        e
        for e in question.expected_sources
        if not any(entry_matches_source(e, s.title, s.origin) for s in sources.values())
    ]


# ---------------------------------------------------------------------------------------------
# Results and metrics
# ---------------------------------------------------------------------------------------------


@dataclass
class QuestionResult:
    question: Question
    outcome: str = "error"  # answered | refused | error
    latency_s: float | None = None
    sentences: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)  # unique per answer
    correct: list[bool] = field(default_factory=list)  # parallel to citations
    refusal_reason: str | None = None
    error: str | None = None
    must_found: list[str] = field(default_factory=list)
    expected_resolved: int = 0
    expected_cited: int = 0
    # From the answer's ``diagnostics`` and badge (Phase 2); None when the API did not send them.
    badge: str | None = None
    top_rerank: float | None = None
    sentence_scores: list[float | None] | None = None
    # Per sentence: "checker", "code_judge" (passed on the code judge, D-045) or None.
    sentence_methods: list[str | None] | None = None
    regenerated: bool | None = None
    removed_sentences: int = 0
    sentence_citations: list[list[Citation]] = field(default_factory=list)  # kept sentences
    # Which chunks the answer was generated from, and the generation settings the app used
    # (``diagnostics.retrieval_fingerprint`` / ``diagnostics.generation``); None when not sent.
    retrieval_fingerprint: str | None = None
    generation: dict[str, Any] | None = None
    # ``diagnostics.failed_sentences``: each failed sentence of a refused or partially verified
    # answer and why it failed (D-075); None when the API did not send them.
    failed_sentences: list[dict[str, str]] | None = None
    # Latency (D-080) and parent context (D-077) from ``diagnostics``; None when not sent.
    judge_calls: int | None = None
    judge_calls_per_pass: list[int] | None = None
    judge_seconds: float | None = None
    answer_calls: int | None = None
    timings: dict[str, Any] | None = None
    prompt_tokens: list[int] | None = None
    completion_tokens: list[int | None] | None = None
    expansions: list[dict[str, Any]] | None = None
    promoted_tokens: int | None = None
    outline_tokens: int | None = None
    outline_fingerprint: str | None = None
    outline_only: list[int] | None = None
    promoted_cited: list[int] | None = None
    # Transcript windows (D-091) from ``diagnostics``; None when not sent.
    windows: list[dict[str, Any]] | None = None
    window_tokens: int | None = None
    window_fingerprint: str | None = None
    window_only: list[int] | None = None

    @property
    def answer_text(self) -> str:
        return " ".join(self.sentences)

    @property
    def n_correct(self) -> int:
        return sum(self.correct)


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (NumPy's default); None for no values."""
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None


def is_code_file(path: str) -> bool:
    """A file locator path that is code: any extension except documentation and plain text."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name.strip("."):
        return True  # Makefile, Dockerfile, LICENSE-like names count as code
    return name.rsplit(".", 1)[1].lower() not in DOC_EXTENSIONS


def expects_code(question: Question) -> bool:
    """Counts towards ``--max-false-refusals-code``: category ``code``, or any question with an
    expected file locator that is a code file (multi-source questions with code)."""
    if question.category == "code":
        return True
    return any(
        loc.file is not None and is_code_file(loc.file) for loc in question.expected_locators
    )


def false_refusal_group(question: Question) -> str:
    """``docs``, ``code`` or ``multi`` by category; anything else is ``other``."""
    return question.category if question.category in ("docs", "code", "multi") else "other"


def false_refusals(results: Sequence[QuestionResult]) -> Ratio:
    answerable = [r for r in results if r.question.answerable]
    return Ratio(sum(r.outcome == "refused" for r in answerable), len(answerable))


@dataclass(frozen=True)
class Metrics:
    precision: Ratio
    correct_refusal: Ratio
    false_refusal: Ratio
    must_contain: Ratio
    errors: int
    latency_p50: float | None
    latency_p95: float | None
    latency_count: int
    false_refusal_by_group: dict[str, Ratio] = field(default_factory=dict)
    false_refusal_code: Ratio = Ratio(0, 0)


def compute_metrics(results: Sequence[QuestionResult]) -> Metrics:
    answerable = [r for r in results if r.question.answerable]
    unanswerable = [r for r in results if not r.question.answerable]
    latencies = [
        r.latency_s
        for r in results
        if r.outcome in ("answered", "refused") and r.latency_s is not None
    ]
    with_must = [r for r in answerable if r.question.must_contain]
    return Metrics(
        precision=Ratio(
            sum(r.n_correct for r in answerable), sum(len(r.citations) for r in answerable)
        ),
        correct_refusal=Ratio(sum(r.outcome == "refused" for r in unanswerable), len(unanswerable)),
        false_refusal=Ratio(sum(r.outcome == "refused" for r in answerable), len(answerable)),
        must_contain=Ratio(
            sum(len(r.must_found) == len(r.question.must_contain) for r in with_must),
            len(with_must),
        ),
        errors=sum(r.outcome == "error" for r in results),
        latency_p50=percentile(latencies, 50),
        latency_p95=percentile(latencies, 95),
        latency_count=len(latencies),
        false_refusal_by_group={
            group: false_refusals([r for r in results if false_refusal_group(r.question) == group])
            for group in FALSE_REFUSAL_GROUPS
        },
        false_refusal_code=false_refusals([r for r in results if expects_code(r.question)]),
    )


# ---------------------------------------------------------------------------------------------
# Threshold sweep (layer 6 tuning; estimation rules in eval/format.md, "Threshold sweep")
# ---------------------------------------------------------------------------------------------

REFUSE_SWEEP = tuple(round(0.05 * i, 2) for i in range(1, 9))  # 0.05 .. 0.40
VERIFY_SWEEP = tuple(round(0.1 * i, 1) for i in range(3, 8))  # 0.3 .. 0.7


@dataclass(frozen=True)
class Thresholds:
    """The space's grounding thresholds during the run (``GET /api/settings``)."""

    refuse: float = 0.15
    verify: float = 0.5


@dataclass(frozen=True)
class SweepRow:
    refuse: float
    verify: float
    correct_refusal: Ratio
    answered: Ratio
    precision: Ratio


def sweep_scores(result: QuestionResult) -> list[float | None] | None:
    """Sentence scores for the sweep: a sentence that passed on the code judge (D-045) passes at
    any verify threshold, so it counts like an unchecked one (None)."""
    scores = result.sentence_scores
    methods = result.sentence_methods or []
    if scores is None:
        return None
    return [
        None if i < len(methods) and methods[i] == "code_judge" else s for i, s in enumerate(scores)
    ]


def outcome_at(result: QuestionResult, refuse: float, verify: float) -> str:
    """Estimated outcome (``answered`` | ``refused`` | ``error``) at other thresholds.

    Refused when the top reranker score is below ``refuse``; or when, at ``verify``, more than
    half of the recorded sentence scores fail or the first one fails (None = not checked, passes);
    or when the run refused it for a reason the scores cannot re-decide (anything but
    ``unsupported``). Questions without diagnostics keep their real outcome.
    """
    if result.outcome == "error":
        return "error"
    if result.top_rerank is not None and result.top_rerank < refuse:
        return "refused"
    scores = sweep_scores(result)
    if not scores:
        return result.outcome
    failing = [s is not None and s < verify for s in scores]
    if failing[0] or sum(failing) * 2 > len(failing):
        return "refused"
    if result.outcome == "refused" and result.refusal_reason != "unsupported":
        return "refused"
    return "answered"


def kept_scores(result: QuestionResult, run_verify: float) -> list[float | None]:
    """Scores of the sentences the run kept, aligned with ``sentence_citations``.

    ``sentence_scores`` covers removed sentences too. Kept ones are those that passed at the
    run's verify threshold (score None or >= it), in order. If that cannot be matched up, every
    kept sentence counts as unknown (None).
    """
    kept = len(result.sentence_citations)
    scores = sweep_scores(result) or []
    if len(scores) == kept:
        return list(scores)
    passing = [s for s in scores if s is None or s >= run_verify]
    return passing if len(passing) == kept else [None] * kept


def sweep(
    results: Sequence[QuestionResult],
    sources: dict[str, SourceInfo],
    run: Thresholds,
) -> list[SweepRow]:
    """Correct-refusal rate, answered rate and a precision proxy for each threshold pair."""
    answerable = [r for r in results if r.question.answerable]
    unanswerable = [r for r in results if not r.question.answerable]
    rows: list[SweepRow] = []
    for refuse in REFUSE_SWEEP:
        for verify in VERIFY_SWEEP:
            refused = sum(outcome_at(r, refuse, verify) == "refused" for r in unanswerable)
            answered = [r for r in answerable if outcome_at(r, refuse, verify) == "answered"]
            num = den = 0
            for r in answered:
                if r.outcome != "answered":
                    continue  # answered only in the estimate: its citations are unknown
                unique: dict[str, Citation] = {}
                for cits, value in zip(
                    r.sentence_citations, kept_scores(r, run.verify), strict=True
                ):
                    if value is None or value >= verify:
                        for c in cits:
                            unique.setdefault(c.key, c)
                den += len(unique)
                num += sum(citation_correct(r.question, c, sources) for c in unique.values())
            rows.append(
                SweepRow(
                    refuse=refuse,
                    verify=verify,
                    correct_refusal=Ratio(refused, len(unanswerable)),
                    answered=Ratio(len(answered), len(answerable)),
                    precision=Ratio(num, den),
                )
            )
    return rows


# ---------------------------------------------------------------------------------------------
# Latency and judge calls (D-080; rules in eval/format.md, "Latency and judge calls")
# ---------------------------------------------------------------------------------------------


def reached_generation(r: QuestionResult) -> bool | None:
    """Whether the answer made an answer call (None: the API did not report ``answer_calls``)."""
    if r.answer_calls is None:
        return None
    return r.answer_calls > 0


def allowed_over(n: int, limit_p: float = 95.0) -> int:
    """How many of ``n`` latencies may exceed a bound while :func:`percentile` ``limit_p`` stays
    within it: those above the upper rank the interpolation reads (2 of 45 for p95)."""
    if n <= 0:
        return 0
    return n - 1 - math.ceil((n - 1) * limit_p / 100.0)


def promoted_citations(r: QuestionResult) -> dict[str, int] | None:
    """Citations of the answer to promoted chunks (D-077) and how many of them are correct
    (inside an expected locator); None when the API sent no ``expansions``."""
    if r.expansions is None:
        return None
    promoted = {e["chunk_id"] for e in r.expansions}
    pairs = [
        (c, ok)
        for c, ok in zip(r.citations, r.correct or [False] * len(r.citations), strict=False)
        if c.chunk_id in promoted
    ]
    return {"n": len(pairs), "correct": sum(1 for _, ok in pairs if ok)}


@dataclass(frozen=True)
class Latency:
    """The per-run latency and judge numbers of the report and the sidecar."""

    generated: list[QuestionResult]  # answers that reached generation (N)
    before_generation: list[str]  # ids refused before any answer call
    unknown: list[str]  # ids without ``answer_calls`` (an older API, or an error)
    timed: list[QuestionResult]  # answered or refused, with a latency (the p50/p95 set)

    @property
    def judge_calls(self) -> list[int]:
        return [r.judge_calls for r in self.generated if r.judge_calls is not None]

    @property
    def judge_seconds(self) -> list[float]:
        return [r.judge_seconds or 0.0 for r in self.generated if r.judge_calls is not None]

    @property
    def judge_share(self) -> float | None:
        total = sum(r.latency_s or 0.0 for r in self.generated)
        return sum(r.judge_seconds or 0.0 for r in self.generated) / total if total else None

    @property
    def answer_calls(self) -> list[int]:
        return [r.answer_calls for r in self.generated if r.answer_calls is not None]

    @property
    def regenerated(self) -> list[QuestionResult]:
        return [r for r in self.generated if r.regenerated]

    @property
    def over(self) -> list[QuestionResult]:
        return [r for r in self.timed if (r.latency_s or 0.0) > P95_FLAG_S]

    @property
    def first_prompt_tokens(self) -> list[int]:
        return [r.prompt_tokens[0] for r in self.generated if r.prompt_tokens]

    def tokens(self, name: str) -> list[int]:
        values = [getattr(r, name) for r in self.generated]
        return [v for v in values if isinstance(v, int)]

    @property
    def outline_only(self) -> list[QuestionResult]:
        return [r for r in self.generated if r.outline_only and any(r.outline_only)]

    @property
    def window_only(self) -> list[QuestionResult]:
        return [r for r in self.generated if r.window_only and any(r.window_only)]

    @property
    def promoted_citations(self) -> tuple[int, int]:
        found = [p for p in (promoted_citations(r) for r in self.generated) if p is not None]
        return sum(p["n"] for p in found), sum(p["correct"] for p in found)


def latency_stats(results: Sequence[QuestionResult]) -> Latency:
    generated: list[QuestionResult] = []
    before: list[str] = []
    unknown: list[str] = []
    for r in results:
        reached = reached_generation(r)
        if reached is None:
            unknown.append(r.question.id)
        elif reached:
            generated.append(r)
        elif r.outcome == "refused":
            before.append(r.question.id)
    timed = [r for r in results if r.outcome in ("answered", "refused") and r.latency_s is not None]
    return Latency(generated, before, unknown, timed)


def _stats(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "mean": sum(values) / len(values) if values else None,
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


def _distribution(calls: Sequence[int]) -> dict[str, int]:
    return {
        "0": sum(1 for c in calls if c == 0),
        "1": sum(1 for c in calls if c == 1),
        "2": sum(1 for c in calls if c == 2),
        "3+": sum(1 for c in calls if c >= 3),
    }


def latency_json(results: Sequence[QuestionResult]) -> dict[str, Any]:
    """The run's latency and judge numbers for the sidecar (numbers and ids only)."""
    lat = latency_stats(results)
    promoted, correct = lat.promoted_citations
    return {
        "generated": len(lat.generated),
        "before_generation": lat.before_generation,
        "unknown": lat.unknown,
        "judge_calls": _stats(lat.judge_calls) | {"distribution": _distribution(lat.judge_calls)},
        "judge_seconds": {
            "p50": percentile(lat.judge_seconds, 50),
            "p95": percentile(lat.judge_seconds, 95),
            "share": lat.judge_share,
        },
        "answer_calls": _stats(lat.answer_calls),
        "regenerated": len(lat.regenerated),
        "over_60_s": [r.question.id for r in lat.over],
        "timed": len(lat.timed),
        "allowed_over_60_s": allowed_over(len(lat.timed)),
        "first_prompt_tokens": _stats(lat.first_prompt_tokens),
        "promoted_tokens": _stats(lat.tokens("promoted_tokens")),
        "outline_tokens": _stats(lat.tokens("outline_tokens")),
        "outline_only": {
            "answers": [r.question.id for r in lat.outline_only],
            "sentences": sum(sum(r.outline_only or []) for r in lat.outline_only),
            "regenerated": sum(1 for r in lat.outline_only if r.regenerated),
        },
        "promoted_citations": {"n": promoted, "correct": correct},
        "window_tokens": _stats(lat.tokens("window_tokens")),
        "window_only": {
            "answers": [r.question.id for r in lat.window_only],
            "sentences": sum(sum(r.window_only or []) for r in lat.window_only),
            "regenerated": sum(1 for r in lat.window_only if r.regenerated),
        },
    }


def _num_cell(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}" if value != int(value) else f"{int(value)}"


def render_latency(results: Sequence[QuestionResult]) -> list[str]:
    """``#### Latency and judge calls`` (D-080): where the answer time went, per run."""
    lat = latency_stats(results)
    if not lat.generated and not lat.before_generation:
        return []
    n = len(lat.generated)
    calls = lat.judge_calls
    stats = _stats(calls)
    dist = _distribution(calls)
    judge_s = lat.judge_seconds
    share = lat.judge_share
    answer = _stats(lat.answer_calls)
    m = compute_metrics(results)
    before = (
        f" ({len(lat.before_generation)} refused before generation: "
        + ", ".join(f"`{q}`" for q in lat.before_generation)
        + ")"
        if lat.before_generation
        else ""
    )
    timed = len(lat.timed)
    over = lat.over
    allowed = allowed_over(timed)
    # The flag follows the computed p95: more than ``allowed`` answers over the bound is only
    # where p95 <= bound stops being guaranteed, not a breach (the p95 interpolates).
    p95 = percentile([r.latency_s for r in lat.timed if r.latency_s is not None], 95)
    over_flag = f" **p95 over {P95_FLAG_S:g} s**" if p95 is not None and p95 > P95_FLAG_S else ""

    def tokens(values: Sequence[int]) -> str:
        return f"{_num_cell(percentile(values, 50), 0)} / {_num_cell(percentile(values, 95), 0)}"

    only = lat.outline_only
    only_sentences = sum(sum(r.outline_only or []) for r in only)
    only_regen = sum(1 for r in only if r.regenerated)
    windowed = lat.window_only
    window_sentences = sum(sum(r.window_only or []) for r in windowed)
    window_regen = sum(1 for r in windowed if r.regenerated)
    promoted, correct = lat.promoted_citations
    out = ["#### Latency and judge calls", ""]
    out.append("| Measure | Value |")
    out.append("|---|---|")
    out.append(f"| Answers that reached generation (N) | {n}{before} |")
    out.append(
        f"| Judge calls per answer: mean / p50 / p95 / max | {_num_cell(stats['mean'], 2)} / "
        f"{_num_cell(stats['p50'])} / {_num_cell(stats['p95'])} / {_num_cell(stats['max'])} |"
    )
    out.append(
        f"| Answers with 0 / 1 / 2 / ≥3 judge calls | {dist['0']} / {dist['1']} / {dist['2']} "
        f"/ {dist['3+']} |"
    )
    out.append(
        f"| Judge seconds per answer: p50 / p95 | {_secs(percentile(judge_s, 50))} / "
        f"{_secs(percentile(judge_s, 95))} |"
    )
    share_cell = "n/a" if share is None else f"{share * 100:.1f}%"
    out.append(f"| Judge share of answer time | {share_cell} |")
    out.append(
        f"| Answer calls per answer: mean / max | {_num_cell(answer['mean'], 2)} / "
        f"{_num_cell(answer['max'])} |"
    )
    out.append(f"| Regenerations | {len(lat.regenerated)}/{n} |")
    out.append(
        f"| Answers over {P95_FLAG_S:g} s | {len(over)}/{timed} (at most {allowed} guarantee "
        f"p95 <= {P95_FLAG_S:g} s){over_flag} |"
    )
    out.append(f"| Latency p50 / p95 | {_secs(m.latency_p50)} / {_secs(m.latency_p95)} |")
    out.append(
        f"| Prompt tokens of the first answer call: p50 / p95 | {tokens(lat.first_prompt_tokens)} |"
    )
    out.append(f"| Promoted tokens: p50 / p95 | {tokens(lat.tokens('promoted_tokens'))} |")
    out.append(f"| Outline tokens: p50 / p95 | {tokens(lat.tokens('outline_tokens'))} |")
    out.append(f"| Transcript window tokens: p50 / p95 | {tokens(lat.tokens('window_tokens'))} |")
    out.append(
        f"| Outline-only failures | {only_sentences} sentence(s) in {len(only)} answer(s), "
        f"{only_regen} of them regenerated |"
    )
    out.append(
        f"| Window-only failures | {window_sentences} sentence(s) in {len(windowed)} "
        f"answer(s), {window_regen} of them regenerated |"
    )
    out.append(
        f"| Citations to promoted chunks | {promoted} ({correct} inside an expected locator) |"
    )
    if lat.unknown:
        out.append(
            f"| Answers without latency diagnostics | {len(lat.unknown)} (older API or error) |"
        )
    out.append("")
    if over:
        out.append(f"Answers over {P95_FLAG_S:g} s:")
        out.append("")
        for r in sorted(over, key=lambda r: -(r.latency_s or 0.0)):
            t = r.timings or {}
            gen = t.get("generation_s")
            ver = t.get("verification_s")
            gen_s = sum(gen) if isinstance(gen, list) else None
            ver_s = sum(ver) if isinstance(ver, list) else None
            regen = ", regenerated" if r.regenerated else ""
            out.append(
                f"- `{r.question.id}`: {_secs(r.latency_s)}; generation {_secs(gen_s)} in "
                f"{r.answer_calls if r.answer_calls is not None else '?'} call(s), verification "
                f"{_secs(ver_s)}, judge {_secs(r.judge_seconds)} in "
                f"{r.judge_calls if r.judge_calls is not None else '?'} call(s){regen}"
            )
        out.append("")
    return out


def has_diagnostics(results: Sequence[QuestionResult]) -> bool:
    return any(r.top_rerank is not None or r.sentence_scores is not None for r in results)


def _ratio_json(r: Ratio) -> dict[str, Any]:
    return {"num": r.num, "den": r.den, "value": r.value}


# 4: wf-2's version 3 plus the D-080 latency fields and the D-077 parent context fields.
# 5: the D-091 transcript window fields (``windows``, ``window_tokens``, ``window_fingerprint``,
# ``window_only``) per question and ``window_tokens`` / ``window_only`` in ``latency``.
SIDECAR_VERSION = 5
FAILED_TEXT_CHARS = 300  # the app caps failed sentences at this; the runner does too


def sidecar(
    info: RunInfo,
    results: Sequence[QuestionResult],
    rows: Sequence[SweepRow],
    setup: SetupReport | None = None,
) -> dict[str, Any]:
    """The JSON written next to the report: scores per question and the sweep.

    Ids, types, settings and numbers, and no question text. The only answer text is
    ``failed_sentences``: the sentences that failed verification and why (D-075), for diagnosing
    false refusals.
    """
    thresholds = info.thresholds
    metrics = compute_metrics(results)
    chat = info.chat
    return {
        "version": SIDECAR_VERSION,
        "date": info.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api_version": info.api_version,
        "space_id": info.space_id,
        "thresholds": None
        if thresholds is None
        else {"refuse": thresholds.refuse, "verify": thresholds.verify},
        "generation_requested": info.generation,
        "retrieval_settings": info.retrieval,
        "chat": None
        if chat is None
        else {
            "model": chat.model,
            "endpoint_host": chat.endpoint_host,
            "preset": chat.preset,
            "overridden": chat.overridden,
        },
        "setup": None
        if setup is None
        else {
            "jobs_ok": not setup.job_problems,
            "refreshed": len(setup.refreshed),
            "jobs": [
                {
                    "source_id": j.source_id,
                    "job_id": j.job_id,
                    "source_status": j.source_status,
                    "job_status": j.job_status,
                    "expect": j.expect,
                    "ok": j.ok,
                    "earlier": j.earlier,
                }
                for j in setup.jobs
            ],
        },
        "questions": [
            {
                "id": r.question.id,
                "type": r.question.type,
                "category": r.question.category,
                "outcome": r.outcome,
                "refusal_reason": r.refusal_reason,
                "badge": r.badge,
                "top_rerank": r.top_rerank,
                "sentence_scores": r.sentence_scores,
                "sentence_methods": r.sentence_methods,
                "regenerated": r.regenerated,
                "removed_sentences": r.removed_sentences,
                "citations": len(r.citations),
                "citations_correct": r.n_correct if r.question.answerable else None,
                "must_contain_found": len(r.must_found) if r.question.must_contain else None,
                "must_contain_total": len(r.question.must_contain) or None,
                "latency_s": r.latency_s,
                "retrieval_fingerprint": r.retrieval_fingerprint,
                "generation": r.generation,
                "failed_sentences": r.failed_sentences,
                "judge_calls": r.judge_calls,
                "judge_calls_per_pass": r.judge_calls_per_pass,
                "judge_seconds": r.judge_seconds,
                "answer_calls": r.answer_calls,
                "timings": r.timings,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "expansions": r.expansions,
                "promoted_tokens": r.promoted_tokens,
                "outline_tokens": r.outline_tokens,
                "outline_fingerprint": r.outline_fingerprint,
                "outline_only": r.outline_only,
                "promoted_cited": r.promoted_cited,
                "promoted_citations": promoted_citations(r),
                "windows": r.windows,
                "window_tokens": r.window_tokens,
                "window_fingerprint": r.window_fingerprint,
                "window_only": r.window_only,
            }
            for r in results
        ],
        "latency": latency_json(results),
        "false_refusals": {
            group: _ratio_json(ratio) for group, ratio in metrics.false_refusal_by_group.items()
        },
        "false_refusals_code": _ratio_json(metrics.false_refusal_code),
        "sweep": [
            {
                "refuse": row.refuse,
                "verify": row.verify,
                "correct_refusal": _ratio_json(row.correct_refusal),
                "answered": _ratio_json(row.answered),
                "precision_proxy": _ratio_json(row.precision),
            }
            for row in rows
        ],
    }


def sidecar_path(report: str) -> Path:
    """``out.md`` → ``out.json`` (any other name gets ``.json`` appended)."""
    path = Path(report)
    return path.with_suffix(".json") if path.suffix.lower() == ".md" else Path(f"{report}.json")


def score(result: QuestionResult, sources: dict[str, SourceInfo]) -> None:
    """Fill the judged fields of a result (citations must already be unique)."""
    q = result.question
    result.correct = [citation_correct(q, c, sources) for c in result.citations]
    text = result.answer_text.casefold() if result.outcome == "answered" else ""
    result.must_found = [k for k in q.must_contain if k.casefold() in text]
    expected = resolve_expected(q, sources)
    result.expected_resolved = len(expected)
    result.expected_cited = len(expected & {c.source_id for c in result.citations})


# ---------------------------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------------------------


class Api:
    """Thin wrapper over the Trustbook HTTP API (docs/ARCHITECTURE.md)."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def _request(self, method: str, url: str, what: str, **kwargs: Any) -> Any:
        return request_json(self.client, method, url, what, **kwargs)

    def thresholds(self) -> Thresholds | None:
        """The grounding thresholds in use, or None if the API does not report them."""
        try:
            data = self._request("GET", "/api/settings", "read settings")
        except ApiUnreachableError:
            raise
        except EvalError:
            return None
        raw = data.get("thresholds") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return None
        refuse, verify = _num(raw.get("refuse")), _num(raw.get("verify"))
        if refuse is None or verify is None:
            return None
        return Thresholds(refuse=refuse, verify=verify)

    def retrieval_settings(self) -> dict[str, bool] | None:
        """The parent context switches in use (D-077), or None if the API does not report them."""
        try:
            data = self._request("GET", "/api/settings", "read settings")
        except ApiUnreachableError:
            raise
        except EvalError:
            return None
        raw = data.get("retrieval") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return None
        return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, bool)}

    def health(self) -> dict[str, Any]:
        data = self._request("GET", "/api/health", "health check")
        return data if isinstance(data, dict) else {}

    def spaces(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/spaces", "list spaces")
        return [s for s in data if isinstance(s, dict)] if isinstance(data, list) else []

    def create_space(self, name: str, description: str) -> dict[str, Any]:
        body = {"name": name, "description": description}
        data = self._request("POST", "/api/spaces", "create space", json=body)
        if not isinstance(data, dict) or "id" not in data:
            raise EvalError("create space: unexpected response")
        return data

    def sources(self, space_id: str) -> list[SourceInfo]:
        data = self._request("GET", f"/api/spaces/{space_id}/sources", "list sources")
        if not isinstance(data, list):
            raise EvalError("list sources: expected a list")
        return [SourceInfo.from_api(s) for s in data if isinstance(s, dict)]

    def add_source(self, space_id: str, kind: str, value: str) -> SourceInfo:
        body = {"path": value} if kind == "folder" else {"url": value}
        data = self._request(
            "POST",
            f"/api/spaces/{space_id}/sources/{kind}",
            f"add {kind} source {value}",
            json=body,
        )
        if not isinstance(data, dict):
            raise EvalError(f"add {kind} source: unexpected response")
        return SourceInfo.from_api(data)

    def refresh_source(self, space_id: str, source_id: str) -> SourceInfo:
        """Re-ingest a source (a new ingest job with the running image's pipeline)."""
        data = self._request(
            "POST",
            f"/api/spaces/{space_id}/sources/{source_id}/refresh",
            f"refresh source {source_id}",
        )
        if not isinstance(data, dict):
            raise EvalError(f"refresh source {source_id}: unexpected response")
        return SourceInfo.from_api(data)

    def jobs(self) -> dict[str, JobInfo]:
        """Unfinished jobs and the last 50 finished ones, by id."""
        data = self._request("GET", "/api/jobs", "list jobs")
        raw = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise EvalError("list jobs: expected {'jobs': [...]}")
        jobs = [JobInfo.from_api(j) for j in raw if isinstance(j, dict)]
        return {j.id: j for j in jobs if j.id}

    def capability(self, cap_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/api/capabilities/{cap_id}", f"read {cap_id} settings")
        if not isinstance(data, dict) or not isinstance(data.get("config"), dict):
            raise EvalError(f"read {cap_id} settings: unexpected response")
        return data

    def update_capability(self, cap_id: str, body: dict[str, Any]) -> dict[str, Any]:
        data = self._request(
            "PUT", f"/api/capabilities/{cap_id}", f"save {cap_id} settings", json=body
        )
        if not isinstance(data, dict):
            raise EvalError(f"save {cap_id} settings: unexpected response")
        return data

    def test_capability(self, cap_id: str) -> dict[str, Any]:
        data = self._request(
            "POST", f"/api/capabilities/{cap_id}/test", f"test {cap_id} endpoint", json={}
        )
        if not isinstance(data, dict):
            raise EvalError(f"test {cap_id} endpoint: unexpected response")
        return data

    def create_conversation(self, space_id: str, title: str) -> str:
        data = self._request(
            "POST",
            f"/api/spaces/{space_id}/conversations",
            "create conversation",
            json={"title": title},
        )
        if not isinstance(data, dict) or "id" not in data:
            raise EvalError("create conversation: unexpected response")
        return str(data["id"])


def ask(
    client: httpx.Client,
    space_id: str,
    question: Question,
    *,
    clock: Callable[[], float] = time.perf_counter,
    generation: dict[str, Any] | None = None,
) -> QuestionResult:
    """Ask one question in a fresh conversation and consume the SSE stream.

    ``generation`` is sent as the message's per-request override (None: the app's default).
    An API that rejects it raises :class:`GenerationUnsupportedError`.
    """
    result = QuestionResult(question=question)
    api = Api(client)
    try:
        cid = api.create_conversation(space_id, f"eval: {question.id}")
    except ApiUnreachableError:
        raise
    except EvalError as exc:
        result.error = str(exc)
        return result

    url = f"/api/spaces/{space_id}/conversations/{cid}/messages"
    streamed: list[tuple[str, list[Citation]]] = []
    final: list[tuple[str, list[Citation]]] | None = None
    terminal = False
    body: dict[str, Any] = {"content": question.question}
    if generation is not None:
        body["generation"] = generation
    start = clock()
    try:
        with client.stream(
            "POST",
            url,
            json=body,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                if resp.status_code == 422 and generation is not None and "generation" in resp.text:
                    raise GenerationUnsupportedError(
                        "this Trustbook does not accept the eval generation override "
                        "(temperature/seed); update the image, or run with --app-default"
                    )
                result.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                return result
            for ev in iter_sse(sse_lines(resp.iter_bytes())):
                if ev.event in ("status", "message"):
                    continue
                try:
                    data = json.loads(ev.data) if ev.data.strip() else {}
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    result.outcome, result.error = "error", f"bad {ev.event} event data"
                    terminal = True
                    break
                if ev.event == "sentence":
                    streamed.append(_sentence(data))
                elif ev.event == "refused":
                    if result.latency_s is None:
                        result.latency_s = clock() - start
                    result.outcome = "refused"
                    result.refusal_reason = str(data.get("reason") or "refused")
                    terminal = True
                elif ev.event == "done":
                    if result.latency_s is None:
                        result.latency_s = clock() - start
                    message = data.get("message")
                    if isinstance(message, dict):
                        if message.get("badge") == "refused" and result.outcome != "refused":
                            result.outcome = "refused"
                            result.refusal_reason = result.refusal_reason or "refused"
                        content = message.get("content")
                        if isinstance(content, dict) and isinstance(content.get("sentences"), list):
                            final = [
                                _sentence(s) for s in content["sentences"] if isinstance(s, dict)
                            ]
                        elif (
                            isinstance(content, dict)
                            and isinstance(content.get("text"), str)
                            and message.get("badge") is None
                            and not streamed
                        ):
                            # The gate's fixed chitchat reply: no answer was given.
                            result.outcome = "refused"
                            result.refusal_reason = "chitchat"
                        read_message_extras(result, message)
                    if result.outcome != "refused":
                        result.outcome = "answered"
                    terminal = True
                    break
                elif ev.event == "error":
                    result.outcome = "error"
                    result.error = str(data.get("message") or "error event")
                    result.latency_s = None
                    terminal = True
                    break
    except httpx.TransportError as exc:
        if result.outcome != "refused":
            result.outcome = "error"
            result.error = f"{type(exc).__name__}: {exc}"
        return result
    except GenerationUnsupportedError:
        raise
    except EvalError as exc:
        result.outcome, result.error = "error", str(exc)
        return result

    if not terminal:
        result.outcome = "error"
        result.error = "stream ended without done, refused or error"
        return result
    sentences = final if final is not None else streamed
    if result.outcome == "answered":
        result.sentences = [text for text, _ in sentences]
        result.sentence_citations = [cits for _, cits in sentences]
        unique: dict[str, Citation] = {}
        for _, cits in sentences:
            for c in cits:
                unique.setdefault(c.key, c)
        result.citations = list(unique.values())
    return result


def _score(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def read_message_extras(result: QuestionResult, message: dict[str, Any]) -> None:
    """Badge, removed sentences and ``diagnostics`` of the final assistant message."""
    badge = message.get("badge")
    result.badge = badge if isinstance(badge, str) else None
    removed = message.get("removed_sentences")
    result.removed_sentences = len(removed) if isinstance(removed, list) else 0
    diagnostics = message.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return
    result.top_rerank = _score(diagnostics.get("top_rerank"))
    scores = diagnostics.get("sentence_scores")
    if isinstance(scores, list):
        result.sentence_scores = [_score(v) for v in scores]
    methods = diagnostics.get("sentence_methods")
    if isinstance(methods, list):
        result.sentence_methods = [m if isinstance(m, str) else None for m in methods]
    regenerated = diagnostics.get("regenerated")
    result.regenerated = regenerated if isinstance(regenerated, bool) else None
    fingerprint = diagnostics.get("retrieval_fingerprint")
    if isinstance(fingerprint, str) and fingerprint:
        result.retrieval_fingerprint = fingerprint
    elif isinstance(fingerprint, list | dict) and fingerprint:
        result.retrieval_fingerprint = json.dumps(fingerprint, sort_keys=True)
    generation = diagnostics.get("generation")
    result.generation = generation if isinstance(generation, dict) else None
    result.failed_sentences = read_failed_sentences(diagnostics.get("failed_sentences"))
    read_latency(result, diagnostics)


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _counts(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    return [n for n in (_int_value(v) for v in value) if n is not None]


def read_latency(result: QuestionResult, diagnostics: dict[str, Any]) -> None:
    """The D-080 latency fields and the D-077 parent context fields (numbers and ids only)."""
    result.judge_calls = _int_value(diagnostics.get("judge_calls"))
    result.judge_calls_per_pass = _counts(diagnostics.get("judge_calls_per_pass"))
    result.judge_seconds = _score(diagnostics.get("judge_seconds"))
    result.answer_calls = _int_value(diagnostics.get("answer_calls"))
    timings = diagnostics.get("timings")
    if isinstance(timings, dict):
        result.timings = {
            key: (
                [x for x in (_score(v) for v in value) if x is not None]
                if isinstance(value, list)
                else _score(value)
            )
            for key, value in timings.items()
            if isinstance(key, str)
        }
    result.prompt_tokens = _counts(diagnostics.get("prompt_tokens"))
    completion = diagnostics.get("completion_tokens")
    if isinstance(completion, list):
        result.completion_tokens = [_int_value(v) for v in completion]
    expansions = diagnostics.get("expansions")
    if isinstance(expansions, list):
        result.expansions = [
            {
                "chunk_id": e.get("chunk_id"),
                "kind": e.get("kind"),
                "referrer": e.get("referrer"),
            }
            for e in expansions
            if isinstance(e, dict) and isinstance(e.get("chunk_id"), str)
        ]
    result.promoted_tokens = _int_value(diagnostics.get("promoted_tokens"))
    result.outline_tokens = _int_value(diagnostics.get("outline_tokens"))
    outline = diagnostics.get("outline_fingerprint")
    result.outline_fingerprint = outline if isinstance(outline, str) and outline else None
    result.outline_only = _counts(diagnostics.get("outline_only"))
    result.promoted_cited = _counts(diagnostics.get("promoted_cited"))
    windows = diagnostics.get("windows")
    if isinstance(windows, list):
        result.windows = [
            {
                "source_id": w.get("source_id"),
                "numbered": [i for i in w.get("numbered") or [] if isinstance(i, str)],
                "chunk_ids": [i for i in w.get("chunk_ids") or [] if isinstance(i, str)],
                "tokens": _int_value(w.get("tokens")),
            }
            for w in windows
            if isinstance(w, dict)
        ]
    result.window_tokens = _int_value(diagnostics.get("window_tokens"))
    window = diagnostics.get("window_fingerprint")
    result.window_fingerprint = window if isinstance(window, str) and window else None
    result.window_only = _counts(diagnostics.get("window_only"))


def _capped(value: str) -> str:
    flat = " ".join(value.split())
    return flat if len(flat) <= FAILED_TEXT_CHARS else flat[: FAILED_TEXT_CHARS - 1] + "…"


def read_failed_sentences(raw: Any) -> list[dict[str, str]] | None:
    """``diagnostics.failed_sentences`` as ``[{"text", "reason"}]`` (None when not a list)."""
    if not isinstance(raw, list):
        return None
    found: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        reason = item.get("reason")
        why = _capped(reason) if isinstance(reason, str) else ""
        found.append({"text": _capped(item["text"]), "reason": why})
    return found


def _sentence(data: dict[str, Any]) -> tuple[str, list[Citation]]:
    cits = data.get("citations")
    citations = [Citation.from_api(c) for c in cits] if isinstance(cits, list) else []
    return (str(data.get("text", "")), citations)


# ---------------------------------------------------------------------------------------------
# Corpus setup
# ---------------------------------------------------------------------------------------------


CORPUS_EXPECT = ("ready", "failed")


@dataclass(frozen=True)
class CorpusEntry:
    kind: str  # folder | url
    value: str
    expect: str = "ready"  # ready | failed (a source that must fail, e.g. a blocked page)


@dataclass(frozen=True)
class Corpus:
    description: str
    entries: tuple[CorpusEntry, ...]


def load_corpus(path: str) -> Corpus:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvalError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise EvalError(f"{path}: expected a mapping with a 'sources' list")
    entries: list[CorpusEntry] = []
    for i, raw in enumerate(data["sources"]):
        if not isinstance(raw, dict) or len({"folder", "url"} & set(raw)) != 1:
            raise EvalError(f"{path}: sources[{i}] needs exactly one of 'folder' or 'url'")
        unknown = set(raw) - {"folder", "url", "expect"}
        if unknown:
            raise EvalError(f"{path}: sources[{i}]: unknown keys {sorted(unknown)}")
        kind = "folder" if "folder" in raw else "url"
        value = raw[kind]
        if not isinstance(value, str) or not value.strip():
            raise EvalError(f"{path}: sources[{i}].{kind} must be a non-empty string")
        if kind == "url" and not is_url(value):
            raise EvalError(f"{path}: sources[{i}].url is not a URL: {value}")
        expect = raw.get("expect", "ready")
        if expect not in CORPUS_EXPECT:
            raise EvalError(
                f"{path}: sources[{i}].expect must be one of {', '.join(CORPUS_EXPECT)}"
            )
        entries.append(CorpusEntry(kind, value.strip(), expect))
    description = data.get("space_description", "")
    return Corpus(description=str(description or ""), entries=tuple(entries))


def entry_present(entry: CorpusEntry, source: SourceInfo) -> bool:
    if source.origin is None:
        return False
    if entry.kind == "url":
        return is_url(source.origin) and norm_url(entry.value) == norm_url(source.origin)
    return source.type == "folder" and paths_match(entry.value, source.origin)


@dataclass(frozen=True)
class JobInfo:
    """One entry of ``GET /api/jobs``."""

    id: str
    kind: str
    status: str
    error: str | None = None

    @staticmethod
    def from_api(raw: dict[str, Any]) -> JobInfo:
        error = raw.get("error")
        return JobInfo(
            id=str(raw.get("id", "")),
            kind=str(raw.get("kind", "")),
            status=str(raw.get("status", "")),
            error=error if isinstance(error, str) and error else None,
        )


@dataclass(frozen=True)
class JobCheck:
    """A corpus source's ingest job against the source's own status (D-055).

    ``problem`` is set when the job did not end as expected: ``done`` for a ready source, ``failed``
    for a source the corpus expects to fail. A ready source whose job failed (or the reverse) is a
    job/source mismatch. ``note`` explains a job that could not be checked but is not an error.
    ``earlier``: the job had already ended before this run started, so it may have run on another
    image; only the source's current status is judged (``--refresh`` re-ingests and checks it).
    """

    source_id: str
    label: str
    source_status: str
    job_id: str | None
    job_status: str | None
    expect: str = "ready"
    problem: str | None = None
    note: str | None = None
    earlier: bool = False

    @property
    def ok(self) -> bool:
        return self.problem is None


def check_job(
    source: SourceInfo,
    job: JobInfo | None,
    expect: str,
    *,
    added: bool,
    listed: bool = True,
    earlier: bool = False,
) -> JobCheck:
    """Judge one source's ingest job. ``added``: this run started the job (it added or refreshed
    the source), so the job must be listed; ``listed``: ``GET /api/jobs`` answered at all;
    ``earlier``: the job had already ended when this run started (see :class:`JobCheck`)."""
    base = JobCheck(
        source_id=source.id,
        label=source.title or str(source.origin),
        source_status=source.status,
        job_id=source.job_id,
        job_status=job.status if job else None,
        expect=expect,
        earlier=earlier and job is not None and not added,
    )
    if not listed:
        return replace(base, problem="job statuses unavailable (GET /api/jobs failed)")
    if job is None:
        if source.job_id is None:
            return replace(base, problem="source has no ingest job") if added else base
        if added:
            return replace(base, problem="ingest job not listed in GET /api/jobs")
        return replace(base, note="older job, no longer listed")
    reason = f": {job.error}" if job.error else ""
    if base.earlier:
        return _check_earlier(base, source, job, reason)
    if job.status not in JOB_DONE:
        return replace(base, problem=f"job still {job.status}")
    if job.status == "cancelled":
        return replace(base, problem="job cancelled")
    if source.status == "ready" and job.status == "failed":
        return replace(base, problem=f"job/source mismatch: source ready, job failed{reason}")
    if source.status == "failed" and job.status == "done":
        return replace(base, problem="job/source mismatch: source failed, job done")
    if source.status not in SOURCE_DONE:
        return replace(base, problem=f"job {job.status}, source still {source.status}")
    if expect == "ready" and job.status == "failed":
        why = source.failure_reason or job.error
        return replace(base, problem="source and job failed" + (f": {why}" if why else ""))
    if expect == "failed" and job.status == "done":
        return replace(base, problem="expected to fail, but the job is done and the source ready")
    return base


def _check_earlier(base: JobCheck, source: SourceInfo, job: JobInfo, reason: str) -> JobCheck:
    """A job that ended before this run: it says nothing about the image under test (Gate 3 F1
    failed every job of the phase-3-rc image while each source was indexed correctly), so it is
    noted, and only the source's current status, which this run's answers depend on, is judged."""
    if base.expect == "ready" and source.status != "ready":
        why = source.failure_reason or job.error
        return replace(base, problem=f"source {source.status}" + (f": {why}" if why else ""))
    if base.expect == "failed" and source.status == "ready":
        return replace(base, problem="expected to fail, but the source is ready")
    mismatch = (source.status == "ready" and job.status == "failed") or (
        source.status == "failed" and job.status == "done"
    )
    what = "job/source mismatch from an earlier ingest" if mismatch else "earlier job"
    return replace(
        base,
        note=f"{what} (job {job.status}{reason}), ended before this run: not re-checked, "
        "--refresh re-ingests and checks it",
    )


@dataclass
class SetupReport:
    space_created: bool = False
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    add_failed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_ready: list[str] = field(default_factory=list)
    timed_out: bool = False
    seconds: float = 0.0
    jobs: list[JobCheck] = field(default_factory=list)

    @property
    def job_problems(self) -> list[JobCheck]:
        return [j for j in self.jobs if not j.ok]

    @property
    def jobs_ratio(self) -> Ratio:
        return Ratio(sum(j.ok for j in self.jobs), len(self.jobs))

    @property
    def earlier_jobs(self) -> int:
        """Jobs that ended before this run and were therefore not re-checked."""
        return sum(j.earlier for j in self.jobs)


def _progress_line(elapsed: float, tracked: Sequence[SourceInfo]) -> str:
    ready = sum(s.status == "ready" for s in tracked)
    failed = sum(s.status == "failed" for s in tracked)
    busy = [
        f"{s.title or s.origin} {s.status}"
        + (f" {round(s.progress * 100)}%" if s.progress is not None else "")
        for s in tracked
        if s.status not in SOURCE_DONE
    ]
    line = f"[{elapsed:6.0f}s] {ready}/{len(tracked)} ready, {failed} failed"
    return line + ("; " + ", ".join(busy[:4]) if busy else "")


def setup_corpus(
    api: Api,
    space_key: str,
    corpus: Corpus,
    *,
    timeout_s: float,
    refresh: bool = False,
    poll_s: float = 5.0,
    log: TextIO = sys.stderr,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], SetupReport]:
    """Create the space if missing, add missing sources, wait until all are ready or failed,
    then wait for their ingest jobs and check each job's status against its source (D-055).

    Idempotent: a corpus entry whose origin already exists in the space is skipped, or with
    ``refresh`` re-ingested, so that its job runs (and is checked) on the image under test. The
    job of a skipped source that had already ended when the run started is an earlier job: it
    is noted, not judged (see :func:`check_job`).
    """
    report = SetupReport()
    space = find_space(api.spaces(), space_key)
    if space is None:
        space = api.create_space(space_key, corpus.description)
        report.space_created = True
        print(f"created space {space.get('name')} ({space['id']})", file=log)
    sid = str(space["id"])
    ended_before = _ended_jobs(api)
    existing = api.sources(sid)
    tracked: dict[str, CorpusEntry] = {}
    added_ids: set[str] = set()  # sources whose current job this run started
    earlier: dict[str, str] = {}  # source id -> its job id, for jobs that ended before the run
    refresh_failed: dict[str, str] = {}
    for entry in corpus.entries:
        match = next((s for s in existing if entry_present(entry, s)), None)
        if match is not None:
            tracked[match.id] = entry
            if refresh:
                try:
                    api.refresh_source(sid, match.id)
                except ApiUnreachableError:
                    raise
                except EvalError as exc:
                    refresh_failed[match.id] = str(exc)
                    print(f"could not refresh {entry.kind} {entry.value}: {exc}", file=log)
                else:
                    added_ids.add(match.id)
                    report.refreshed.append(entry.value)
                    print(f"refreshed {entry.kind} {entry.value}", file=log)
                    continue
            if match.job_id is not None and match.job_id in ended_before:
                earlier[match.id] = match.job_id
            report.skipped.append(f"{entry.value} (already present: {match.status})")
            print(f"skip {entry.kind} {entry.value}: already present ({match.status})", file=log)
            continue
        try:
            created = api.add_source(sid, entry.kind, entry.value)
        except ApiUnreachableError:
            raise
        except EvalError as exc:
            report.add_failed.append(f"{entry.value}: {exc}")
            print(f"could not add {entry.kind} {entry.value}: {exc}", file=log)
            continue
        tracked[created.id] = entry
        added_ids.add(created.id)
        existing.append(created)
        report.added.append(entry.value)
        print(f"added {entry.kind} {entry.value}", file=log)

    start = clock()
    last_line = ""
    while True:
        current = [s for s in api.sources(sid) if s.id in tracked]
        elapsed = clock() - start
        line = _progress_line(elapsed, current)
        if line.split("]", 1)[1] != last_line.split("]", 1)[-1]:
            print(line, file=log)
            last_line = line
        if all(s.status in SOURCE_DONE for s in current):
            break
        if elapsed >= timeout_s:
            report.timed_out = True
            report.not_ready = [
                s.title or str(s.origin) for s in current if s.status not in SOURCE_DONE
            ]
            print(
                f"setup timed out after {timeout_s:.0f}s; not ready: {report.not_ready}", file=log
            )
            break
        sleep(poll_s)
    report.failed = [
        f"{s.title or s.origin}: {s.failure_reason or 'failed'}"
        for s in current
        if s.status == "failed"
    ]
    report.jobs = wait_for_jobs(
        api,
        current,
        {source_id: e.expect for source_id, e in tracked.items()},
        added_ids,
        earlier=earlier,
        deadline=start + timeout_s,
        poll_s=poll_s,
        log=log,
        sleep=sleep,
        clock=clock,
    )
    report.jobs = [
        replace(j, problem=f"refresh failed: {refresh_failed[j.source_id]}", note=None)
        if j.source_id in refresh_failed
        else j
        for j in report.jobs
    ]
    report.seconds = clock() - start
    return space, report


def _ended_jobs(api: Api) -> set[str]:
    """Ids of the jobs that had already ended when the run started (empty if unknown: then no
    job counts as earlier, and a failing ``GET /api/jobs`` is reported by the job checks)."""
    try:
        jobs = api.jobs()
    except ApiUnreachableError:
        raise
    except EvalError:
        return set()
    return {j.id for j in jobs.values() if j.status in JOB_DONE}


def wait_for_jobs(
    api: Api,
    sources: Sequence[SourceInfo],
    expect: dict[str, str],
    added: set[str],
    *,
    earlier: dict[str, str] | None = None,
    deadline: float,
    poll_s: float,
    log: TextIO,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> list[JobCheck]:
    """Poll ``GET /api/jobs`` until every finished source's job has ended (or ``deadline``), then
    check each one. A source can look ready while its job still fails in a later step (Gate 3,
    F1): that is the job/source mismatch this catches."""
    jobs: dict[str, JobInfo] = {}
    listed = True
    while True:
        try:
            jobs = api.jobs()
        except ApiUnreachableError:
            raise
        except EvalError as exc:
            print(f"could not read ingest jobs: {exc}", file=log)
            listed = False
            break
        running = [
            s
            for s in sources
            if s.status in SOURCE_DONE
            and s.job_id in jobs
            and jobs[s.job_id].status not in JOB_DONE
        ]
        if not running or clock() >= deadline:
            break
        sleep(poll_s)
    checks = [
        check_job(
            s,
            jobs.get(s.job_id) if s.job_id else None,
            expect.get(s.id, "ready"),
            added=s.id in added,
            listed=listed,
            earlier=earlier is not None and s.job_id is not None and earlier.get(s.id) == s.job_id,
        )
        for s in sources
    ]
    for c in checks:
        tail = f": {c.problem}" if c.problem else (f" ({c.note})" if c.note else "")
        print(
            f"job for {c.label}: {c.job_status or 'not listed'}, source {c.source_status}{tail}",
            file=log,
        )
    return checks


# ---------------------------------------------------------------------------------------------
# Chat model for the run (--chat-endpoint / --chat-model; D-055)
# ---------------------------------------------------------------------------------------------

# The fields the runner saves and puts back. The API key is never read (the API only returns
# whether one is set) and never sent, so it stays exactly as the user saved it.
CHAT_RESTORE_FIELDS = ("preset", "base_url", "model", "api_version")


@dataclass(frozen=True)
class ChatConfig:
    """The saved chat capability config, as far as the runner needs it."""

    preset: str | None
    base_url: str | None
    model: str | None
    api_version: str | None
    api_key_set: bool = False

    @staticmethod
    def from_capability(cap: dict[str, Any]) -> ChatConfig:
        cfg = cap.get("config")
        cfg = cfg if isinstance(cfg, dict) else {}

        def text(name: str) -> str | None:
            value = cfg.get(name)
            return value if isinstance(value, str) and value else None

        return ChatConfig(
            preset=text("preset"),
            base_url=text("base_url"),
            model=text("model"),
            api_version=text("api_version"),
            api_key_set=cfg.get("api_key_set") is True,
        )

    def restore_body(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in CHAT_RESTORE_FIELDS}

    def describe(self) -> str:
        return (
            f"preset {self.preset or '-'}, base URL {self.base_url or '-'}, "
            f"model {self.model or '-'}, API version {self.api_version or '-'}"
        )


@dataclass(frozen=True)
class ChatInfo:
    """The chat model and endpoint the run used (read back from the API)."""

    model: str | None
    endpoint_host: str | None
    preset: str | None
    overridden: bool = False


def endpoint_host(url: str | None) -> str | None:
    """``host:port`` of a base URL (no path, no credentials)."""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
    return (f"{host}:{port}" if port else host) or None


def url_origin(url: str | None) -> tuple[str, str, int | None] | None:
    """Scheme, host and port: the parts that decide where a saved API key is sent (the same
    comparison the backend uses to drop a key when the chat server changes)."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    try:
        port = parts.port
    except ValueError:
        port = None
    return (parts.scheme.lower(), (parts.hostname or "").lower(), port)


def chat_override_body(
    original: ChatConfig, endpoint: str | None, model: str | None
) -> dict[str, Any]:
    """The ``PUT /api/capabilities/chat`` body for the override.

    An endpoint switches to the ``custom`` preset (any OpenAI-compatible server, such as
    llama-server). Trustbook drops a saved API key when the chat server changes, so that the key
    never reaches another host; the runner cannot put it back afterwards (it never sees keys).
    Such an override is refused instead.
    """
    body: dict[str, Any] = {}
    if endpoint is not None:
        if not is_url(endpoint):
            raise EvalError(f"--chat-endpoint is not a URL: {endpoint}")
        if original.api_key_set and url_origin(endpoint) != url_origin(original.base_url):
            raise EvalError(
                "the saved chat settings have an API key, and Trustbook deletes a saved key when "
                "the chat server changes; --chat-endpoint would lose it. Use an endpoint on the "
                "same server, or change the chat endpoint in Settings yourself."
            )
        body["preset"] = "custom"
        body["base_url"] = endpoint.strip()
    if model is not None:
        if not model.strip():
            raise EvalError("--chat-model must not be empty")
        body["model"] = model.strip()
    return body


def read_chat_info(api: Api, *, overridden: bool = False) -> ChatInfo | None:
    """The chat model and endpoint host as the API reports them; None if it cannot say."""
    try:
        config = ChatConfig.from_capability(api.capability("chat"))
    except ApiUnreachableError:
        raise
    except EvalError:
        return None
    return ChatInfo(
        model=config.model,
        endpoint_host=endpoint_host(config.base_url),
        preset=config.preset,
        overridden=overridden,
    )


def apply_chat_override(api: Api, body: dict[str, Any], log: TextIO) -> None:
    """Save the override, then test the endpoint once so a typo fails before the run."""
    api.update_capability("chat", body)
    result = api.test_capability("chat")
    if result.get("ok") is not True:
        message = str(result.get("message") or "no reason given")
        raise EvalError(f"the chat endpoint from --chat-endpoint/--chat-model failed: {message}")
    print("chat override saved and tested", file=log)


def restore_chat(api: Api, original: ChatConfig, log: TextIO) -> bool:
    """Put the saved chat settings back. Never raises: on failure it prints what to restore."""
    try:
        api.update_capability("chat", original.restore_body())
        now = ChatConfig.from_capability(api.capability("chat"))
    except (EvalError, httpx.HTTPError) as exc:
        print(
            f"WARNING: could not restore the chat settings ({exc}). Restore them in Settings: "
            f"{original.describe()}",
            file=log,
        )
        return False
    if (now.preset, now.model, now.api_version) != (
        original.preset,
        original.model,
        original.api_version,
    ) or url_origin(now.base_url) != url_origin(original.base_url):
        print(
            "WARNING: the chat settings read back differ from the saved ones. Check Settings: "
            f"expected {original.describe()}, found {now.describe()}",
            file=log,
        )
        return False
    print("chat settings restored", file=log)
    return True


# ---------------------------------------------------------------------------------------------
# Cited ranges (Gate 3: every cited file/line range exists at the indexed commit)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BadRange:
    question_id: str
    file: str
    start: int | None
    end: int | None
    reason: str


@dataclass(frozen=True)
class RangeReport:
    """Citations with ``locator.file`` checked against a checkout of the corpus."""

    root: str
    valid: Ratio
    bad: tuple[BadRange, ...] = ()


def _line_number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def range_problem(root: Path, locator: dict[str, Any], lines: dict[str, int | None]) -> str | None:
    """Why a cited file/line range does not exist under ``root`` (None when it does).

    ``lines`` caches line counts per relative path (None: missing or unreadable file).
    """
    rel = str(locator.get("file") or "").replace("\\", "/").strip()
    if not rel:
        return "empty file name"
    if rel not in lines:
        path = (root / rel).resolve()
        inside = path == root or root in path.parents
        try:
            text = path.read_text("utf-8", errors="replace") if inside and path.is_file() else None
        except OSError:
            text = None
        lines[rel] = None if text is None else len(text.splitlines())
    count = lines[rel]
    if count is None:
        return "file not found"
    start, end = locator.get("start_line"), locator.get("end_line")
    if start is None and end is None:
        return None
    first, last = _line_number(start), _line_number(end)
    if first is None or last is None or first < 1 or last < first:
        return "invalid line range"
    if last > count:
        return f"file has {count} lines"
    return None


def check_ranges(results: Sequence[QuestionResult], root: str) -> RangeReport:
    """Every citation with a ``locator.file`` (all answered questions, unique per answer)."""
    base = Path(root).resolve()
    lines: dict[str, int | None] = {}
    total = 0
    bad: list[BadRange] = []
    for r in results:
        for c in r.citations:
            if not isinstance(c.locator.get("file"), str):
                continue
            total += 1
            problem = range_problem(base, c.locator, lines)
            if problem is not None:
                bad.append(
                    BadRange(
                        r.question.id,
                        str(c.locator.get("file")),
                        _line_number(c.locator.get("start_line")),
                        _line_number(c.locator.get("end_line")),
                        problem,
                    )
                )
    return RangeReport(root=root, valid=Ratio(total - len(bad), total), bad=tuple(bad))


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


def _pct(r: Ratio) -> str:
    v = r.value
    return "n/a (0/0)" if v is None else f"{v * 100:.1f}% ({r.num}/{r.den})"


def _secs(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f} s"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _verdict(value: float | None, minimum: float | None) -> tuple[str, str]:
    if minimum is None:
        return ("-", "-")
    target = f">= {minimum * 100:g}%"
    return (target, "pass" if value is not None and value >= minimum else "**FAIL**")


def _at_most(value: float | None, maximum: float | None, unit: str = "") -> tuple[str, str]:
    if maximum is None:
        return ("-", "-")
    target = f"<= {maximum:g}{unit}"
    return (target, "pass" if value is not None and value <= maximum else "**FAIL**")


@dataclass(frozen=True)
class RunInfo:
    base_url: str
    space_name: str
    space_id: str
    question_files: tuple[str, ...]
    api_version: str | None
    started_at: datetime
    min_precision: float | None = None
    min_refusal: float | None = None
    min_must_contain: float | None = None
    thresholds: Thresholds | None = None  # None when the API did not report them
    max_false_refusals_code: int | None = None
    max_p95: float | None = None
    min_ranges_valid: float | None = None
    # The per-request override sent with every question; None = the app's own default.
    generation: dict[str, Any] | None = None
    chat: ChatInfo | None = None  # None when the API did not report it
    # The D-077 switches during the run (``GET /api/settings``); None when not reported.
    retrieval: dict[str, bool] | None = None


def describe_generation(generation: dict[str, Any]) -> str:
    """``temperature 0, seed 42`` (keys in a fixed order, others sorted after them)."""
    order = ["temperature", "seed"]
    keys = [k for k in order if k in generation] + sorted(k for k in generation if k not in order)
    parts = []
    for k in keys:
        v = generation[k]
        value = f"{v:g}" if isinstance(v, float) else ("none" if v is None else str(v))
        parts.append(f"{k} {value}")
    return ", ".join(parts) or "nothing"


def generation_lines(info: RunInfo, results: Sequence[QuestionResult]) -> list[str]:
    """Header lines: what the runner asked for and what the app reports it used."""
    if info.generation is None:
        lines = [
            "- Generation: **real-use run**, app default (the runner sends no override; the app "
            "uses temperature 0.2 without a seed)"
        ]
    else:
        lines = [
            f"- Generation: {describe_generation(info.generation)} (eval override sent with every "
            f"question; the app default is temperature 0.2 without a seed). {SEED_CAVEAT}"
        ]
    seen: dict[str, int] = {}
    for r in results:
        if r.generation is not None:
            key = describe_generation(r.generation)
            seen[key] = seen.get(key, 0) + 1
    if seen:
        reported = "; ".join(f"{k} ({n}/{len(results)} answers)" for k, n in sorted(seen.items()))
        lines.append(f"- Generation reported by the app: {reported}")
    return lines


def chat_line(chat: ChatInfo | None) -> str:
    if chat is None:
        return "- Chat model: not reported by the API"
    how = (
        "set for this run by --chat-endpoint/--chat-model, restored afterwards"
        if chat.overridden
        else "saved settings"
    )
    return (
        f"- Chat model: `{chat.model or 'unknown'}` at `{chat.endpoint_host or 'unknown'}` "
        f"(preset {chat.preset or '-'}; {how})"
    )


def render_report(
    info: RunInfo,
    results: Sequence[QuestionResult],
    sources: dict[str, SourceInfo],
    setup: SetupReport | None = None,
    ranges: RangeReport | None = None,
) -> str:
    m = compute_metrics(results)
    n_ans = sum(r.question.answerable for r in results)
    out: list[str] = []
    out.append(f"### Evaluation: {info.space_name}")
    out.append("")
    out.append(f"- Date: {info.started_at.strftime('%Y-%m-%d %H:%M UTC')}")
    version = f" (version {info.api_version})" if info.api_version else ""
    out.append(f"- API: {info.base_url}{version}")
    out.append(f"- Space: {info.space_name} (`{info.space_id}`), {len(sources)} sources")
    files = ", ".join(f"`{f}`" for f in info.question_files)
    out.append(
        f"- Questions: {files}: {len(results)} "
        f"({n_ans} answerable, {len(results) - n_ans} unanswerable)"
    )
    out.append(chat_line(info.chat))
    out.extend(generation_lines(info, results))
    out.append("")
    p_target, p_res = _verdict(m.precision.value, info.min_precision)
    r_target, r_res = _verdict(m.correct_refusal.value, info.min_refusal)
    k_target, k_res = _verdict(m.must_contain.value, info.min_must_contain)
    out.append("| Metric | Value | Target | Result |")
    out.append("|---|---|---|---|")
    out.append(f"| Citation precision (answerable) | {_pct(m.precision)} | {p_target} | {p_res} |")
    out.append(
        f"| Correct-refusal rate (unanswerable) | {_pct(m.correct_refusal)} "
        f"| {r_target} | {r_res} |"
    )
    out.append(
        f"| must_contain hit rate (answerable with keywords) | {_pct(m.must_contain)} "
        f"| {k_target} | {k_res} |"
    )
    out.append(
        f"| False refusals (answerable) | {m.false_refusal.num}/{m.false_refusal.den} | - | - |"
    )
    for group in FALSE_REFUSAL_GROUPS:
        g = m.false_refusal_by_group.get(group, Ratio(0, 0))
        out.append(f"| False refusals: {group} | {g.num}/{g.den} | - | - |")
    code = m.false_refusal_code
    c_target, c_res = _at_most(
        None if code.den == 0 and info.max_false_refusals_code is not None else code.num,
        info.max_false_refusals_code,
    )
    out.append(
        "| False refusals on code questions (category code, or expecting a code file) "
        f"| {code.num}/{code.den} | {c_target} | {c_res} |"
    )
    out.append(f"| Errors | {m.errors} | - | - |")
    l_target, l_res = _at_most(m.latency_p95, info.max_p95, " s")
    flag = (
        f" **above {P95_FLAG_S:g} s**"
        if m.latency_p95 is not None and m.latency_p95 > P95_FLAG_S
        else ""
    )
    out.append(
        f"| Answer latency p50 / p95 ({m.latency_count} answers) | "
        f"{_secs(m.latency_p50)} / {_secs(m.latency_p95)}{flag} | {l_target} | {l_res} |"
    )
    if ranges is not None or info.min_ranges_valid is not None:
        valid = ranges.valid if ranges is not None else Ratio(0, 0)
        v_target, v_res = _verdict(valid.value, info.min_ranges_valid)
        out.append(
            f"| Cited ranges valid (file and lines exist in the checkout) | {_pct(valid)} "
            f"| {v_target} | {v_res} |"
        )
    if setup is not None:
        jobs = setup.jobs_ratio
        j_res = "pass" if not setup.job_problems else "**FAIL**"
        n_earlier = setup.earlier_jobs
        j_val = f"{jobs.num}/{jobs.den}" + (
            f" ({n_earlier} ended before this run, not re-checked)" if n_earlier else ""
        )
        out.append(f"| Ingest jobs ended as expected (setup) | {j_val} | all | {j_res} |")
    out.append("")
    out.append(
        "| ID | Type | Category | Outcome | Citations correct/total "
        "| Expected sources cited | must_contain | Latency |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        q = r.question
        outcome = r.outcome
        if r.outcome == "refused":
            outcome += f" ({r.refusal_reason})"
            if q.answerable:
                outcome += " **false refusal**"
        elif r.outcome == "answered" and not q.answerable:
            outcome += " **should refuse**"
        cits = f"{r.n_correct}/{len(r.citations)}" if q.answerable else f"-/{len(r.citations)}"
        cited = f"{r.expected_cited}/{r.expected_resolved}" if q.answerable else "-"
        must = f"{len(r.must_found)}/{len(q.must_contain)}" if q.must_contain else "-"
        out.append(
            f"| {_cell(q.id)} | {q.type} | {q.category or '-'} | {_cell(outcome)} | {cits} "
            f"| {cited} | {must} | {_secs(r.latency_s)} |"
        )
    out.append("")

    wrong = [
        (r, c)
        for r in results
        if r.question.answerable
        for c, ok in zip(r.citations, r.correct, strict=True)
        if not ok
    ]
    out.append("#### Wrong citations")
    out.append("")
    if not wrong:
        out.append("None.")
    for r, c in wrong:
        title = sources[c.source_id].title if c.source_id in sources else c.source_title
        expected = ", ".join(r.question.expected_sources)
        locs = "; ".join(loc.describe() for loc in r.question.expected_locators)
        out.append(
            f"- `{r.question.id}` [{c.n}] {title}: {describe_locator(c.locator)} "
            f"(expected: {expected}{'; ' + locs if locs else ''})"
        )
    out.append("")

    out.extend(render_false_refusals(results))
    out.extend(render_latency(results))

    answered_unans = [r for r in results if not r.question.answerable and r.outcome == "answered"]
    if answered_unans:
        out.append("#### Unanswerable questions that were answered")
        out.append("")
        for r in answered_unans:
            out.append(f"- `{r.question.id}`: {_cell(r.answer_text[:300])}")
        out.append("")

    if ranges is not None:
        out.append("#### Cited ranges")
        out.append("")
        out.append(
            f"Checked against `{ranges.root}`: {ranges.valid.num}/{ranges.valid.den} citations "
            "with a file locator point at a file and line range that exist."
        )
        out.append("")
        for b in ranges.bad:
            span = f" lines {b.start}-{b.end}" if b.start is not None else ""
            out.append(f"- `{b.question_id}` `{_cell(b.file)}`{span}: {b.reason}")
        if ranges.bad:
            out.append("")

    if setup is not None and setup.jobs:
        out.append("#### Ingest jobs")
        out.append("")
        out.append("| Source | Source status | Job status | Expected | Check |")
        out.append("|---|---|---|---|---|")
        for j in setup.jobs:
            check = f"**{j.problem}**" if j.problem else ("ok" + (f" ({j.note})" if j.note else ""))
            out.append(
                f"| {_cell(j.label)} | {j.source_status} | {j.job_status or 'not listed'} "
                f"| {j.expect} | {_cell(check)} |"
            )
        out.append("")

    errors = [r for r in results if r.outcome == "error"]
    if errors:
        out.append("#### Errors")
        out.append("")
        for r in errors:
            out.append(f"- `{r.question.id}`: {_cell(r.error or 'unknown error')}")
        out.append("")

    out.extend(render_sweep(info, results, sources))

    unmatched = sorted({e for r in results for e in unmatched_entries(r.question, sources)})
    notes: list[str] = []
    if unmatched:
        notes.append(
            "expected_sources entries that match no source title or origin in this space "
            "(harmless if another entry for the same source matches): "
            + ", ".join(f"`{e}`" for e in unmatched)
        )
    if setup is not None:
        if setup.timed_out:
            notes.append(
                "**setup timed out**, these sources were not ready: " + ", ".join(setup.not_ready)
            )
        if setup.failed:
            notes.append("failed sources: " + "; ".join(setup.failed))
        if setup.add_failed:
            notes.append("sources that could not be added: " + "; ".join(setup.add_failed))
        if setup.earlier_jobs:
            notes.append(
                f"{setup.earlier_jobs} ingest jobs ended before this run (possibly on another "
                "image) and were not re-checked; `--refresh` re-ingests every corpus source and "
                "checks its job"
            )
        refreshed = f"{len(setup.refreshed)} refreshed, " if setup.refreshed else ""
        notes.append(
            f"setup: {len(setup.added)} added, {refreshed}{len(setup.skipped)} already present, "
            f"waited {setup.seconds:.0f} s"
        )
    if notes:
        out.append("#### Notes")
        out.append("")
        out.extend(f"- {n}" for n in notes)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_false_refusals(results: Sequence[QuestionResult]) -> list[str]:
    """For each false refusal, the sentences that failed verification and why (D-075)."""
    refused = [r for r in results if r.question.answerable and r.outcome == "refused"]
    if not refused:
        return []
    out = ["#### False refusals: failed sentences", ""]
    for r in refused:
        out.append(f"- `{r.question.id}` (refused, {r.refusal_reason or 'no reason'}):")
        if r.failed_sentences is None:
            out.append("  - the API did not report failed sentences")
        elif not r.failed_sentences:
            out.append("  - no failed sentences (the model gave no usable answer)")
        for f in r.failed_sentences or []:
            out.append(f"  - “{_cell(f['text'])}” — {_cell(f['reason'] or 'no reason given')}")
    out.append("")
    return out


def _ratio_cell(r: Ratio) -> str:
    v = r.value
    return f"n/a ({r.num}/{r.den})" if v is None else f"{v * 100:.0f}% ({r.num}/{r.den})"


def render_sweep(
    info: RunInfo, results: Sequence[QuestionResult], sources: dict[str, SourceInfo]
) -> list[str]:
    """The "Threshold sweep" section (estimation rules: eval/format.md)."""
    out = ["#### Threshold sweep", ""]
    if not has_diagnostics(results):
        out += ["No answer diagnostics were returned (API before Phase 2); no sweep.", ""]
        return out
    run = info.thresholds or Thresholds()
    known = "" if info.thresholds else " (not reported by the API; defaults assumed)"
    out.append(
        f"Estimated from the recorded scores; this run used refuse {run.refuse:g}, "
        f'verify {run.verify:g}{known}. See `eval/format.md`, "Threshold sweep", for how '
        "each value is estimated and where it is only a bound."
    )
    out.append("")
    out.append(
        "| Refuse | Verify | Correct refusals (unanswerable) | Answered (answerable) "
        "| Precision proxy |"
    )
    out.append("|---|---|---|---|---|")
    for row in sweep(results, sources, run):
        current = math.isclose(row.refuse, run.refuse) and math.isclose(row.verify, run.verify)
        mark = " **(run)**" if current else ""
        out.append(
            f"| {row.refuse:.2f}{mark} | {row.verify:.1f} | {_ratio_cell(row.correct_refusal)} "
            f"| {_ratio_cell(row.answered)} | {_ratio_cell(row.precision)} |"
        )
    out.append("")
    return out


def thresholds_met(
    m: Metrics,
    min_precision: float | None,
    min_refusal: float | None,
    min_must_contain: float | None = None,
    *,
    max_false_refusals_code: int | None = None,
    max_p95: float | None = None,
    min_ranges_valid: float | None = None,
    ranges: RangeReport | None = None,
) -> bool:
    """True when every given target is met (a value that can't be computed fails)."""
    checks = (
        (m.precision.value, min_precision),
        (m.correct_refusal.value, min_refusal),
        (m.must_contain.value, min_must_contain),
        (ranges.valid.value if ranges is not None else None, min_ranges_valid),
    )
    minimums = all(
        minimum is None or (value is not None and value >= minimum) for value, minimum in checks
    )
    code = m.false_refusal_code
    maximums = (
        max_false_refusals_code is None or (code.den > 0 and code.num <= max_false_refusals_code)
    ) and (max_p95 is None or (m.latency_p95 is not None and m.latency_p95 <= max_p95))
    return minimums and maximums


# ---------------------------------------------------------------------------------------------
# Comparing two runs (--compare A.json B.json)
# ---------------------------------------------------------------------------------------------

# Two top rerank scores closer than this count as the same retrieval when no fingerprint exists.
SAME_SCORE = 1e-5


def load_sidecar(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise EvalError(f"{path}: not a runner sidecar (no 'questions' list)")
    return data


def _question_rows(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(q["id"]): q
        for q in data["questions"]
        if isinstance(q, dict) and q.get("id") is not None
    }


def outcome_cell(q: dict[str, Any]) -> str:
    """``answered 2/3`` (correct/total citations), ``refused (unsupported)``, ``error``."""
    outcome = str(q.get("outcome") or "?")
    if outcome == "refused":
        return f"refused ({q.get('refusal_reason') or '-'})"
    if outcome == "answered":
        cits, correct = q.get("citations"), q.get("citations_correct")
        if q.get("type") == "unanswerable":
            return "answered (should refuse)"
        text = f"answered {correct}/{cits}" if correct is not None else "answered"
        found, total = q.get("must_contain_found"), q.get("must_contain_total")
        if total:
            text += f", must_contain {found}/{total}"
        return text
    return outcome


def retrieval_same(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    """Same context? By ``retrieval_fingerprint`` when both runs have one, else by the top rerank
    score (weaker: the same top chunk, not necessarily the same context). None = unknown."""
    fa, fb = a.get("retrieval_fingerprint"), b.get("retrieval_fingerprint")
    if fa is not None and fb is not None:
        return bool(fa == fb)
    ta, tb = _num(a.get("top_rerank")), _num(b.get("top_rerank"))
    if ta is not None and tb is not None:
        return abs(ta - tb) <= SAME_SCORE
    return None


def outline_same(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    """Same code outline (D-077)? None when either sidecar predates ``outline_fingerprint``."""
    if "outline_fingerprint" not in a or "outline_fingerprint" not in b:
        return None
    return bool(a.get("outline_fingerprint") == b.get("outline_fingerprint"))


def windows_same(a: dict[str, Any], b: dict[str, Any]) -> bool | None:
    """Same transcript windows (D-091)? None when either sidecar predates
    ``window_fingerprint`` (version 5)."""
    if "window_fingerprint" not in a or "window_fingerprint" not in b:
        return None
    return bool(a.get("window_fingerprint") == b.get("window_fingerprint"))


def _run_label(data: dict[str, Any]) -> str:
    chat = data.get("chat") if isinstance(data.get("chat"), dict) else None
    model = f"`{chat.get('model')}` at `{chat.get('endpoint_host')}`" if chat else "chat unknown"
    gen = data.get("generation_requested")
    if isinstance(gen, dict):
        generation = describe_generation(gen)
    elif "generation_requested" in data:
        generation = "app default (real-use)"
    else:
        generation = "generation unknown"
    return f"{data.get('date') or '?'}, {model}, {generation}"


def _precision(rows: Iterable[dict[str, Any]]) -> str:
    num = den = 0
    for q in rows:
        if q.get("type") == "answerable" and isinstance(q.get("citations"), int):
            den += q["citations"]
            num += q.get("citations_correct") or 0
    return _pct(Ratio(num, den))


def compare_runs(a: dict[str, Any], b: dict[str, Any], names: tuple[str, str]) -> str:
    """Markdown: the questions whose outcome differs between two runs, and whether their
    retrieval was the same (then the model or its sampling made the difference) or not (then the
    pipeline or the index did)."""
    qa, qb = _question_rows(a), _question_rows(b)
    out = ["### Run comparison", ""]
    out.append(f"- A: `{names[0]}`: {_run_label(a)}; citation precision {_precision(qa.values())}")
    out.append(f"- B: `{names[1]}`: {_run_label(b)}; citation precision {_precision(qb.values())}")
    out.append("")
    common = [qid for qid in qa if qid in qb]
    changed = [qid for qid in common if outcome_cell(qa[qid]) != outcome_cell(qb[qid])]
    retrieval_moved = [qid for qid in common if retrieval_same(qa[qid], qb[qid]) is False]
    outline_moved = [
        qid
        for qid in common
        if retrieval_same(qa[qid], qb[qid]) is True and outline_same(qa[qid], qb[qid]) is False
    ]
    window_moved = [
        qid
        for qid in common
        if retrieval_same(qa[qid], qb[qid]) is True
        and outline_same(qa[qid], qb[qid]) is not False
        and windows_same(qa[qid], qb[qid]) is False
    ]
    causes = {"model": 0, "outline": 0, "window": 0, "retrieval": 0, "unknown": 0}
    if changed:
        out.append("| ID | A | B | Retrieval | Likely cause |")
        out.append("|---|---|---|---|---|")
        for qid in changed:
            same = retrieval_same(qa[qid], qb[qid])
            fingerprinted = all(
                q.get("retrieval_fingerprint") is not None for q in (qa[qid], qb[qid])
            )
            if same is True and outline_same(qa[qid], qb[qid]) is False:
                where, cause = "same chunks, different outline", "outline"
            elif same is True and windows_same(qa[qid], qb[qid]) is False:
                where, cause = "same chunks, different transcript window", "window"
            elif same is True:
                where = "same" if fingerprinted else "same top score"
                cause = "model"
            elif same is False:
                where, cause = "different", "retrieval"
            else:
                where, cause = "unknown", "unknown"
            causes[cause] += 1
            label = {
                "model": "model / sampling",
                "outline": "outline only",
                "window": "transcript window only",
                "retrieval": "pipeline / index",
                "unknown": "unknown",
            }[cause]
            out.append(
                f"| {_cell(qid)} | {_cell(outcome_cell(qa[qid]))} | {_cell(outcome_cell(qb[qid]))} "
                f"| {where} | {label} |"
            )
        out.append("")
    else:
        out += ["No outcome differences.", ""]
    out.append(
        f"{len(changed)} of {len(common)} questions differ: {causes['model']} with the same "
        f"retrieval (model / sampling), {causes['outline']} with the same chunks and a "
        f"different outline (outline only), "
        + (
            f"{causes['window']} with the same chunks and a different transcript window "
            "(transcript window only), "
            if causes["window"]
            else ""
        )
        + f"{causes['retrieval']} with different retrieval "
        f"(pipeline / index), {causes['unknown']} unknown (no fingerprint or scores). "
        f"Retrieval differs on {len(retrieval_moved)} question(s) in all"
        + (": " + ", ".join(f"`{q}`" for q in retrieval_moved) if retrieval_moved else "")
        + "."
    )
    if outline_moved:
        out.append(
            f"Only the outline differs on {len(outline_moved)} question(s): "
            + ", ".join(f"`{q}`" for q in outline_moved)
            + "."
        )
    if window_moved:
        out.append(
            f"Only the transcript windows differ on {len(window_moved)} question(s): "
            + ", ".join(f"`{q}`" for q in window_moved)
            + "."
        )
    only_a = [qid for qid in qa if qid not in qb]
    only_b = [qid for qid in qb if qid not in qa]
    if only_a:
        out.append(f"Only in A: {', '.join(f'`{q}`' for q in only_a)}.")
    if only_b:
        out.append(f"Only in B: {', '.join(f'`{q}`' for q in only_b)}.")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def _fraction(value: str) -> float:
    v = float(value)
    if v > 1:
        v /= 100.0  # accept 90 as well as 0.9
    if not 0 <= v <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1 (or 0 and 100)")
    return v


def _count(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return n


def _seconds(value: str) -> float:
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return v


def _temperature(value: str) -> float:
    v = float(value)
    if not math.isfinite(v) or not 0 <= v <= 2:
        raise argparse.ArgumentTypeError("must be between 0 and 2")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval/runner.py",
        description="Run Trustbook evaluation questions against a space and write a report.",
    )
    p.add_argument("--base-url", default="http://localhost:8484", help="Trustbook URL")
    p.add_argument("--space", help="space id, slug or name (required unless --compare)")
    p.add_argument(
        "--questions",
        action="append",
        help="question YAML file or directory (repeatable; required unless --compare)",
    )
    p.add_argument(
        "--report",
        help="also write the Markdown report to this file, and the scores to the same name "
        "with .json",
    )
    p.add_argument("--setup", help="corpus YAML to ingest into the space before asking")
    p.add_argument(
        "--refresh",
        action="store_true",
        help="with --setup: also re-ingest every corpus source already in the space, so that "
        "all are indexed and their jobs checked on the running image",
    )
    p.add_argument("--setup-timeout", type=float, default=3600.0, help="seconds (default 3600)")
    p.add_argument("--poll-interval", type=float, default=5.0, help=argparse.SUPPRESS)
    p.add_argument("--timeout", type=float, default=300.0, help="seconds per answer (default 300)")
    p.add_argument("--min-precision", type=_fraction, help="exit 1 if citation precision is lower")
    p.add_argument("--min-refusal", type=_fraction, help="exit 1 if correct-refusal rate is lower")
    p.add_argument(
        "--min-must-contain", type=_fraction, help="exit 1 if the must_contain hit rate is lower"
    )
    p.add_argument(
        "--max-false-refusals-code",
        type=_count,
        metavar="N",
        help="exit 1 if more than N answerable code questions are refused (category code, or an "
        "expected file locator that is a code file)",
    )
    p.add_argument(
        "--max-p95",
        type=_seconds,
        metavar="SECONDS",
        help=f"exit 1 if the p95 answer latency is higher (flagged above {P95_FLAG_S:g} s anyway)",
    )
    p.add_argument(
        "--code-root",
        metavar="PATH",
        help="checkout of the corpus at the indexed commit; checks that every cited file and "
        "line range exists there",
    )
    p.add_argument(
        "--min-ranges-valid",
        type=_fraction,
        help="exit 1 if the share of valid cited ranges is lower (needs --code-root)",
    )
    gen = p.add_argument_group(
        "generation",
        "Every question is sent with a generation override (default temperature "
        f"{DEFAULT_TEMPERATURE:g}, seed {DEFAULT_SEED}) so runs can be compared. {SEED_CAVEAT}",
    )
    gen.add_argument(
        "--temperature",
        type=_temperature,
        help=f"temperature for the eval override (default {DEFAULT_TEMPERATURE:g})",
    )
    gen.add_argument(
        "--seed", type=int, help=f"seed for the eval override (default {DEFAULT_SEED})"
    )
    gen.add_argument(
        "--app-default",
        action="store_true",
        help="send no override: a real-use run with the app's own settings "
        "(temperature 0.2, no seed)",
    )
    chat = p.add_argument_group(
        "chat model",
        "Switch the chat capability for this run; the saved settings are restored afterwards, "
        "also on an error or Ctrl-C. API keys are never read, sent or printed.",
    )
    chat.add_argument(
        "--chat-endpoint",
        metavar="URL",
        help="OpenAI-compatible base URL (saved as the 'custom' preset for the run)",
    )
    chat.add_argument("--chat-model", metavar="NAME", help="chat model name for the run")
    p.add_argument(
        "--compare",
        nargs=2,
        metavar=("A.json", "B.json"),
        help="print the per-question outcome differences between two runs' sidecars and exit",
    )
    return p


def generation_override(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.app_default:
        return None
    temperature = DEFAULT_TEMPERATURE if args.temperature is None else args.temperature
    seed = DEFAULT_SEED if args.seed is None else args.seed
    return {"temperature": temperature, "seed": seed}


def _interrupt(signum: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def run_compare(paths: Sequence[str], out: TextIO, log: TextIO) -> int:
    try:
        a, b = load_sidecar(paths[0]), load_sidecar(paths[1])
    except EvalError as exc:
        print(f"error: {exc}", file=log)
        return EXIT_UNAVAILABLE
    out.write(compare_runs(a, b, (paths[0], paths[1])))
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    out: TextIO | None = None,
    log: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = out or sys.stdout
    log = log or sys.stderr
    if args.compare:
        return run_compare(args.compare, out, log)
    if not args.space or not args.questions:
        parser.error("--space and --questions are required (unless --compare)")
    if args.min_ranges_valid is not None and not args.code_root:
        parser.error("--min-ranges-valid needs --code-root")
    if args.refresh and not args.setup:
        parser.error("--refresh needs --setup")
    if args.app_default and (args.temperature is not None or args.seed is not None):
        parser.error("--app-default sends no override; drop --temperature/--seed")
    generation = generation_override(args)
    started = datetime.now(UTC)
    try:
        questions = load_questions(args.questions)
        corpus = load_corpus(args.setup) if args.setup else None
        if args.code_root and not Path(args.code_root).is_dir():
            raise EvalError(f"--code-root is not a folder: {args.code_root}")
    except EvalError as exc:
        print(f"error: {exc}", file=log)
        return EXIT_UNAVAILABLE

    timeout = httpx.Timeout(args.timeout, connect=10.0)
    overriding = args.chat_endpoint is not None or args.chat_model is not None
    with httpx.Client(base_url=args.base_url, timeout=timeout, transport=transport) as client:
        api = Api(client)
        original_chat: ChatConfig | None = None
        try:
            health = api.health()
            if overriding:
                saved = ChatConfig.from_capability(api.capability("chat"))
                body = chat_override_body(saved, args.chat_endpoint, args.chat_model)
                original_chat = saved
                apply_chat_override(api, body, log)
            chat = read_chat_info(api, overridden=overriding)
            if chat is not None:
                print(
                    f"chat model {chat.model or 'unknown'} at {chat.endpoint_host or 'unknown'}; "
                    + (
                        f"generation {describe_generation(generation)}"
                        if generation is not None
                        else "generation: app default (real-use run)"
                    ),
                    file=log,
                )
            setup: SetupReport | None = None
            if corpus is not None:
                space, setup = setup_corpus(
                    api,
                    args.space,
                    corpus,
                    timeout_s=args.setup_timeout,
                    refresh=args.refresh,
                    poll_s=args.poll_interval,
                    log=log,
                    sleep=sleep,
                    clock=clock,
                )
            else:
                found = find_space(api.spaces(), args.space)
                if found is None:
                    raise EvalError(f"space not found: {args.space} (use --setup to create it)")
                space = found
            sid = str(space["id"])
            sources = {s.id: s for s in api.sources(sid)}
            run_thresholds = api.thresholds()
            run_retrieval = api.retrieval_settings()
            results: list[QuestionResult] = []
            for i, q in enumerate(questions, 1):
                r = ask(client, sid, q, clock=clock, generation=generation)
                score(r, sources)
                results.append(r)
                print(
                    f"[{i}/{len(questions)}] {q.id}: {r.outcome}, "
                    f"{r.n_correct}/{len(r.citations)} citations correct, {_secs(r.latency_s)}",
                    file=log,
                )
        except ApiUnreachableError as exc:
            print(f"error: Trustbook API unreachable at {args.base_url}: {exc}", file=log)
            return EXIT_UNAVAILABLE
        except EvalError as exc:
            print(f"error: {exc}", file=log)
            return EXIT_UNAVAILABLE
        except KeyboardInterrupt:
            print("interrupted", file=log)
            return EXIT_INTERRUPTED
        finally:
            if original_chat is not None:
                restore_chat(api, original_chat, log)

    info = RunInfo(
        base_url=args.base_url,
        space_name=str(space.get("name", args.space)),
        space_id=sid,
        question_files=tuple(args.questions),
        api_version=str(health["version"]) if health.get("version") else None,
        started_at=started,
        min_precision=args.min_precision,
        min_refusal=args.min_refusal,
        min_must_contain=args.min_must_contain,
        thresholds=run_thresholds,
        max_false_refusals_code=args.max_false_refusals_code,
        max_p95=args.max_p95,
        min_ranges_valid=args.min_ranges_valid,
        generation=generation,
        chat=chat,
        retrieval=run_retrieval,
    )
    ranges = check_ranges(results, args.code_root) if args.code_root else None
    report = render_report(info, results, sources, setup, ranges)
    out.write(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"report written to {args.report}", file=log)
        data = sidecar(
            info, results, sweep(results, sources, run_thresholds or Thresholds()), setup
        )
        if ranges is not None:
            data["cited_ranges"] = _ratio_json(ranges.valid)
        json_path = sidecar_path(args.report)
        json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"scores written to {json_path}", file=log)
    met = thresholds_met(
        compute_metrics(results),
        args.min_precision,
        args.min_refusal,
        args.min_must_contain,
        max_false_refusals_code=args.max_false_refusals_code,
        max_p95=args.max_p95,
        min_ranges_valid=args.min_ranges_valid,
        ranges=ranges,
    )
    if setup is not None and setup.job_problems:
        print(f"ingest jobs not as expected: {len(setup.job_problems)}", file=log)
        met = False
    if not met:
        print("thresholds not met", file=log)
        return EXIT_THRESHOLD
    return EXIT_OK


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _interrupt)  # restore the chat settings on `kill` too
    sys.exit(main())
