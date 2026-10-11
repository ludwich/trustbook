"""Grounding layer 5: deterministic code checks (SPEC §8).

- Every backticked identifier in a sentence must occur verbatim in at least one cited chunk.
  The only normalisation is a trailing ``()`` (prose writes ``get()`` for a function ``get``).
- Every cited chunk whose locator names a ``file`` must point at a file, and line range, that
  exist in the source's indexed snapshot (the ``files`` segments of ``map.json``).

No model, no network: plain string and file checks.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("trustbook.grounding")

_BACKTICKED = re.compile(r"`([^`\n]+)`")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

LineRange = tuple[int, int]


def backticked(text: str) -> list[str]:
    """The backticked identifiers of ``text``, unique, in order (``get()`` → ``get``)."""
    found: dict[str, None] = {}
    for match in _BACKTICKED.finditer(text):
        token = match.group(1).strip()
        if token.endswith("()") and len(token) > 2:
            token = token[:-2]
        if token:
            found.setdefault(token, None)
    return list(found)


_NAME_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?")
_MARKDOWN_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")


def unescape_markdown(text: str) -> str:
    """Markdown backslash escapes removed (``trust\\_env`` → ``trust_env``): web pages and
    converted documents are stored as Markdown, and a name must match what the reader sees."""
    return _MARKDOWN_ESCAPE.sub(r"\1", text)


def verbatim(span: str, text: str) -> bool:
    """``span`` occurs in ``text`` and is not part of a longer name: an edge of the span that is
    a name character may not continue into one (`socks5` is not in ``socks5h``, D-056)."""
    left = r"(?<![A-Za-z0-9_])" if span[:1].isalnum() or span[:1] == "_" else ""
    right = r"(?![A-Za-z0-9_])" if span[-1:].isalnum() or span[-1:] == "_" else ""
    return re.search(left + re.escape(span) + right, text) is not None


def _occurs(token: str, texts: list[str]) -> bool:
    """``token`` as a whole word in any of ``texts`` (exact, case-sensitive)."""
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])")
    return any(pattern.search(t) for t in texts)


def missing_identifiers(text: str, chunk_texts: Iterable[str]) -> list[str]:
    """Backticked names of ``text`` not found in ``chunk_texts`` (exact, deterministic).

    A backticked span passes if it occurs verbatim in a cited chunk (not inside a longer name),
    or if every identifier and
    number inside it occurs there as a whole word: code defines ``class Response`` and
    ``def ok``, so ``Response.ok`` never appears verbatim (D-042). A span without identifiers
    (e.g. a quoted literal) must occur verbatim.
    """
    texts = [unescape_markdown(t) for t in chunk_texts]
    missing: list[str] = []
    for token in backticked(text):
        if any(verbatim(token, t) for t in texts):
            continue
        parts = _NAME_PART.findall(token)
        if not parts or not all(_occurs(part, texts) for part in parts):
            missing.append(token)
    return missing


def _line(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def files_from_map(data: Mapping[str, Any]) -> dict[str, list[LineRange | None]]:
    """``file → line ranges`` of a ``map.json`` (None for a segment without line numbers)."""
    files: dict[str, list[LineRange | None]] = {}
    segments = data.get("segments")
    if not isinstance(segments, list):
        return files
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        file = segment.get("file")
        if not isinstance(file, str) or not file:
            continue
        start, end = _line(segment.get("start_line")), _line(segment.get("end_line"))
        files.setdefault(file, []).append((start, end) if start and end else None)
    return files


class SnapshotFiles:
    """Files and line ranges of each source's indexed snapshot, read once per instance.

    ``sources_dir`` is a space's ``sources/`` folder. A source without a readable ``map.json``
    has no files, so every file citation of it fails (grounding first).
    """

    def __init__(self, sources_dir: Path | None) -> None:
        self._dir = sources_dir
        self._cache: dict[str, dict[str, list[LineRange | None]]] = {}
        self._lock = threading.Lock()

    def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
        with self._lock:
            cached = self._cache.get(source_id)
            if cached is None:
                cached = self._read(source_id)
                self._cache[source_id] = cached
            return cached

    def _read(self, source_id: str) -> dict[str, list[LineRange | None]]:
        if self._dir is None or not _SAFE_ID.match(source_id):
            return {}
        path = self._dir / source_id / "map.json"
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            logger.info("No readable map.json for source %s; its file citations fail", source_id)
            return {}
        return files_from_map(data) if isinstance(data, dict) else {}


def locator_problem(files: SnapshotFiles, source_id: str, locator: Mapping[str, Any]) -> str | None:
    """Why a cited chunk's file/line range does not exist in the snapshot (None when it does).

    Locators without a ``file`` (pages, sections, timestamps) are not code and pass.
    """
    file = locator.get("file")
    if not isinstance(file, str) or not file:
        return None
    ranges = files.files(source_id).get(file)
    if ranges is None:
        return f"{file} is not in the indexed snapshot"
    raw_start, raw_end = locator.get("start_line"), locator.get("end_line")
    if raw_start is None and raw_end is None:
        return None
    start, end = _line(raw_start), _line(raw_end)
    if start is None or end is None or start < 1 or end < start:
        return f"{file} has an invalid line range"
    if any(r is not None and r[0] <= start and end <= r[1] for r in ranges):
        return None
    return f"lines {start}-{end} are not in {file}"


# --- focused premises and literal checks (D-044) -----------------------------------------------

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?")
_PROSE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = frozenset(
    "the a an and or of to in on for with by is are was were be been it its this that these "
    "those as at from not no if then than when which who what how does do did can may will "
    "default defaults set uses use used value values".split()
)
FOCUS_MAX_CHARS = 1_200
FOCUS_MAX_UNITS = 6
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_])")
# True/False/None only where the sentence states a value ("is False", "defaults to None"), not
# behaviour ("returns True if the host changes"), which code rarely spells as a literal.
_VALUE_CONSTANT = re.compile(
    r"\b(?:is|are|defaults?(?:\s+to)?|(?:is\s+)?set\s+to|equals?|=)\s+(True|False|None)\b"
)


def _terms(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if w.lower() not in _STOPWORDS and len(w) > 1}


_ASSIGN = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[^=]+?)?\s*=\s*"
    r"(?P<value>[^#]+?)\s*,?\s*(?:#.*)?$"
)
_SIMPLE_VALUE = re.compile(
    r"""^(?:-?\d+(?:\.\d+)?|True|False|None|[A-Z][A-Z0-9_]+|[A-Za-z_][A-Za-z0-9_.]*\(\s*\)|"""
    r"""b?"[^"]{0,60}"|b?'[^']{0,60}')$"""
)


