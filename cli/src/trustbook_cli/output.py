"""What the CLI writes: exit codes, versioned JSON documents and plain text (D-088).

With ``--json`` stdout holds exactly one JSON document (ASCII-only, so any console code page reads
it), errors included; progress and notes go to stderr. Everything passes the redactor.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TextIO

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2  # Trustbook refused to answer: not in the sources (or no sources)
EXIT_FAILED_SOURCE = 3  # a source failed to read or index
EXIT_TARGET_MISSED = 4  # `trustbook eval`: a --min-*/--max-* target was missed
EXIT_INTERRUPTED = 130

SCHEMA_ASK = "trustbook.ask/1"
SCHEMA_SPACES = "trustbook.spaces/1"
SCHEMA_SPACE = "trustbook.space/1"
SCHEMA_SOURCES = "trustbook.sources/1"
SCHEMA_SOURCE = "trustbook.source/1"
SCHEMA_SEARCH = "trustbook.search/1"
SCHEMA_JOBS = "trustbook.jobs/1"
SCHEMA_GATE_LOG = "trustbook.gate_log/1"
SCHEMA_DELETED = "trustbook.deleted/1"
SCHEMA_ERROR = "trustbook.error/1"


def json_text(doc: dict[str, Any]) -> str:
    """One document on one line, ASCII-only (``\\uXXXX`` escapes).

    One line: Windows PowerShell 5.1's ``ConvertFrom-Json`` and line-based tools read it as is.
    """
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":")) + "\n"


def ascii_safe(text: str, stream: TextIO) -> str:
    """Text the stream's encoding can write (a Windows console may be cp1252)."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    return text


class Output:
    """stdout/stderr for one command. ``json_mode``: :meth:`result` writes JSON, else text."""

    def __init__(
        self, stdout: TextIO, stderr: TextIO, *, json_mode: bool, redact: Callable[[str], str]
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.json_mode = json_mode
        self._redact = redact
        self._wrote_result = False

    def _write(self, stream: TextIO, text: str) -> None:
        stream.write(ascii_safe(self._redact(text), stream))
        stream.flush()

    def result(self, doc: dict[str, Any], text: str) -> None:
        """The command's result: ``doc`` (with ``schema``) as JSON, or ``text`` for people."""
        if self.json_mode:
            if self._wrote_result:  # one document per run, whatever happens
                return
            self._wrote_result = True
            self._write(self.stdout, json_text(doc))
        else:
            self._write(self.stdout, text if text.endswith("\n") else text + "\n")

    def passthrough(self, text: str, *, err: bool = False) -> None:
        """Another program's output, as it comes (``trustbook eval``)."""
        self._write(self.stderr if err else self.stdout, text)

    def note(self, text: str) -> None:
        """Progress and hints (stderr, both modes)."""
        self._write(self.stderr, text if text.endswith("\n") else text + "\n")

    def error(
        self,
        message: str,
        http_status: int | None = None,
        *,
        created: list[dict[str, Any]] | None = None,
    ) -> None:
        """An error: a ``trustbook.error/1`` document on stdout (JSON mode), always on stderr.

        ``created``: sources added before the error (``sources add`` with several items); the
        document gets a ``created`` list and stderr names them, so nobody adds them twice.
        """
        if self.json_mode:
            doc: dict[str, Any] = {
                "schema": SCHEMA_ERROR,
                "message": message,
                "http_status": http_status,
            }
            if created:
                doc["created"] = created
            self.result(doc, message)
        self.note(f"trustbook: error: {message}")
        if created:
            names = ", ".join(
                f"{s.get('title') or s.get('origin')} ({s.get('id')})" for s in created
            )
            self.note(f"trustbook: already added before the error: {names}")
