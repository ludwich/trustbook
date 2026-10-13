"""Job handler registry. A handler runs in a worker thread with a :class:`JobContext`."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

# Why a running job makes no progress yet (D-113). A real state the job is in, never a guess:
# its thread waits for the model lock calibration holds, or a bundled model is loading.
JobStage = Literal["waiting_for_calibration", "loading_models"]


class JobCancelledError(Exception):
    """Raised by handlers (via :meth:`JobContext.check_cancelled`) to stop early."""


@dataclass
class JobContext:
    """What a handler gets: its payload, progress reporting and cooperative cancellation."""

    job_id: str
    kind: str
    space_id: str | None
    payload: dict[str, Any]
    _progress: Callable[[float], None] = field(repr=False)
    _cancelled: Callable[[], bool] = field(repr=False)
    _stage: Callable[[JobStage | None], None] = field(default=lambda _stage: None, repr=False)

    def progress(self, value: float) -> None:
        """Report progress in 0..1."""
        self._progress(value)

    def stage(self, value: JobStage | None) -> None:
        """Say why the job is not making progress yet (``None``: it is working)."""
        self._stage(value)

    def is_cancelled(self) -> bool:
        return self._cancelled()

    def check_cancelled(self) -> None:
        """Raise :class:`JobCancelledError` if the job was cancelled."""
        if self._cancelled():
            raise JobCancelledError


Handler = Callable[[JobContext], None]


class HandlerRegistry:
    """Maps job kinds to handler functions."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._lock = threading.Lock()

    def register(self, kind: str, handler: Handler) -> None:
        with self._lock:
            self._handlers[kind] = handler

    def get(self, kind: str) -> Handler | None:
        with self._lock:
            return self._handlers.get(kind)

    def kinds(self) -> list[str]:
        with self._lock:
            return sorted(self._handlers)


default_handlers = HandlerRegistry()


def register_handler(kind: str, handler: Handler) -> None:
    """Register ``handler`` for ``kind`` in the process-wide default registry."""
    default_handlers.register(kind, handler)
