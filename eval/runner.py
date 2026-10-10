#!/usr/bin/env python3
"""Trustbook evaluation runner (SPEC §8 layer 6, §13).

Asks every question in one or more YAML question files (format: ``eval/format.md``) against a
space of a running Trustbook, then prints a Markdown report ready to paste into ``docs/GATES.md``:
citation precision, correct-refusal rate, false refusals, answer latency p50/p95 and the
informational ``must_contain`` hit rate.

Usage (from the repo root, with the backend's virtualenv)::

    uv run --project backend python eval/runner.py --base-url http://localhost:8484 \\
        --space "requests smoke" --questions eval/smoke/questions.yaml \\
        --setup eval/smoke/corpus.yaml --report eval-report.md

Exit codes: 0 = report written; 1 = a ``--min-*`` threshold was not met; 2 = the API is
unreachable or the input (questions, corpus, space) is invalid.

Only ``httpx`` and ``PyYAML`` are used. The runner talks to the API contract in
``docs/ARCHITECTURE.md`` ("Phase 1: sources, index, retrieval and chat").
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit, urlunsplit

import httpx
import yaml

QUESTION_TYPES = ("answerable", "unanswerable")
CATEGORIES = ("docs", "code", "multi", "unanswerable")
SOURCE_DONE = ("ready", "failed")
EXIT_OK = 0
EXIT_THRESHOLD = 1
EXIT_UNAVAILABLE = 2


class EvalError(Exception):
    """Invalid input or an unusable API answer (exit code 2)."""


class ApiUnreachableError(EvalError):
    """The Trustbook API cannot be reached."""


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
# SSE
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SseEvent:
    event: str
    data: str


def iter_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Parse a ``text/event-stream`` (WHATWG rules, minus ``id``/``retry`` which are unused).

    Data lines are joined with ``\\n``; comment lines (``:``) are ignored; an event without a
    blank line before the end of the stream is still delivered.
    """
    event = ""
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield SseEvent(event or "message", "\n".join(data))
            event, data = "", []
            continue
        if line.startswith(":"):
            continue
        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield SseEvent(event or "message", "\n".join(data))


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
        )


_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")


def norm_text(value: str) -> str:
    """Case-, whitespace-, entity- and dash-insensitive form used for titles and headings."""
    value = unicodedata.normalize("NFKC", html.unescape(value)).translate(_DASHES)
    value = value.replace("\u00b6", "").strip().strip("#").strip()
    return " ".join(value.casefold().split())


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
        heading = loc.get("heading")
        if not isinstance(heading, str):
            return False
        want = norm_text(expected.heading)
        return want == norm_text(heading) or want in _heading_parts(heading)
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
    )


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


def _json(resp: httpx.Response, what: str) -> Any:
    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            body = resp.json()
            if isinstance(body, dict) and "detail" in body:
                detail = str(body["detail"])
        except ValueError:
            pass
        raise EvalError(f"{what} failed: HTTP {resp.status_code}: {detail}")
    try:
        return resp.json()
    except ValueError as exc:
        raise EvalError(f"{what}: response is not JSON") from exc


class Api:
    """Thin wrapper over the Trustbook HTTP API (docs/ARCHITECTURE.md)."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def _request(self, method: str, url: str, what: str, **kwargs: Any) -> Any:
        try:
            resp = self.client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise ApiUnreachableError(f"{what}: {type(exc).__name__}: {exc}") from exc
        return _json(resp, what)

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


def find_space(spaces: Iterable[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Match ``--space`` by id, then slug, then name (case-insensitive)."""
    spaces = list(spaces)
    for field_name in ("id", "slug"):
        for s in spaces:
            if str(s.get(field_name, "")) == key:
                return s
    for s in spaces:
        if norm_text(str(s.get("name", ""))) == norm_text(key):
            return s
    return None


def ask(
    client: httpx.Client,
    space_id: str,
    question: Question,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> QuestionResult:
    """Ask one question in a fresh conversation and consume the SSE stream."""
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
    start = clock()
    try:
        with client.stream(
            "POST",
            url,
            json={"content": question.question},
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                result.error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                return result
            for ev in iter_sse(resp.iter_lines()):
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
        unique: dict[str, Citation] = {}
        for _, cits in sentences:
            for c in cits:
                unique.setdefault(c.key, c)
        result.citations = list(unique.values())
    return result


def _sentence(data: dict[str, Any]) -> tuple[str, list[Citation]]:
    cits = data.get("citations")
    citations = [Citation.from_api(c) for c in cits] if isinstance(cits, list) else []
    return (str(data.get("text", "")), citations)


# ---------------------------------------------------------------------------------------------
# Corpus setup
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusEntry:
    kind: str  # folder | url
    value: str


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
        kind = "folder" if "folder" in raw else "url"
        value = raw[kind]
        if not isinstance(value, str) or not value.strip():
            raise EvalError(f"{path}: sources[{i}].{kind} must be a non-empty string")
        if kind == "url" and not is_url(value):
            raise EvalError(f"{path}: sources[{i}].url is not a URL: {value}")
        entries.append(CorpusEntry(kind, value.strip()))
    description = data.get("space_description", "")
    return Corpus(description=str(description or ""), entries=tuple(entries))


def entry_present(entry: CorpusEntry, source: SourceInfo) -> bool:
    if source.origin is None:
        return False
    if entry.kind == "url":
        return is_url(source.origin) and norm_url(entry.value) == norm_url(source.origin)
    return source.type == "folder" and paths_match(entry.value, source.origin)


@dataclass
class SetupReport:
    space_created: bool = False
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    add_failed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    not_ready: list[str] = field(default_factory=list)
    timed_out: bool = False
    seconds: float = 0.0


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
    poll_s: float = 5.0,
    log: TextIO = sys.stderr,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], SetupReport]:
    """Create the space if missing, add missing sources, wait until all are ready or failed.

    Idempotent: a corpus entry whose origin already exists in the space is skipped.
    """
    report = SetupReport()
    space = find_space(api.spaces(), space_key)
    if space is None:
        space = api.create_space(space_key, corpus.description)
        report.space_created = True
        print(f"created space {space.get('name')} ({space['id']})", file=log)
    sid = str(space["id"])
    existing = api.sources(sid)
    tracked: set[str] = set()
    for entry in corpus.entries:
        match = next((s for s in existing if entry_present(entry, s)), None)
        if match is not None:
            tracked.add(match.id)
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
        tracked.add(created.id)
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
    report.seconds = clock() - start
    return space, report


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


