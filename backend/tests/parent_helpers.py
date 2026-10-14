"""Helpers for parent context tests (D-077): chunk rows with parent keys and structure records.

Rows are written straight into ``space.db`` (the parent columns of migration 005 are added here
when the schema does not have them yet) and indexed with the keyword embedder, so retrieval,
the lookups and the expansion read them through the real store queries. Nothing is chunked:
slice A computes the structure at index time; these fixtures carry it by hand.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.chunking.structure import Import, Structure, class_key, module_key, structure_json
from app.grounding.code_checks import LineRange, SnapshotFiles
from app.index.base import SpaceIndex
from app.retrieval import service
from app.retrieval.store import ChunkRow, chunks_at_ordinals, load_chunks
from tests.chat_helpers import add_chunk

ADAPTERS = "src/requests/adapters.py"
HISTORY = "HISTORY.md"
MODULE = module_key(ADAPTERS)
HTTP_ADAPTER = class_key(ADAPTERS, "HTTPAdapter")
BASE_ADAPTER = class_key(ADAPTERS, "BaseAdapter")

IMPORTS = (
    Import(1, 1, "import os.path", ("os",), None),
    Import(3, 3, "from urllib3.util.retry import Retry", ("Retry",), None),
    Import(
        4,
        8,
        "from .compat import (basestring, urlparse, unquote)",
        ("basestring", "urlparse", "unquote"),
        "src/requests/compat.py",
    ),
    Import(9, 9, "from .auth import _basic_auth_str", ("_basic_auth_str",), "src/requests/auth.py"),
)
MODULE_STRUCTURE = Structure(
    "module",
    ADAPTERS,
    None,
    True,
    defines=("DEFAULT_POOLBLOCK", "DEFAULT_POOLSIZE", "DEFAULT_RETRIES", "DEFAULT_POOL_TIMEOUT"),
    imports=IMPORTS,
)
MODULE_TEXT = (
    f"File: {ADAPTERS} (lines 1-20)\n"
    "import os.path\n\n"
    "from urllib3.util.retry import Retry\n"
    "from .compat import (\n    basestring,\n    urlparse,\n    unquote,\n)\n"
    "from .auth import _basic_auth_str\n\n"
    "DEFAULT_POOLBLOCK = False\nDEFAULT_POOLSIZE = 10\nDEFAULT_RETRIES = 0\n"
    "DEFAULT_POOL_TIMEOUT = None"
)


def ensure_parent_columns(engine: Engine) -> None:
    """Add the migration 005 columns when the schema does not have them yet."""
    with engine.begin() as conn:
        names = {r[1] for r in conn.execute(text("PRAGMA table_info(chunks)"))}
        for column in ("parent_key", "structure"):
            if column not in names:
                conn.execute(text(f"ALTER TABLE chunks ADD COLUMN {column} TEXT"))


def set_parent(
    engine: Engine, chunk_id: str, parent_key: str | None, structure: Structure | None
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE chunks SET parent_key = :key, structure = :structure WHERE id = :id"),
            {
                "key": parent_key,
                "structure": structure_json(structure) if structure is not None else None,
                "id": chunk_id,
            },
        )


@dataclass(frozen=True)
class Spec:
    """One chunk row: ordinal, text, locator, parent key and structure."""

    ordinal: int
    text: str
    locator: dict[str, Any]
    parent_key: str | None = None
    structure: Structure | None = None
    tokens: int | None = None


def add_rows(
    engine: Engine, index: SpaceIndex, source_id: str, specs: Sequence[Spec]
) -> dict[int, str]:
    """Write and index ``specs`` (in the given order); chunk ids by ordinal."""
    ensure_parent_columns(engine)
    ids: dict[int, str] = {}
    for spec in specs:
        chunk_id = add_chunk(
            engine,
            index,
            source_id,
            spec.text,
            ordinal=spec.ordinal,
            locator=spec.locator,
            token_count=spec.tokens,
        )
        set_parent(engine, chunk_id, spec.parent_key, spec.structure)
        ids[spec.ordinal] = chunk_id
    return ids


def code(
    ordinal: int,
    symbol: str | None,
    kind: str,
    start: int,
    end: int,
    body: str,
    *,
    parent_key: str | None,
    structure: Structure | None,
    file: str = ADAPTERS,
    tokens: int | None = None,
) -> Spec:
    header = (
        f"File: {file} · {symbol} (lines {start}-{end})"
        if symbol
        else (f"File: {file} (lines {start}-{end})")
    )
    locator: dict[str, Any] = {"file": file, "start_line": start, "end_line": end, "kind": kind}
    if symbol:
        locator["symbol"] = symbol
    return Spec(ordinal, f"{header}\n{body}", locator, parent_key, structure, tokens)


def method(
    ordinal: int, cls: str, name: str, start: int, end: int, body: str, *, tokens: int | None = None
) -> Spec:
    key = class_key(ADAPTERS, cls)
    return code(
        ordinal,
        f"{cls}.{name}",
        "method",
        start,
        end,
        body,
        parent_key=key,
        structure=Structure("class", cls, MODULE, False, defines=(name,)),
        tokens=tokens,
    )


def class_head(
    ordinal: int, cls: str, decl: str, start: int, end: int, body: str, *, tokens: int | None = None
) -> Spec:
    return code(
        ordinal,
        cls,
        "class",
        start,
        end,
        body,
        parent_key=class_key(ADAPTERS, cls),
        structure=Structure("class", cls, MODULE, True, decl=decl),
        tokens=tokens,
    )


def adapters_specs(*, head_tokens: int = 60) -> list[Spec]:
    """A small ``adapters.py``: module head, BaseAdapter, HTTPAdapter with four methods."""
    return [
        code(
            0,
            None,
            "module",
            1,
            20,
            MODULE_TEXT.partition("\n")[2],
            parent_key=MODULE,
            structure=MODULE_STRUCTURE,
            tokens=90,
        ),
        class_head(1, "BaseAdapter", "class BaseAdapter", 22, 30, "class BaseAdapter:\n    pass"),
        class_head(
            2,
            "HTTPAdapter",
            "class HTTPAdapter(BaseAdapter)",
            32,
            60,
            'class HTTPAdapter(BaseAdapter):\n    """The built-in HTTP Adapter for urllib3."""\n'
            '    __attrs__ = ["max_retries", "config"]',
            tokens=head_tokens,
        ),
        method(
            3,
            "HTTPAdapter",
            "__init__",
            62,
            90,
            "    def __init__(self, pool_connections=DEFAULT_POOLSIZE,\n"
            "                 max_retries=DEFAULT_RETRIES):\n"
            "        self.max_retries = Retry(0, read=False)",
            tokens=80,
        ),
        method(
            4,
            "HTTPAdapter",
            "init_poolmanager",
            92,
            120,
            "    def init_poolmanager(self, connections, maxsize, block=DEFAULT_POOLBLOCK):\n"
            "        self._pool_connections = connections",
            tokens=70,
        ),
        method(
            5,
            "HTTPAdapter",
            "send",
            122,
            200,
            "    def send(self, request, stream=False, timeout=None):\n"
            "        conn = self.get_connection(request.url)\n"
            "        url = urlparse(request.url)",
            tokens=150,
        ),
        method(
            6,
            "HTTPAdapter",
            "close",
            202,
            210,
            "    def close(self):\n        self.poolmanager.clear()",
            tokens=30,
        ),
    ]


def doc(
    ordinal: int,
    start: int,
    end: int,
    body: str,
    *,
    section: int | None,
    head: bool,
    title: str = "Release History › 2.32.0 (2024-05-20)",
    file: str = HISTORY,
    tokens: int | None = None,
) -> Spec:
    key = f"{file}#L{section}" if section is not None else None
    structure = Structure("section", title, None, head) if section is not None else None
    locator = {"file": file, "start_line": start, "end_line": end, "heading": title}
    return Spec(ordinal, body, locator, key, structure, tokens)


def with_parent_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make retrieval read the parent columns for its candidates.

    Until slice A's migration 005 lands, ``load_chunks`` reads the ORM model, which has no parent
    fields; this reads them through ``chunks_at_ordinals`` (the raw query that does).
    """

    def load(engine: Engine, chunk_ids: Sequence[str], allowed: Any) -> dict[str, ChunkRow]:
        allowed_ids = list(allowed)
        rows = load_chunks(engine, chunk_ids, allowed_ids)
        out: dict[str, ChunkRow] = {}
        for chunk_id, row in rows.items():
            full = chunks_at_ordinals(engine, row.source_id, [row.ordinal], allowed_ids)
            out[chunk_id] = full.get(row.ordinal, row)
        return out

    monkeypatch.setattr(service, "load_chunks", load)


