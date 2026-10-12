"""``trustbook``: command line entry point (argument parsing, errors and exit codes, D-087)."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import TextIO

import httpx

from trustbook_cli import __version__, commands
from trustbook_cli.client import DEFAULT_BASE_URL, ApiError, TrustbookClient, TrustbookError
from trustbook_cli.output import EXIT_ERROR, EXIT_INTERRUPTED, EXIT_OK, Output
from trustbook_cli.redact import Redactor, saved_secrets

SEARCH_LIMIT = 10

Handler = Callable[[commands.Context, argparse.Namespace], int]


class UsageError(Exception):
    """Bad command line; exit code 1 (argparse's own 2 means "refused" here)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(f"{self.prog}: {message}")


def _common() -> _Parser:
    """Options that may also follow the command (``trustbook ask "…" --json``)."""
    common = _Parser(add_help=False, allow_abbrev=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--base-url", default=argparse.SUPPRESS)
    return common


def _space_opt(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--space", help="space id, slug or name (default: TRUSTBOOK_SPACE)", default=None
    )


def _wait_opts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait", action="store_true", help="wait until ready or failed (progress on stderr)"
    )
    parser.add_argument(
        "--timeout", type=float, default=None, help="with --wait: give up after this many seconds"
    )


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = _Parser(
        prog="trustbook",
        description="Trustbook from the command line: spaces, sources and grounded answers.",
        epilog="Exit codes: 0 ok, 1 error, 2 refused (not in the sources), 3 a source failed, "
        "4 eval: a target was missed.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"trustbook-cli {__version__}")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Trustbook address (default: TRUSTBOOK_URL or {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--json", action="store_true", help="one JSON document on stdout")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(
        group: argparse._SubParsersAction[_Parser],
        name: str,
        handler: Handler,
        help_: str,
        *,
        add_help: bool = True,
    ) -> _Parser:
        p = group.add_parser(
            name, help=help_, parents=[common], allow_abbrev=False, add_help=add_help
        )
        p.set_defaults(handler=handler)
        return p

    spaces = sub.add_parser("spaces", help="list, create or delete spaces").add_subparsers(
        dest="action", required=True, metavar="ACTION"
    )
    add(spaces, "list", commands.spaces_list, "list spaces")
    p = add(spaces, "create", commands.spaces_create, "create a space")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p = add(spaces, "delete", commands.spaces_delete, "delete a space and everything in it")
    p.add_argument("space_name", metavar="SPACE")
    p.add_argument("--yes", action="store_true", help="confirm")

    sources = sub.add_parser("sources", help="add, list, check, refresh or remove sources")
    src = sources.add_subparsers(dest="action", required=True, metavar="ACTION")
    adds = src.add_parser("add", help="add a source").add_subparsers(
        dest="kind", required=True, metavar="KIND"
    )
    p = add(adds, "folder", commands.add_folder, "link a folder (code or documents)")
    p.add_argument("path", help="relative to the browse root, or absolute with --browse-root")
    p.add_argument(
        "--browse-root",
        default=None,
        help="the folder Trustbook may read, as this computer sees it (default: "
        "TRUSTBOOK_BROWSE_ROOT); maps an absolute path",
    )
    p = add(adds, "url", commands.add_url, "add web pages or YouTube videos")
    p.add_argument("urls", nargs="+", metavar="URL")
    p = add(adds, "file", commands.add_file, "upload files (PDF, DOCX, audio, …)")
    p.add_argument("files", nargs="+", metavar="FILE")
    p = add(adds, "paste", commands.add_paste, "add text (from stdin with - or no TEXT)")
    p.add_argument("text", nargs="?", default=None)
    p.add_argument("--title", default=None)
    p = add(adds, "search", commands.add_search, "web search; add results with --pick")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=SEARCH_LIMIT)
    p.add_argument("--pick", action="append", default=[], help="result numbers or URLs: 1,3")
    for p in (adds.choices[k] for k in ("folder", "url", "file", "paste", "search")):
        _space_opt(p)
        _wait_opts(p)

    p = add(src, "list", commands.sources_list, "list the space's sources")
    _space_opt(p)
    for name, handler, help_ in (
        ("status", commands.sources_status, "one source's status"),
        ("refresh", commands.sources_refresh, "re-read a source"),
    ):
        p = add(src, name, handler, help_)
        p.add_argument("source", help="source id, title or origin")
        _space_opt(p)
        _wait_opts(p)
    p = add(src, "remove", commands.sources_remove, "remove a source")
    p.add_argument("source", help="source id, title or origin")
    p.add_argument("--yes", action="store_true", help="confirm")
    _space_opt(p)

    p = add(sub, "ask", commands.ask_cmd, "ask a question (grounded in the space's sources)")
    p.add_argument("question", help='the question, or "-" to read it from stdin')
    _space_opt(p)
    add(sub, "queue", commands.queue, "the processing queue")
    p = add(sub, "gate-log", commands.gate_log, "the gate's recent decisions in a space")
    _space_opt(p)
    p.add_argument("--limit", type=int, default=50)
    add(
        sub,
        "eval",
        commands.eval_run,
        "run eval/runner.py (all further arguments go to it)",
        add_help=False,
    )
    return parser


def _parse(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    args, extra = parser.parse_known_args(argv)
    if getattr(args, "command", None) == "eval":
        args.runner_args = extra
    elif extra:
        raise UsageError(f"trustbook: unrecognized arguments: {' '.join(extra)}")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stdin: TextIO | None = None,
) -> int:
    """Run one command and return its exit code (the console script exits with it).

    ``client``: an ``httpx.Client`` with the API's base URL (tests pass FastAPI's TestClient).
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    out_stream, err_stream = stdout or sys.stdout, stderr or sys.stderr
    out = Output(
        out_stream, err_stream, json_mode="--json" in argv, redact=Redactor(saved_secrets())
    )
    parser = build_parser()
    try:
        with contextlib.redirect_stdout(out_stream), contextlib.redirect_stderr(err_stream):
            args = _parse(parser, argv)
    except UsageError as exc:
        out.error(f"{exc} (see trustbook --help)")
        return EXIT_ERROR
    except SystemExit as exc:  # --help, --version
        return exc.code if isinstance(exc.code, int) else EXIT_OK
    out.json_mode = bool(args.json)
    base_url = args.base_url or os.environ.get("TRUSTBOOK_URL") or DEFAULT_BASE_URL
    api = TrustbookClient(base_url, client=client)
    ctx = commands.Context(
        client=api, out=out, env=os.environ, stdin=stdin or sys.stdin, sleep=sleep,
        base_url=base_url,
    )  # fmt: skip
    try:
        code: int = args.handler(ctx, args)
        return code
    except ApiError as exc:
        out.error(str(exc), exc.http_status)
    except TrustbookError as exc:
        out.error(str(exc))
    except KeyboardInterrupt:
        out.error("interrupted")
        return EXIT_INTERRUPTED
    finally:
        api.close()
    return EXIT_ERROR