def render_report(
    info: RunInfo,
    results: Sequence[QuestionResult],
    sources: dict[str, SourceInfo],
    setup: SetupReport | None = None,
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
    out.append("")
    p_target, p_res = _verdict(m.precision.value, info.min_precision)
    r_target, r_res = _verdict(m.correct_refusal.value, info.min_refusal)
    out.append("| Metric | Value | Target | Result |")
    out.append("|---|---|---|---|")
    out.append(f"| Citation precision (answerable) | {_pct(m.precision)} | {p_target} | {p_res} |")
    out.append(
        f"| Correct-refusal rate (unanswerable) | {_pct(m.correct_refusal)} "
        f"| {r_target} | {r_res} |"
    )
    out.append(
        f"| False refusals (answerable) | {m.false_refusal.num}/{m.false_refusal.den} | - | - |"
    )
    out.append(f"| Errors | {m.errors} | - | - |")
    out.append(
        f"| Answer latency p50 / p95 ({m.latency_count} answers) | "
        f"{_secs(m.latency_p50)} / {_secs(m.latency_p95)} | - | - |"
    )
    out.append(f"| must_contain hit rate (informational) | {_pct(m.must_contain)} | - | - |")
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

    answered_unans = [r for r in results if not r.question.answerable and r.outcome == "answered"]
    if answered_unans:
        out.append("#### Unanswerable questions that were answered")
        out.append("")
        for r in answered_unans:
            out.append(f"- `{r.question.id}`: {_cell(r.answer_text[:300])}")
        out.append("")

    errors = [r for r in results if r.outcome == "error"]
    if errors:
        out.append("#### Errors")
        out.append("")
        for r in errors:
            out.append(f"- `{r.question.id}`: {_cell(r.error or 'unknown error')}")
        out.append("")

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
        notes.append(
            f"setup: {len(setup.added)} added, {len(setup.skipped)} already present, "
            f"waited {setup.seconds:.0f} s"
        )
    if notes:
        out.append("#### Notes")
        out.append("")
        out.extend(f"- {n}" for n in notes)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def thresholds_met(m: Metrics, min_precision: float | None, min_refusal: float | None) -> bool:
    ok = True
    if min_precision is not None:
        ok = ok and m.precision.value is not None and m.precision.value >= min_precision
    if min_refusal is not None:
        ok = ok and m.correct_refusal.value is not None and m.correct_refusal.value >= min_refusal
    return ok


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval/runner.py",
        description="Run Trustbook evaluation questions against a space and write a report.",
    )
    p.add_argument("--base-url", default="http://localhost:8484", help="Trustbook URL")
    p.add_argument("--space", required=True, help="space id, slug or name")
    p.add_argument(
        "--questions",
        action="append",
        required=True,
        help="question YAML file or directory (repeatable)",
    )
    p.add_argument("--report", help="also write the Markdown report to this file")
    p.add_argument("--setup", help="corpus YAML to ingest into the space before asking")
    p.add_argument("--setup-timeout", type=float, default=3600.0, help="seconds (default 3600)")
    p.add_argument("--poll-interval", type=float, default=5.0, help=argparse.SUPPRESS)
    p.add_argument("--timeout", type=float, default=300.0, help="seconds per answer (default 300)")
    p.add_argument("--min-precision", type=_fraction, help="exit 1 if citation precision is lower")
    p.add_argument("--min-refusal", type=_fraction, help="exit 1 if correct-refusal rate is lower")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    out: TextIO | None = None,
    log: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    log = log or sys.stderr
    started = datetime.now(UTC)
    try:
        questions = load_questions(args.questions)
        corpus = load_corpus(args.setup) if args.setup else None
    except EvalError as exc:
        print(f"error: {exc}", file=log)
        return EXIT_UNAVAILABLE

    timeout = httpx.Timeout(args.timeout, connect=10.0)
    with httpx.Client(base_url=args.base_url, timeout=timeout, transport=transport) as client:
        api = Api(client)
        try:
            health = api.health()
            setup: SetupReport | None = None
            if corpus is not None:
                space, setup = setup_corpus(
                    api,
                    args.space,
                    corpus,
                    timeout_s=args.setup_timeout,
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
            results: list[QuestionResult] = []
            for i, q in enumerate(questions, 1):
                r = ask(client, sid, q, clock=clock)
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

    info = RunInfo(
        base_url=args.base_url,
        space_name=str(space.get("name", args.space)),
        space_id=sid,
        question_files=tuple(args.questions),
        api_version=str(health["version"]) if health.get("version") else None,
        started_at=started,
        min_precision=args.min_precision,
        min_refusal=args.min_refusal,
    )
    report = render_report(info, results, sources, setup)
    out.write(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"report written to {args.report}", file=log)
    if not thresholds_met(compute_metrics(results), args.min_precision, args.min_refusal):
        print("thresholds not met", file=log)
        return EXIT_THRESHOLD
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