def outline_text_of(blocks: Sequence[Any]) -> str:
    return "\n".join(block.render() for block in blocks)


def dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class AllFiles(SnapshotFiles):
    """Every file these tests cite is in each source's indexed snapshot (layer 5's locator check
    passes), so a sentence fails only on what its cited text holds."""

    FILES = (ADAPTERS, HISTORY, "src/requests/compat.py", "src/requests/auth.py")

    def __init__(self) -> None:
        super().__init__(None)

    def files(self, source_id: str) -> dict[str, list[LineRange | None]]:
        return {name: [(1, 100_000)] for name in self.FILES}


# --- transcripts (D-091) ----------------------------------------------------------------------

MEETING_TEXTS = (
    ("Anna", "Welcome everyone to the planning call."),
    ("Bo", "The budget for the bridge project is forty thousand euros."),
    ("Anna", "We agreed to ship the release on Friday."),
    ("Bo", "Carla will write the release notes."),
    ("Anna", "The venue for the launch party is the harbour hall."),
    ("Bo", "Testing starts on Monday morning."),
    ("Anna", "Thanks, see you next week."),
)


def turn(
    ordinal: int,
    text: str,
    *,
    speaker: str | None = None,
    seconds: int = 60,
    tokens: int | None = None,
) -> Spec:
    """A transcript chunk ``seconds`` long at ``ordinal`` (start = ordinal × seconds)."""
    start = ordinal * seconds
    locator: dict[str, Any] = {"start_s": float(start), "end_s": float(start + seconds)}
    locator["speaker"] = speaker
    return Spec(ordinal, text, locator, None, None, tokens)


