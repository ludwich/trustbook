"""Command handlers: each turns parsed arguments into API calls, one result and an exit code."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from trustbook_cli import eval_cmd
from trustbook_cli.ask import ask, render_text
from trustbook_cli.client import TrustbookClient, TrustbookError, norm_text
from trustbook_cli.output import (
    EXIT_FAILED_SOURCE,
    EXIT_OK,
    SCHEMA_DELETED,
    SCHEMA_GATE_LOG,
    SCHEMA_JOBS,
    SCHEMA_SEARCH,
    SCHEMA_SOURCE,
    SCHEMA_SOURCES,
    SCHEMA_SPACE,
    SCHEMA_SPACES,
    Output,
)
from trustbook_cli.paths import folder_api_path
from trustbook_cli.wait import stage_text, status_text, wait_for_sources

SPACE_FIELDS = ("id", "slug", "name", "description", "source_count", "created_at")
SOURCE_FIELDS = (
    "id", "type", "title", "origin", "group", "status", "progress", "failure_reason",
    "failure_next_step", "quality_warning", "enabled", "snapshot_at", "commit_hash", "stats",
    "created_at", "job_id", "ahead", "stage",
)  # fmt: skip
JOB_FIELDS = (
    "id", "kind", "title", "space_id", "space_name", "status", "progress", "position", "error",
    "created_at", "started_at", "finished_at", "eta_seconds", "stage",
)  # fmt: skip
GATE_FIELDS = ("id", "created", "input", "decision", "confidence", "user_correction")


@dataclass
class Context:
    """What every handler gets: the API, the output, and the process's surroundings."""

    client: TrustbookClient
    out: Output
    env: Mapping[str, str]
    stdin: TextIO
    sleep: Callable[[float], None]
    base_url: str


def _pick(raw: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: raw.get(name) for name in fields}


def space_out(raw: dict[str, Any]) -> dict[str, Any]:
    return _pick(raw, SPACE_FIELDS)


def source_out(raw: dict[str, Any]) -> dict[str, Any]:
    return _pick(raw, SOURCE_FIELDS)


def _space_ref(space: dict[str, Any]) -> dict[str, Any]:
    return {"id": space.get("id"), "name": space.get("name")}


def _table(rows: list[list[str]], header: list[str]) -> str:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip()
             for row in [header, *rows]]  # fmt: skip
    return "\n".join(lines)


def _space(ctx: Context, args: argparse.Namespace) -> dict[str, Any]:
    key = args.space or ctx.env.get("TRUSTBOOK_SPACE")
    if not key:
        raise TrustbookError("Give the space with --space (or set TRUSTBOOK_SPACE).")
    return ctx.client.space(key)


def _read_text(ctx: Context, value: str | None, what: str) -> str:
    text = ctx.stdin.read() if value in (None, "-") else value
    assert text is not None
    if not text.strip():
        raise TrustbookError(f"The {what} is empty.")
    return text


# --- spaces ----------------------------------------------------------------------------------


def spaces_list(ctx: Context, _args: argparse.Namespace) -> int:
    spaces = [space_out(s) for s in ctx.client.spaces()]
    rows = [[str(s["name"]), str(s["source_count"]), str(s["id"])] for s in spaces]
    text = _table(rows, ["NAME", "SOURCES", "ID"]) if spaces else "No spaces yet."
    ctx.out.result({"schema": SCHEMA_SPACES, "spaces": spaces}, text)
    return EXIT_OK


def spaces_create(ctx: Context, args: argparse.Namespace) -> int:
    space = space_out(ctx.client.create_space(args.name, args.description or ""))
    ctx.out.result(
        {"schema": SCHEMA_SPACE, "space": space}, f"Created space {space['name']} ({space['id']})"
    )
    return EXIT_OK


def spaces_delete(ctx: Context, args: argparse.Namespace) -> int:
    space = ctx.client.space(args.space_name)
    if not args.yes:
        raise TrustbookError(
            f"This deletes the space {space['name']} with all its sources and conversations. "
            "Add --yes to confirm."
        )
    ctx.client.delete_space(str(space["id"]))
    doc = {"schema": SCHEMA_DELETED, "kind": "space", "id": space["id"], "name": space["name"]}
    ctx.out.result(doc, f"Deleted space {space['name']}")
    return EXIT_OK


# --- sources ---------------------------------------------------------------------------------


