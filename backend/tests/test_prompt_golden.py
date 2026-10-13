"""The answer prompt without transcript windows is byte-identical to the pre-D-091 one.

The Smoke and eval corpora have no transcripts, so their prompts must not change when windows
are added (D-091). The hashes were taken from the prompt code before windows existed; any change
to the rendering of chunks, the outline or the regeneration section shows up here.
"""

from __future__ import annotations

import hashlib
import json

from app.capabilities.adapters.base import ChatMessage
from app.chat.prompt import FailedSentence, answer_messages
from app.retrieval.parents import OutlineBlock
from app.retrieval.service import RetrievedChunk

FILE = "src/requests/adapters.py"
CHUNKS = [
    RetrievedChunk(
        "k1",
        "s1",
        "requests",
        f"File: {FILE} · HTTPAdapter.send (lines 122-200)\n"
        "    def send(self, request, stream=False, timeout=None):\n"
        "        conn = self.get_connection(request.url)  # \\_raw",
        {"file": FILE, "start_line": 122, "end_line": 200, "symbol": "HTTPAdapter.send"},
        40,
        0.9,
    ),
    RetrievedChunk(
        "k2",
        "s2",
        "netrc  Authentication",
        "Requests reads \\~/.netrc, \\~/\\_netrc when no auth is given.",
        {"heading": "netrc Authentication"},
        20,
        0.7,
    ),
    RetrievedChunk(
        "k3",
        "s3",
        "Release notes",
        "File: HISTORY.md · 2.32.0 (lines 159-199)\n2.32.0 (2024-05-20)\n-------\n\\* Fixes",
        {"file": "HISTORY.md", "start_line": 159, "end_line": 199, "heading": "2.32.0"},
        30,
        0.6,
    ),
    RetrievedChunk(
        "k4",
        "s4",
        "Team call",
        "We agreed to ship on Friday. Anna will write the notes.",
        {"start_s": 130.4, "end_s": 3725.0, "speaker": "Anna"},
        15,
        0.5,
    ),
    RetrievedChunk("k5", "s5", "Scan", "Page text.", {"pages": [3, 4]}, 5, 0.4),
]
OUTLINE = (
    OutlineBlock(
        "s1",
        FILE,
        ("imports: from .compat import …, urlparse, …", "class HTTPAdapter(BaseAdapter): send"),
        30,
    ),
)
FAILED = [
    FailedSentence("It uses `DEFAULT_POOLSIZE`.", ("c1",), (("DEFAULT_POOLSIZE", ()),)),
    FailedSentence('Anna said "Friday".', ("c4",), (("Friday", ("c2", "c3")),)),
]
QUESTION = "  How does   HTTPAdapter send a request?\n"
# SHA-256 of the JSON messages, taken at commit eae63e0 (before D-091).
FIRST = "1a1e5d07e238ff7029110850d21a13a9317bc0d7eb0c0d88645322ad2ccd35ef"
WITH_OUTLINE = "a1d4a22a410a2a943ab5cb49bc3c4f42e5a770c77c671fd537892bb650e59395"
REGENERATION = "f5d93362afa55b1cd1ec2c69377b77cb566017687eb114c9cf1af2c4d41be868"


def digest(messages: list[ChatMessage]) -> str:
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()


def test_the_first_attempt_prompt_is_unchanged() -> None:
    assert digest(answer_messages(QUESTION, CHUNKS)) == FIRST


def test_the_prompt_with_an_outline_is_unchanged() -> None:
    assert digest(answer_messages(QUESTION, CHUNKS, outline=OUTLINE)) == WITH_OUTLINE


def test_the_regeneration_prompt_is_unchanged() -> None:
    messages = answer_messages(QUESTION, CHUNKS, outline=OUTLINE, regenerate=True, failed=FAILED)
    assert digest(messages) == REGENERATION


def test_no_windows_is_the_default() -> None:
    plain = answer_messages(QUESTION, CHUNKS, outline=OUTLINE, regenerate=True, failed=FAILED)
    assert plain == answer_messages(
        QUESTION, CHUNKS, outline=OUTLINE, windows=(), regenerate=True, failed=FAILED
    )