def meeting_specs(*, tokens: int | None = None) -> list[Spec]:
    """Seven one-minute turns of a planning call (ordinals 0-6), speakers set by hand: today's
    readers set none, so only fixture locators carry them."""
    return [
        turn(n, text, speaker=speaker, tokens=tokens)
        for n, (speaker, text) in enumerate(MEETING_TEXTS)
    ]


# --- a status_codes.py-like file: a split data table and the function reading it (D-119) ---------

STATUS = "src/requests/status_codes.py"
STATUS_MODULE = module_key(STATUS)

DOCSTRING = (
    '"""\nThe ``codes`` object defines a mapping from common names for HTTP statuses\n'
    "to their numerical codes, accessible either as attributes or as dictionary\nitems.\n\n"
    ">>> requests.codes['temporary_redirect']\n307\n>>> requests.codes.teapot\n418\n"
    '"""\n\nfrom .structures import LookupDict'
)
TABLE_HEAD = (
    '_codes = {\n    # Informational.\n    100: ("continue",),\n    101: ("switching_protocols",),'
)
TABLE_429 = (
    '    428: ("precondition_required", "precondition"),\n'
    '    429: ("too_many_requests", "too_many"),\n'
    '    431: ("header_fields_too_large", "fields_too_large"),'
)
CODES = 'codes = LookupDict(name="status_codes")'
INIT = (
    "def _init():\n    for code, titles in _codes.items():\n        for title in titles:\n"
    '            setattr(codes, title, code)\n            if not title.startswith(("\\\\", "/")):\n'
    "                setattr(codes, title.upper(), code)"
)
DOC = 'def doc(code):\n    found = ", ".join(f"``{n}``" for n in _codes[code])\n    return found'


def status_structure(head: bool = False, defines: tuple[str, ...] = ()) -> Structure:
    return Structure("module", STATUS, None, head, defines=defines)


def table_part(ordinal: int, part: int, start: int, end: int, body: str, tokens: int) -> Spec:
    """A part of the split ``_codes`` literal: header ``_codes (part N)``, locator kind data."""
    text = f"File: {STATUS} · _codes (part {part}) (lines {start}-{end})\n{body}"
    locator = {"file": STATUS, "start_line": start, "end_line": end, "kind": "data"}
    locator["symbol"] = "_codes"
    defines = ("_codes",) if part == 1 else ()
    return Spec(ordinal, text, locator, STATUS_MODULE, status_structure(defines=defines), tokens)


def status_specs(*, init_tokens: int = 120, doc_tokens: int = 60) -> list[Spec]:
    return [
        code(
            0,
            None,
            "module",
            1,
            21,
            DOCSTRING,
            parent_key=STATUS_MODULE,
            structure=status_structure(head=True),
            file=STATUS,
            tokens=150,
        ),
        table_part(1, 1, 23, 40, TABLE_HEAD, 140),
        table_part(2, 6, 77, 85, TABLE_429, 130),
        code(
            3,
            None,
            "module",
            104,
            106,
            CODES,
            parent_key=STATUS_MODULE,
            structure=status_structure(defines=("codes",)),
            file=STATUS,
            tokens=20,
        ),
        code(
            4,
            "_init",
            "function",
            109,
            125,
            INIT,
            parent_key=STATUS_MODULE,
            structure=status_structure(defines=("_init",)),
            file=STATUS,
            tokens=init_tokens,
        ),
        code(
            5,
            "doc",
            "function",
            128,
            131,
            DOC,
            parent_key=STATUS_MODULE,
            structure=status_structure(defines=("doc",)),
            file=STATUS,
            tokens=doc_tokens,
        ),
    ]
