"""``trustbook ask``: one question in a fresh conversation, as the UI asks it (D-088).

The answer comes from the chat route's event stream, so it passed the same six grounding layers
and refusal as in the UI; the CLI only reshapes it. Each cited passage's text is read from the
source viewer's route (``/chunks/{id}/context``): the lines or text the citation points at.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from trustbook_cli.client import (
    DEFAULT_TIMEOUT,
    STREAM_READ_TIMEOUT_S,
    ApiError,
    ApiUnreachableError,
    TrustbookClient,
    error_detail,
    iter_sse,
    sse_lines,
)
from trustbook_cli.output import EXIT_OK, EXIT_REFUSED, SCHEMA_ASK

VERDICTS = ("verified", "partially_verified", "unverified", "refused")
TITLE_CHARS = 60
# The gate's fixed reply to greetings and small talk (no answer from the sources).
CHITCHAT = "chitchat"


@dataclass
class AskResult:
    doc: dict[str, Any]
    exit_code: int


def _title(question: str) -> str:
    line = " ".join(question.split())
    return "CLI: " + (line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1] + "…")


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _lines(loc: dict[str, Any]) -> list[int] | None:
    start = loc.get("start_line")
    if not isinstance(start, int) or isinstance(start, bool):
        return None
    end = loc.get("end_line")
    return [start, end if isinstance(end, int) and not isinstance(end, bool) else start]


def _pages(loc: dict[str, Any]) -> list[int] | None:
    raw = loc.get("pages", loc.get("page"))
    values = raw if isinstance(raw, list) else [raw]
    pages = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return pages or None


def passage_text(context: dict[str, Any]) -> str | None:
    """The cited lines (code view) or the highlighted passage (text view)."""
    code = context.get("code")
    if context.get("view") == "code" and isinstance(code, dict):
        lines, first, span = code.get("lines"), code.get("first_line"), code.get("highlight")
        if isinstance(lines, list) and isinstance(first, int) and isinstance(span, list | tuple):
            start, end = span[0] - first, span[1] - first + 1
            return "\n".join(str(line) for line in lines[max(start, 0) : max(end, 0)])
    text = context.get("text")
    if isinstance(text, dict) and isinstance(text.get("highlight"), str):
        return str(text["highlight"])
    chunk = context.get("chunk")
    if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
        return str(chunk["text"])
    return None


def citation_out(
    raw: dict[str, Any], origins: dict[str, str | None], context: dict[str, Any] | None
) -> dict[str, Any]:
    """One citation of the ``trustbook.ask/1`` document."""
    loc = raw.get("locator") if isinstance(raw.get("locator"), dict) else {}
    assert isinstance(loc, dict)
    source_id = str(raw.get("source_id", ""))
    return {
        "n": raw.get("n") if isinstance(raw.get("n"), int) else 0,
        "source": str(raw.get("source_title", "")),
        "source_id": source_id,
        "origin": origins.get(source_id),
        "chunk_id": str(raw.get("chunk_id", "")),
        "file": _str(loc.get("file")),
        "lines": _lines(loc),
        "pages": _pages(loc),
        "start_s": _num(loc.get("start_s")),
        "end_s": _num(loc.get("end_s")),
        "speaker": _str(loc.get("speaker")),
        "heading": _str(loc.get("heading")),
        "symbol": _str(loc.get("symbol")),
        "text": passage_text(context) if context is not None else None,
    }


def answer_text(sentences: list[dict[str, Any]]) -> str:
    """The sentences joined, each followed by its citation numbers: ``Text. [1][2]``."""
    parts = []
    for s in sentences:
        marks = "".join(f"[{n}]" for n in s["citations"])
        parts.append(f"{s['text']} {marks}" if marks else s["text"])
    return " ".join(parts)


def _removed(message: dict[str, Any]) -> list[dict[str, str]]:
    """The answer's removed statements (the UI's "Unverified, not from your sources")."""
    diagnostics = message.get("diagnostics")
    failed = diagnostics.get("failed_sentences") if isinstance(diagnostics, dict) else None
    reasons: dict[str, str] = {}
    for item in failed if isinstance(failed, list) else []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            reasons.setdefault(item["text"], str(item.get("reason") or ""))
    raw = message.get("removed_sentences")
    removed = [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []
    return [{"text": t, "reason": reasons.get(t, reasons.get(t[:300], ""))} for t in removed]


@dataclass
class _Stream:
    sentences: list[dict[str, Any]]
    refusal: dict[str, Any] | None
    message: dict[str, Any] | None
    latency_s: float | None


def _consume(
    client: TrustbookClient, url: str, question: str, clock: Callable[[], float]
) -> _Stream:
    streamed: list[dict[str, Any]] = []
    refusal: dict[str, Any] | None = None
    start = clock()
    timeout = httpx.Timeout(DEFAULT_TIMEOUT.connect, read=STREAM_READ_TIMEOUT_S)
    try:
        with client.http.stream(
            "POST",
            url,
            json={"content": question},
            headers={"Accept": "text/event-stream"},
            timeout=timeout,
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise ApiError(
                    f"ask failed: HTTP {resp.status_code}: {error_detail(resp)}", resp.status_code
                )
            # Every error below came with a response: report its status (trustbook.error/1).
            status = resp.status_code
            for ev in iter_sse(sse_lines(resp.iter_bytes())):
                if ev.event in ("status", "message"):
                    continue
                try:
                    data = json.loads(ev.data) if ev.data.strip() else {}
                except ValueError:
                    data = None
                if not isinstance(data, dict):
                    raise ApiError(f"ask: bad {ev.event} event from the API", status)
                if ev.event == "sentence":
                    streamed.append(data)
                elif ev.event == "refused":
                    refusal = data
                elif ev.event == "error":
                    # The model endpoint's status when it answered (401: the key was rejected).
                    endpoint = data.get("http_status")
                    raise ApiError(
                        f"ask failed: {data.get('message') or 'error event'}",
                        endpoint if isinstance(endpoint, int) else status,
                    )
                elif ev.event == "done":
                    message = data.get("message")
                    if not isinstance(message, dict):
                        raise ApiError("ask: the answer's message is missing", status)
                    return _Stream(streamed, refusal, message, round(clock() - start, 2))
            raise ApiError("ask: the answer stream ended without a result", status)
    except httpx.TransportError as exc:
        raise ApiUnreachableError(f"ask: {type(exc).__name__}: {exc}") from exc


def _sentences(message: dict[str, Any], streamed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = message.get("content")
    final = content.get("sentences") if isinstance(content, dict) else None
    raw = final if isinstance(final, list) else streamed
    return [s for s in raw if isinstance(s, dict)]


def ask(
    client: TrustbookClient,
    space: dict[str, Any],
    question: str,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> AskResult:
    """Ask ``question`` in ``space`` and build the ``trustbook.ask/1`` document."""
    space_id = str(space["id"])
    cid = client.create_conversation(space_id, _title(question))
    url = f"/api/spaces/{space_id}/conversations/{cid}/messages"
    stream = _consume(client, url, question, clock)
    message = stream.message or {}
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    assert isinstance(content, dict)
    badge = message.get("badge")
    raw_sentences = _sentences(message, stream.sentences)

    refusal: dict[str, Any] | None = None
    if stream.refusal is not None or badge == "refused":
        r = stream.refusal or {}
        refusal = {
            "reason": str(r.get("reason") or "not_in_sources"),
            "message": str(r.get("message") or content.get("text") or ""),
            "suggestion": _str(r.get("suggestion")) or _str(content.get("suggestion")),
        }
    elif not raw_sentences:
        # The gate's fixed reply to small talk: nothing was answered from the sources.
        refusal = {
            "reason": CHITCHAT,
            "message": str(content.get("text") or ""),
            "suggestion": None,
        }

    sentences: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    if refusal is None:
        unique: dict[str, dict[str, Any]] = {}
        for s in raw_sentences:
            cits = [c for c in s.get("citations") or [] if isinstance(c, dict)]
            sentences.append({"text": str(s.get("text", "")), "citations": []})
            for c in cits:
                key = str(c.get("chunk_id") or f"n{c.get('n')}")
                unique.setdefault(key, c)
                n = unique[key].get("n")
                if isinstance(n, int) and n not in sentences[-1]["citations"]:
                    sentences[-1]["citations"].append(n)
        if unique:
            origins = {
                str(src.get("id")): _str(src.get("origin")) for src in client.sources(space_id)
            }
            for raw in unique.values():
                chunk_id = str(raw.get("chunk_id", ""))
                context = client.chunk_context(space_id, chunk_id) if chunk_id else None
                citations.append(citation_out(raw, origins, context))
        citations.sort(key=lambda c: int(c["n"]))

    verdict = "refused" if refusal is not None else badge if badge in VERDICTS else "unverified"
    doc = {
        "schema": SCHEMA_ASK,
        "space": {"id": space_id, "name": str(space.get("name", ""))},
        "question": question,
        "conversation_id": cid,
        "message_id": _str(message.get("id")),
        "verdict": verdict,
        "answer": answer_text(sentences) if refusal is None else "",
        "sentences": sentences,
        "citations": citations,
        "removed_statements": _removed(message) if refusal is None else [],
        "refusal": refusal,
        "latency_s": stream.latency_s,
    }
    return AskResult(doc, EXIT_REFUSED if refusal is not None else EXIT_OK)


VERDICT_LABELS = {
    "verified": "Verified",
    "partially_verified": "Partially verified",
    "unverified": "Unverified",
}


def _locator_label(c: dict[str, Any]) -> str:
    parts = [str(c["source"])]
    if c["file"]:
        where = str(c["file"])
        if c["lines"]:
            where += f" lines {c['lines'][0]}-{c['lines'][1]}"
        if c["symbol"]:
            where += f" ({c['symbol']})"
        parts.append(where)
    elif c["pages"]:
        parts.append("page " + ", ".join(str(p) for p in c["pages"]))
    elif c["start_s"] is not None:
        end = c["end_s"] if c["end_s"] is not None else c["start_s"]
        span = f"{_clock(c['start_s'])}-{_clock(end)}"
        parts.append(span + (f" ({c['speaker']})" if c["speaker"] else ""))
    elif c["heading"]:
        parts.append(f'"{c["heading"]}"')
    return " - ".join(parts)


def _clock(seconds: float) -> str:
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_text(doc: dict[str, Any], *, excerpt_lines: int = 6) -> str:
    """The answer for people: verdict, cited sentences, sources with excerpts, removed ones."""
    refusal = doc["refusal"]
    if refusal is not None:
        lines = [f"Not answered ({refusal['reason']}): {refusal['message']}".rstrip()]
        if refusal["suggestion"]:
            lines.append(refusal["suggestion"])
        return "\n".join(lines) + "\n"
    out = [f"{VERDICT_LABELS.get(doc['verdict'], doc['verdict'])} answer:", "", doc["answer"], ""]
    if doc["citations"]:
        out.append("Sources:")
        for c in doc["citations"]:
            out.append(f"[{c['n']}] {_locator_label(c)}")
            text = (c["text"] or "").strip("\n").splitlines()
            out.extend(f"    | {line}" for line in text[:excerpt_lines])
            if len(text) > excerpt_lines:
                out.append(f"    | ... ({len(text) - excerpt_lines} more lines)")
    if doc["removed_statements"]:
        out += ["", "Removed (not verified, not from your sources):"]
        out.extend(f"- {r['text']}" for r in doc["removed_statements"])
    return "\n".join(out) + "\n"
