"""``--wait``: poll sources until they are ready or failed, with progress on stderr."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from trustbook_cli.client import TrustbookClient, TrustbookError

DONE = ("ready", "failed")
POLL_S = 2.0
# Progress is repeated at least this often while nothing changes (a long OCR or transcription).
QUIET_S = 30.0


def _progress(source: dict[str, Any]) -> str:
    status = str(source.get("status", "?"))
    progress = source.get("progress")
    if status not in DONE and isinstance(progress, int | float) and not isinstance(progress, bool):
        status += f" {round(progress * 100)}%"
    if status == "failed" and source.get("failure_reason"):
        status += f": {source['failure_reason']}"
    return f"{source.get('title') or source.get('id')}: {status}"


def wait_for_sources(
    client: TrustbookClient,
    space_id: str,
    sources: list[dict[str, Any]],
    *,
    note: Callable[[str], None],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout_s: float | None = None,
    poll_s: float = POLL_S,
) -> list[dict[str, Any]]:
    """The sources as they are once every one is ``ready`` or ``failed`` (same order).

    :raises TrustbookError: ``timeout_s`` passed first (the sources keep processing).
    """
    current = {str(s["id"]): s for s in sources}
    start = clock()
    shown: dict[str, str] = {}
    last_note = start
    while True:
        lines = []
        for sid, source in current.items():
            line = _progress(source)
            if shown.get(sid) != line:
                shown[sid] = line
                lines.append(line)
        now = clock()
        if not lines and now - last_note >= QUIET_S:
            lines = [shown[sid] for sid, s in current.items() if s.get("status") not in DONE]
        for line in lines:
            note(f"[{int(now - start)}s] {line}")
        if lines:
            last_note = now
        if all(s.get("status") in DONE for s in current.values()):
            return list(current.values())
        if timeout_s is not None and now - start >= timeout_s:
            raise TrustbookError(
                f"Sources still processing after {int(timeout_s)} s; they continue in the "
                "background (see `trustbook sources list` or `trustbook queue`)."
            )
        sleep(poll_s)
        for sid, source in current.items():
            if source.get("status") not in DONE:
                current[sid] = client.source(space_id, sid)