def code_facts(chunk_texts: Sequence[str]) -> list[str]:
    """Plain statements for the simple facts the cited code states (D-044).

    ``NAME = value`` → "NAME is value."; a parameter line ``name: type = DEFAULT,`` → "name defaults
    to DEFAULT, which is 10." when ``DEFAULT = 10`` is in the cited code too. Only literals,
    constant names and empty calls are restated; nothing is inferred beyond what the lines say.
    """
    constants: dict[str, str] = {}
    found: list[tuple[str, str, bool]] = []  # (name, value, is_parameter)
    for text in chunk_texts:
        if not text.startswith("File: "):
            continue
        for line in text.splitlines()[1:]:
            match = _ASSIGN.match(line)
            if not match or match.group("name") in {"self", "cls"}:
                continue
            value = match.group("value").strip()
            if not _SIMPLE_VALUE.match(value):
                continue
            name = match.group("name")
            parameter = line.rstrip().endswith(",") or "self." in line
            if not line.startswith((" ", "\t")) and name.isupper():
                constants[name] = value
            found.append((name, value, parameter))
    facts: list[str] = []
    for name, value, parameter in found:
        resolved = constants.get(value)
        tail = f", which is {resolved}" if resolved is not None and resolved != value else ""
        verb = "defaults to" if parameter else "is"
        facts.append(f"{name} {verb} {value}{tail}.")
    return list(dict.fromkeys(facts))


def text_units(text: str) -> list[str]:
    """A chunk's units: its lines for code (``File: …`` header left out), sentences for prose."""
    if text.startswith("File: "):
        parts = text.splitlines()[1:]
    else:
        parts = _PROSE_SPLIT.split(" ".join(text.split()))
    return [part.strip() for part in parts if part.strip()]


def support_window(sentence: str, text: str) -> str:
    """The units of one chunk that share the most terms with ``sentence`` (all ties), in order.

    This is the part of the chunk that states the sentence; two chunks whose windows are the same
    text are copies of one passage as far as the sentence is concerned (D-052 extension).
    """
    wanted = _terms(sentence)
    units = text_units(text)
    scores = [len(_terms(u) & wanted) for u in units]
    best = max(scores, default=0)
    if best == 0:
        return ""
    return "\n".join(u for u, score in zip(units, scores, strict=True) if score == best)


def focus_premise(
    sentence: str, chunk_texts: Sequence[str], max_chars: int = FOCUS_MAX_CHARS
) -> str:
    """Restated code facts, lines (code) and sentences (prose) of the cited chunks that share terms
    with ``sentence``, at most ``max_chars``.

    The grounding checker was trained on short prose; a 900-token chunk drowns the few lines that
    state a claim, so true and false claims about code both scored near 0 (D-044). Everything in
    the window comes from cited text: lines verbatim, or simple facts restated by
    :func:`code_facts`.
    """
    wanted = _terms(sentence)
    if not wanted:
        return ""
    facts = code_facts(chunk_texts)
    n_facts = len(facts)
    units: list[tuple[str, bool]] = [(f, False) for f in facts]
    for text in chunk_texts:
        units.extend((u, not text.startswith("File: ")) for u in text_units(text))
    scores = [len(_terms(u) & wanted) for u, _ in units]
    best = max(scores, default=0)
    if best == 0:
        return ""
    # The best-matching units first (restated facts before raw lines at equal overlap), then the
    # rest that share at least two terms; at most FOCUS_MAX_UNITS so noise can't drown the fact.
    order = sorted(
        (i for i, s in enumerate(scores) if s == best or s >= 2),
        key=lambda i: (-scores[i], i >= n_facts, i),
    )[:FOCUS_MAX_UNITS]
    chosen: list[str] = []
    size = 0
    for i in order:
        unit = units[i][0]
        if unit in chosen or size + len(unit) + 1 > max_chars:
            continue
        chosen.append(unit)
        size += len(unit) + 1
    return "\n".join(chosen)


def missing_literals(text: str, chunk_texts: Iterable[str]) -> list[str]:
    """Numbers, and ``True``/``False``/``None`` stated as values, that no cited chunk contains.

    Used for sentences citing code, where the grounding checker cannot tell ``True`` from
    ``False`` reliably (D-044).
    """
    texts = list(chunk_texts)
    found = dict.fromkeys(
        [m.group(0) for m in _NUMBER.finditer(text)]
        + [m.group(1) for m in _VALUE_CONSTANT.finditer(text)]
    )
    return [lit for lit in found if not _occurs(lit, texts)]
