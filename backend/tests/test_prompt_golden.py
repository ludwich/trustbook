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
# SHA-256 of the JSON messages. Taken at commit eae63e0 (before D-091); retaken for D-120, whose
# only changes are the two-chunk citation rule in ANSWER_SYSTEM, the last sentence of FEEDBACK_TASK
# and the "so cite … as well" wording of the feedback (checked by reverting those strings).
FIRST = "a899c461e7e1968effd856f35a44fe210a070012e88c0026ea0ba818f9a52dd3"
WITH_OUTLINE = "da66d47001b8de95c825d509e8a9448dfe0e4fc9fe8b1118c1f83f5c79aa0a52"
REGENERATION = "1f64dc5f4e0fef8b43b9c32ead384eec67b8a03e662735c742004f52b6104b22"


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
