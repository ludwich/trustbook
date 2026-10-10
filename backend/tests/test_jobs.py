from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from app.capabilities.http import current_space
from app.jobs.handlers import HandlerRegistry, JobContext, default_handlers, register_handler
from app.jobs.pool import WorkerPool, error_message, workers_for_power
from app.jobs.repository import JobNotFoundError, JobRepository, JobStateError
from tests.conftest import wait_for


@pytest.fixture
def repo(global_engine: Any) -> JobRepository:
    return JobRepository(global_engine)


def test_workers_for_power() -> None:
    assert workers_for_power("low", 16) == 1
    assert workers_for_power("normal", 16) == 2
    assert workers_for_power("high", 16) == 14
    assert workers_for_power("high", 2) == 2
    assert workers_for_power("high") >= 2


def test_enqueue_positions_and_get(repo: JobRepository) -> None:
    a = repo.enqueue("k", "A", payload={"x": 1})
    b = repo.enqueue("k", "B", space_id="s1")
    assert (a.position, b.position) == (1, 2)
    assert a.status == "queued"
    assert repo.get(a.id).payload == '{"x": 1}'
    with pytest.raises(JobNotFoundError):
        repo.get("missing")


def test_claim_order_and_active_space(repo: JobRepository) -> None:
    a = repo.enqueue("k", "A")
    b = repo.enqueue("k", "B", space_id="s1")
    c = repo.enqueue("k", "C", space_id="s2")
    claimed = repo.claim_next("s2")
    assert claimed is not None and claimed.id == c.id
    assert claimed.status == "running" and claimed.started_at
    assert repo.claim_next(None).id == a.id  # type: ignore[union-attr]
    assert repo.claim_next("nope").id == b.id  # type: ignore[union-attr]
    assert repo.claim_next(None) is None


def test_pause_resume_cancel(repo: JobRepository) -> None:
    job = repo.enqueue("k", "A")
    assert repo.pause(job.id).status == "paused"
    assert repo.claim_next(None) is None
    with pytest.raises(JobStateError, match="Only waiting jobs can be paused"):
        repo.pause(job.id)
    assert repo.resume(job.id).status == "queued"
    with pytest.raises(JobStateError, match="Only paused jobs can be resumed"):
        repo.resume(job.id)
    cancelled = repo.cancel(job.id)
    assert cancelled.status == "cancelled" and cancelled.finished_at
    with pytest.raises(JobStateError, match="already cancelled"):
        repo.cancel(job.id)


def test_finish_does_not_override_cancel(repo: JobRepository) -> None:
    job = repo.enqueue("k", "A")
    repo.claim_next(None)
    repo.set_progress(job.id, 1.7)
    assert repo.get(job.id).progress == 1.0
    repo.cancel(job.id)
    repo.finish(job.id, "done")
    assert repo.get(job.id).status == "cancelled"


def test_reorder(repo: JobRepository) -> None:
    a, b, c = (repo.enqueue("k", name) for name in "ABC")
    repo.pause(c.id)
    repo.reorder([c.id, a.id])
    positions = {j.id: j.position for j in repo.list_for_view()}
    assert positions[c.id] < positions[b.id] < positions[a.id]
    with pytest.raises(JobStateError, match="once"):
        repo.reorder([a.id, a.id])
    with pytest.raises(JobNotFoundError):
        repo.reorder(["missing"])
    repo.claim_next(None)
    running = next(j for j in repo.list_for_view() if j.status == "running")
    with pytest.raises(JobStateError, match="Only waiting jobs"):
        repo.reorder([running.id])


def test_requeue_interrupted_and_cancel_for_space(repo: JobRepository) -> None:
    a = repo.enqueue("k", "A", space_id="s")
    b = repo.enqueue("k", "B", space_id="s")
    other = repo.enqueue("k", "C", space_id="t")
    repo.claim_next("s")
    assert repo.requeue_interrupted() == 1
    assert repo.get(a.id).status == "queued"
    repo.claim_next("s")
    assert repo.cancel_for_space("s") == [a.id]
    assert repo.get(b.id).status == "cancelled"
    assert repo.get(other.id).status == "queued"
    assert repo.counts() == {"cancelled": 2, "queued": 1}


def test_list_for_view_limits_finished(repo: JobRepository) -> None:
    for i in range(5):
        repo.cancel(repo.enqueue("k", f"J{i}").id)
    waiting = repo.enqueue("k", "W")
    view = repo.list_for_view(finished_limit=3)
    assert view[0].id == waiting.id
    assert len(view) == 4
    assert repo.latest("k").id == waiting.id  # type: ignore[union-attr]
    assert repo.find_unfinished("k").id == waiting.id  # type: ignore[union-attr]
    assert repo.find_unfinished("other") is None