def find_source(sources: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """A source by id, then title, then origin (path or URL); one match or an error."""
    by_id = [s for s in sources if str(s.get("id")) == key]
    if by_id:
        return by_id[0]
    for field in ("title", "origin"):
        found = [s for s in sources if norm_text(str(s.get(field) or "")) == norm_text(key)]
        if len(found) > 1:
            ids = ", ".join(str(s.get("id")) for s in found)
            raise TrustbookError(f"Several sources match {key!r} ({ids}); give the id.")
        if found:
            return found[0]
    raise TrustbookError(f"No source {key!r} in this space. See `trustbook sources list`.")


def _sources_text(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "No sources."
    rows = []
    for s in sources:
        rows.append([status_text(s), str(s.get("type")), str(s.get("title")), str(s.get("id"))])
    text = _table(rows, ["STATUS", "TYPE", "TITLE", "ID"])
    for s in sources:
        for key in ("failure_reason", "failure_next_step", "quality_warning"):
            if s.get(key):
                text += f"\n{s.get('title')}: {s[key]}"
    return text


def _finish(
    ctx: Context, args: argparse.Namespace, space: dict[str, Any], created: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if getattr(args, "wait", False) and created:
        return wait_for_sources(
            ctx.client,
            str(space["id"]),
            created,
            note=ctx.out.note,
            sleep=ctx.sleep,
            timeout_s=args.timeout,
        )
    return created


def _exit_for(sources: list[dict[str, Any]]) -> int:
    return EXIT_FAILED_SOURCE if any(s.get("status") == "failed" for s in sources) else EXIT_OK


def _report_sources(ctx: Context, space: dict[str, Any], sources: list[dict[str, Any]]) -> int:
    shown = [source_out(s) for s in sources]
    doc = {"schema": SCHEMA_SOURCES, "space": _space_ref(space), "sources": shown}
    ctx.out.result(doc, _sources_text(shown))
    return _exit_for(shown)


def sources_list(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    return _report_sources(ctx, space, ctx.client.sources(str(space["id"])))


def _one_source(ctx: Context, space: dict[str, Any], source: dict[str, Any]) -> int:
    shown = source_out(source)
    doc = {"schema": SCHEMA_SOURCE, "space": _space_ref(space), "source": shown}
    ctx.out.result(doc, _sources_text([shown]))
    return _exit_for([shown])


def sources_status(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    source = find_source(ctx.client.sources(str(space["id"])), args.source)
    return _one_source(ctx, space, _finish(ctx, args, space, [source])[0])


def sources_refresh(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    source = find_source(ctx.client.sources(str(space["id"])), args.source)
    refreshed = ctx.client.refresh_source(str(space["id"]), str(source["id"]))
    return _one_source(ctx, space, _finish(ctx, args, space, [refreshed])[0])


def sources_remove(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    source = find_source(ctx.client.sources(str(space["id"])), args.source)
    if not args.yes:
        raise TrustbookError(
            f"This removes {source.get('title')} and its index from {space['name']}. "
            "Add --yes to confirm."
        )
    ctx.client.delete_source(str(space["id"]), str(source["id"]))
    doc = {"schema": SCHEMA_DELETED, "kind": "source", "id": source["id"], "name": source["title"]}
    ctx.out.result(doc, f"Removed {source.get('title')}")
    return EXIT_OK


def add_folder(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    root = args.browse_root or ctx.env.get("TRUSTBOOK_BROWSE_ROOT")
    path = folder_api_path(args.path, root)
    created = [ctx.client.add_folder(str(space["id"]), path)]
    return _report_sources(ctx, space, _finish(ctx, args, space, created))


def add_url(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    created: list[dict[str, Any]] = []
    try:
        for url in args.urls:  # one request per URL: a later one can fail after others were added
            created.append(ctx.client.add_url(str(space["id"]), url))
    except TrustbookError as exc:
        if created:
            exc.created = [source_out(s) for s in created]
        raise
    return _report_sources(ctx, space, _finish(ctx, args, space, created))


def add_file(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    files: list[tuple[str, bytes]] = []
    for name in args.files:
        path = Path(name)
        try:
            files.append((path.name, path.read_bytes()))
        except OSError as exc:
            raise TrustbookError(f"Cannot read {name}: {exc.strerror or exc}") from exc
    created = ctx.client.upload(str(space["id"]), files)
    return _report_sources(ctx, space, _finish(ctx, args, space, created))


def add_paste(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    text = _read_text(ctx, args.text, "text")
    created = [ctx.client.add_paste(str(space["id"]), text, args.title)]
    return _report_sources(ctx, space, _finish(ctx, args, space, created))


def _picked(results: list[dict[str, Any]], picks: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for pick in (p.strip() for raw in picks for p in raw.split(",")):
        if not pick:
            continue
        if pick.isdigit():
            n = int(pick)
            if not 1 <= n <= len(results):
                raise TrustbookError(f"--pick {n}: the search gave {len(results)} results.")
            hit = results[n - 1]
        else:
            same = [r for r in results if str(r.get("url")) == pick]
            hit = same[0] if same else {"url": pick, "title": ""}
        item = {"url": str(hit.get("url", "")), "title": str(hit.get("title") or "")[:200]}
        if item not in items:
            items.append(item)
    if not items:
        raise TrustbookError("--pick names no results.")
    return items


def add_search(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    space_id = str(space["id"])
    results = ctx.client.web_search(space_id, args.query, args.limit)
    if not args.pick:
        shown = [
            {"n": i, "title": r.get("title"), "url": r.get("url"), "snippet": r.get("snippet")}
            for i, r in enumerate(results, start=1)
        ]
        lines = [f"[{r['n']}] {r['title']}\n    {r['url']}" for r in shown] or ["No results."]
        lines.append("Add results with --pick 1,3 (numbers or URLs).")
        doc = {"schema": SCHEMA_SEARCH, "space": _space_ref(space), "query": args.query}
        ctx.out.result({**doc, "results": shown}, "\n".join(lines))
        return EXIT_OK
    created = ctx.client.add_from_search(space_id, _picked(results, args.pick))
    return _report_sources(ctx, space, _finish(ctx, args, space, created))


# --- ask, queue, gate log, eval --------------------------------------------------------------


def ask_cmd(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    question = _read_text(ctx, args.question, "question").strip()
    result = ask(ctx.client, space, question, clock=time.perf_counter)
    ctx.out.result(result.doc, render_text(result.doc))
    return result.exit_code


def queue(ctx: Context, _args: argparse.Namespace) -> int:
    data = ctx.client.jobs()
    raw = data.get("jobs")
    jobs = (
        [_pick(j, JOB_FIELDS) for j in raw if isinstance(j, dict)] if isinstance(raw, list) else []
    )
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    rows = []
    for j in jobs:
        progress = j["progress"]
        cell = f"{round(progress * 100)}%" if isinstance(progress, int | float) else ""
        stage = stage_text(j) if j["status"] == "running" else None
        cell = f"{cell} {stage}".strip() if stage else cell
        rows.append([str(j["status"]), cell, str(j["space_name"] or ""), str(j["title"])])
    text = _table(rows, ["STATUS", "PROGRESS", "SPACE", "TITLE"]) if jobs else "No jobs."
    ctx.out.result({"schema": SCHEMA_JOBS, "jobs": jobs, "summary": summary}, text)
    return EXIT_OK


def gate_log(ctx: Context, args: argparse.Namespace) -> int:
    space = _space(ctx, args)
    entries = [_pick(e, GATE_FIELDS) for e in ctx.client.gate_log(str(space["id"]), args.limit)]
    rows = [
        [
            str(e["created"]),
            str(e["decision"]) + (f" -> {e['user_correction']}" if e["user_correction"] else ""),
            "" if e["confidence"] is None else f"{e['confidence']:.2f}",
            " ".join(str(e["input"]).split())[:80],
        ]
        for e in entries
    ]
    text = (
        _table(rows, ["CREATED", "DECISION", "CONFIDENCE", "INPUT"]) if entries else "No entries."
    )
    doc = {"schema": SCHEMA_GATE_LOG, "space": _space_ref(space), "entries": entries}
    ctx.out.result(doc, text)
    return EXIT_OK


def eval_run(ctx: Context, args: argparse.Namespace) -> int:
    eval_cmd.check_eval_dependencies()
    runner = eval_cmd.find_runner(ctx.env)
    return eval_cmd.run_eval(
        runner,
        args.runner_args,
        ctx.base_url,
        out=ctx.out.passthrough,
        err=lambda text: ctx.out.passthrough(text, err=True),
    )
