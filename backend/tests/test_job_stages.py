"""A running job says why it makes no progress yet: calibration holds the models, or a model
loads (CLI nit 2, D-113). Real states the backend knows, not a guess from elapsed time."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.capabilities.bundled import ModelInfo, ModelUseLock, ProviderRegistry, RuntimeOptions
from app.index.memory import InMemoryIndexRegistry
from app.jobs.handlers import HandlerRegistry, JobContext
from app.jobs.pool import WorkerPool
from app.jobs.repository import JobRepository
from tests.conftest import AppFactory, wait_for
from tests.fakes import FakeGrounding, FakeReranker, FakeTranscriber, write_models_lock
from tests.ingest_helpers import services_of

GB = 1024**3
TEXT = "# Pumps\n\nThe pump starts when the valve opens. It stops at 4 bar."


class SlowLoadEmbedder:
    dim = 3

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, float(len(t)), 0.0] for t in texts]


# --- units ----------------------------------------------------------------------------------


def test_shared_lock_reports_only_a_real_wait() -> None:
    lock = ModelUseLock()
    waits: list[str] = []
    with lock.shared(on_wait=lambda: waits.append("wait")):
        pass
    assert waits == []  # nobody calibrating: no wait reported

    holding, release = threading.Event(), threading.Event()

    def calibrate() -> None:
        with lock.exclusive():
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=calibrate)
    thread.start()
    assert holding.wait(10)
    entered = threading.Event()

    def use() -> None:
        with lock.shared(on_wait=lambda: waits.append("wait")):
            entered.set()

    user = threading.Thread(target=use)
    user.start()
    wait_for(lambda: waits == ["wait"], timeout=30)
    assert not entered.is_set()  # still blocked by calibration
    release.set()
    thread.join(10)
    user.join(10)
    assert entered.is_set() and waits == ["wait"]


def test_ingest_embedder_loaded(paths: Any) -> None:
    write_models_lock(paths.models_dir)
    registry = ProviderRegistry(
        paths.models_dir,
        factories={"embeddings": lambda _i, _o: SlowLoadEmbedder()},
        memory=lambda: 64 * GB,
    )
    registry.configure(RuntimeOptions(torch_threads=2, embedding_streams=2, stream_threads=1))
    assert registry.ingest_streams() == 2
    assert not registry.ingest_embedder_loaded(0)
    registry.ingest_embedder(0)
    assert registry.ingest_embedder_loaded(0) and not registry.ingest_embedder_loaded(1)
    registry.release_replicas()
    assert not registry.ingest_embedder_loaded(0)

    single = ProviderRegistry(paths.models_dir, factories={})
    single.override(embedder=SlowLoadEmbedder())
    assert single.ingest_embedder_loaded(0)  # an injected embedder is always loaded


def test_stage_only_while_running_and_cleared_when_done(global_engine: Any) -> None:
    repo = JobRepository(global_engine)
    handlers = HandlerRegistry()
    gate = threading.Event()

    def work(ctx: JobContext) -> None:
        ctx.stage("loading_models")
        gate.wait(10)

    handlers.register("work", work)
    queued = repo.enqueue("work", "Queued")
    repo.set_stage(queued.id, "loading_models")
    assert repo.stage(repo.get(queued.id)) is None  # only running jobs have a stage
    repo.set_stage(queued.id, None)

    pool = WorkerPool(repo, handlers, workers=1, poll_interval=0.05)
    pool.start()
    try:
        pool.notify()
        wait_for(lambda: repo.stage(repo.get(queued.id)) == "loading_models", timeout=30)
        gate.set()
        wait_for(lambda: repo.status(queued.id) == "done", timeout=30)
    finally:
        pool.stop()
    job = repo.get(queued.id)
    assert repo.stage(job) is None
    repo.set_stage(job.id, "loading_models")  # even a stale entry is never shown for a done job
    assert repo.stage(job) is None


# --- through the API ------------------------------------------------------------------------


@pytest.fixture
def load_gate() -> threading.Event:
    return threading.Event()


@pytest.fixture
def staged_client(make_client: AppFactory, paths: Any, load_gate: threading.Event) -> TestClient:
    write_models_lock(paths.models_dir)

    def factory(_info: ModelInfo, _options: RuntimeOptions) -> SlowLoadEmbedder:
        load_gate.wait(20)  # loading the embedder takes as long as the test wants
        return SlowLoadEmbedder()

    registry = ProviderRegistry(
        paths.models_dir, factories={"embeddings": factory}, memory=lambda: 64 * GB
    )
    registry.override(
        reranker=FakeReranker(), grounding=FakeGrounding(), transcriber=FakeTranscriber()
    )
    registry.configure(RuntimeOptions(torch_threads=2, embedding_streams=1))
    return make_client(providers=registry, indexes=InMemoryIndexRegistry())


def test_source_and_queue_show_calibration_wait_then_model_loading(
    staged_client: TestClient, load_gate: threading.Event
) -> None:
    client = staged_client
    services = services_of(client)
    sid = client.post("/api/spaces", json={"name": "S"}).json()["id"]
    created = client.post(f"/api/spaces/{sid}/sources/paste", json={"text": TEXT}).json()
    url = f"/api/spaces/{sid}/sources/{created['id']}"
    assert client.get(url).json()["stage"] is None  # queued: no stage

    def source() -> dict[str, Any]:
        data: dict[str, Any] = client.get(url).json()
        return data

    def queue_stage() -> Any:
        jobs = client.get("/api/jobs").json()["jobs"]
        return next(j["stage"] for j in jobs if j["id"] == created["job_id"])

    calibrating, release = threading.Event(), threading.Event()

    def calibrate() -> None:
        with services.providers.use.exclusive():
            calibrating.set()
            release.wait(20)

    calibration = threading.Thread(target=calibrate)
    calibration.start()
    assert calibrating.wait(10)
    job = services.jobs.claim_next(None)
    assert job is not None
    worker = threading.Thread(target=services.pool._run, args=(job,))
    worker.start()
    try:
        # The job waits for calibration before reading.
        wait_for(lambda: source()["stage"] == "waiting_for_calibration", timeout=30)
        assert source()["status"] == "reading" and source()["progress"] == 0.0
        assert queue_stage() == "waiting_for_calibration"

        # Calibration is done; the first batch loads the embedder.
        release.set()
        wait_for(lambda: source()["stage"] == "loading_models", timeout=30)
        assert source()["status"] == "indexing"
        assert queue_stage() == "loading_models"
    finally:
        release.set()
        load_gate.set()
        calibration.join(20)
        worker.join(20)
    done = source()
    assert (done["status"], done["stage"]) == ("ready", None)
    assert queue_stage() is None