def test_handler_registry() -> None:
    registry = HandlerRegistry()

    def handler(_ctx: JobContext) -> None:
        return None

    registry.register("x", handler)
    assert registry.get("x") is handler
    assert registry.kinds() == ["x"]
    register_handler("test-default", handler)
    assert default_handlers.get("test-default") is handler


def test_error_message() -> None:
    class WithMessageError(Exception):
        message = "Plain words."

    assert error_message(WithMessageError()) == "Plain words."
    assert error_message(ValueError("bad")) == "ValueError: bad"
    assert error_message(KeyError()) == "KeyError"


def _pool(repo: JobRepository, handlers: HandlerRegistry, **kw: Any) -> WorkerPool:
    return WorkerPool(repo, handlers, poll_interval=0.05, **kw)


def test_pool_runs_jobs(repo: JobRepository) -> None:
    handlers = HandlerRegistry()
    seen: list[tuple[str | None, Any, str | None]] = []

    def work(ctx: JobContext) -> None:
        ctx.progress(0.5)
        space = current_space.get()
        seen.append((ctx.space_id, ctx.payload, space.name if space else None))

    def fail(_ctx: JobContext) -> None:
        raise ValueError("broken input")

    handlers.register("work", work)
    handlers.register("fail", fail)
    pool = _pool(repo, handlers, space_name=lambda _sid: "ACME")
    pool.start()
    try:
        ok = repo.enqueue("work", "OK", space_id="s1", payload={"n": 1})
        bad = repo.enqueue("fail", "Bad")
        unknown = repo.enqueue("nobody", "Unknown")
        pool.notify()
        wait_for(lambda: all(repo.status(j.id) != "queued" for j in (ok, bad, unknown)))
        wait_for(lambda: repo.status(unknown.id) == "failed")
        wait_for(lambda: repo.status(ok.id) == "done")
    finally:
        pool.stop()
    assert seen == [("s1", {"n": 1}, "ACME")]
    assert repo.get(ok.id).progress == 1.0
    assert repo.get(bad.id).error == "ValueError: broken input"
    assert repo.get(unknown.id).error == "No handler for job type “nobody”."


def test_pool_cooperative_cancel(repo: JobRepository) -> None:
    handlers = HandlerRegistry()
    started = threading.Event()

    def long(ctx: JobContext) -> None:
        started.set()
        while True:
            ctx.check_cancelled()
            threading.Event().wait(0.01)

    handlers.register("long", long)
    pool = _pool(repo, handlers, workers=1)
    pool.start()
    try:
        job = repo.enqueue("long", "Long")
        pool.notify()
        assert started.wait(5)
        repo.cancel(job.id)
        pool.request_cancel(job.id)
        wait_for(lambda: not pool.is_cancelled(job.id) or repo.status(job.id) == "cancelled")
        follow = repo.enqueue("long-done", "Next")
        handlers.register("long-done", lambda _ctx: None)
        pool.notify()
        wait_for(lambda: repo.status(follow.id) == "done")
    finally:
        pool.stop()
    assert repo.status(job.id) == "cancelled"


def test_handler_raising_cancel_marks_cancelled(repo: JobRepository) -> None:
    handlers = HandlerRegistry()

    def give_up(ctx: JobContext) -> None:
        pool.request_cancel(ctx.job_id)
        assert ctx.is_cancelled()
        ctx.check_cancelled()

    handlers.register("give_up", give_up)
    pool = _pool(repo, handlers, workers=1)
    pool.start()
    try:
        job = repo.enqueue("give_up", "G")
        pool.notify()
        wait_for(lambda: repo.status(job.id) == "cancelled")
    finally:
        pool.stop()


def test_pool_resize_and_active_space(repo: JobRepository) -> None:
    handlers = HandlerRegistry()
    order: list[str] = []
    handlers.register("rec", lambda ctx: order.append(ctx.payload["name"]))
    pool = _pool(repo, handlers, workers=1)
    repo.enqueue("rec", "A", payload={"name": "A"})
    repo.enqueue("rec", "B", space_id="s", payload={"name": "B"})
    pool.set_active_space("s")
    assert pool.active_space_id == "s"
    pool.resize(3)
    assert pool.workers == 3
    pool.start()
    pool.start()  # idempotent
    try:
        wait_for(lambda: len(order) == 2)
        pool.resize(1)
        wait_for(lambda: pool._alive == 1)
    finally:
        pool.stop()
    assert set(order) == {"A", "B"}


def test_usable_cpus_respects_cgroup_quota(tmp_path: Path) -> None:
    from app.jobs.pool import usable_cpus

    cpu_max = tmp_path / "cpu.max"
    unlimited = usable_cpus(tmp_path / "missing")
    assert unlimited >= 1
    cpu_max.write_text("max 100000\n")
    assert usable_cpus(cpu_max) == unlimited
    cpu_max.write_text("150000 100000\n")  # docker --cpus=1.5
    assert usable_cpus(cpu_max) == min(unlimited, 2)
