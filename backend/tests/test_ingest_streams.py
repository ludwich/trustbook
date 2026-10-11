"""Indexing one source with parallel embedding streams and batched index writes (Gate 1 G2)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.capabilities.bundled import ModelInfo, ProviderRegistry, RuntimeOptions
from app.db.engine import get_engine
from app.db.models import Chunk
from app.index.memory import InMemoryIndexRegistry, InMemorySpaceIndex
from app.ingest import errors, service
from app.jobs.handlers import JobCancelledError, JobContext
from app.services import Services
from tests.conftest import AppFactory
from tests.fakes import FakeGrounding, FakeReranker, FakeTranscriber, write_models_lock
from tests.ingest_helpers import run_jobs, services_of

GB = 1024**3
# ~120 chunks of prose (500-token target): 15 batches of 8 for three streams.
TEXT = "# Report\n\n" + "\n\n".join(
    f"Paragraph {p}. " + " ".join(f"Finding {p}-{i} concerns supplier {i}." for i in range(60))
    for p in range(96)
)


def expected_vector(text: str) -> list[float]:
    return [float(len(text)), float(sum(map(ord, text)) % 997), 1.0]


class Replica:
    """A fake embedding replica that records the thread of every call."""

    dim = 3

    def __init__(self, number: int, log: list[tuple[int, str, int]]) -> None:
        self.number = number
        self.log = log
        self.fail_after: int | None = None
        self.before_call: Any = None

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [expected_vector(t) for t in texts]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.before_call is not None:
            self.before_call()
        self.log.append((self.number, threading.current_thread().name, len(texts)))
        time.sleep(0.005)  # a model call takes time, so every stream gets work
        if self.fail_after is not None and len(self.log) > self.fail_after:
            raise RuntimeError("model crashed")
        return [expected_vector(t) for t in texts]


@pytest.fixture
def replicas() -> list[Replica]:
    return []


@pytest.fixture
def calls() -> list[tuple[int, str, int]]:
    return []


@pytest.fixture
def stream_client(
    make_client: AppFactory,
    paths: Any,
    replicas: list[Replica],
    calls: list[tuple[int, str, int]],
) -> TestClient:
    write_models_lock(paths.models_dir)

    def factory(_info: ModelInfo, _options: RuntimeOptions) -> Replica:
        replica = Replica(len(replicas), calls)
        replicas.append(replica)
        return replica

    registry = ProviderRegistry(
        paths.models_dir, factories={"embeddings": factory}, memory=lambda: 64 * GB
    )
    registry.override(
        reranker=FakeReranker(), grounding=FakeGrounding(), transcriber=FakeTranscriber()
    )
    registry.configure(RuntimeOptions(torch_threads=2, embedding_streams=3, stream_threads=2))
    return make_client(providers=registry, indexes=InMemoryIndexRegistry())


def _paste(client: TestClient, text: str = TEXT) -> tuple[str, dict[str, Any]]:
    sid = client.post("/api/spaces", json={"name": "S"}).json()["id"]
    created = client.post(f"/api/spaces/{sid}/sources/paste", json={"text": text}).json()
    return sid, created


def _chunks(services: Services, sid: str) -> list[Chunk]:
    record = services.spaces.get(sid)
    with Session(get_engine(record.db_path)) as session:
        return list(session.exec(select(Chunk).order_by(Chunk.ordinal)).all())


def _index(services: Services, sid: str) -> InMemorySpaceIndex:
    index = services.indexes.for_space(sid)
    assert isinstance(index, InMemorySpaceIndex)
    return index


def test_streams_index_every_chunk_with_its_own_vector(
    stream_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    replicas: list[Replica],
    calls: list[tuple[int, str, int]],
) -> None:
    monkeypatch.setattr(service, "WRITE_BATCH", 16)
    services = services_of(stream_client)
    sid, created = _paste(stream_client)
    index = _index(services, sid)
    appends: list[int] = []
    refreshes: list[int] = []
    original_append = index.append
    monkeypatch.setattr(
        index, "append", lambda c, v: (appends.append(len(c)), original_append(c, v))
    )
    monkeypatch.setattr(index, "refresh_text_index", lambda: refreshes.append(1))

    assert run_jobs(services) == 1
    source = stream_client.get(f"/api/spaces/{sid}/sources").json()[0]
    assert source["status"] == "ready", source

    chunks = _chunks(services, sid)
    assert len(chunks) >= 30
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))  # stable ordinals
    assert index.count() == len(chunks)
    for chunk in chunks:  # each chunk's vector was made from its own text
        stored, vector = index._rows[chunk.id]
        assert stored.text == chunk.text and stored.source_id == created["id"]
        assert vector == expected_vector(chunk.text)

    # Three replicas did the work, each only in its own stream thread; batches of 8.
    assert len(replicas) == 3
    by_replica: dict[int, set[str]] = {}
    for number, thread, size in calls:
        by_replica.setdefault(number, set()).add(thread)
        assert size <= 8
    assert all(len(threads) == 1 for threads in by_replica.values())  # one thread per replica
    assert set().union(*by_replica.values()) == {f"embed-stream-{i}" for i in range(3)}
    # Vectors were written in batches as they came, and the text index refreshed once.
    assert sum(appends) == len(chunks) and len(appends) >= 2
    assert all(n >= 16 for n in appends[:-1])
    assert refreshes == [1]


def _ctx(job_id: str, sid: str, source_id: str, cancelled: Any) -> JobContext:
    return JobContext(
        job_id=job_id,
        kind="ingest",
        space_id=sid,
        payload={"source_id": source_id},
        _progress=lambda _v: None,
        _cancelled=cancelled,
    )


def test_cancel_mid_way_leaves_no_partial_index(
    stream_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    replicas: list[Replica],
    calls: list[tuple[int, str, int]],
) -> None:
    monkeypatch.setattr(service, "WRITE_BATCH", 8)
    services = services_of(stream_client)
    sid, created = _paste(stream_client)
    flag = {"cancelled": False}

    def maybe_cancel() -> None:
        if len(calls) >= 3:
            flag["cancelled"] = True

    services.providers.ingest_embedder(0)  # load the replicas up front to hook them
    services.providers.ingest_embedder(1)
    services.providers.ingest_embedder(2)
    for replica in replicas:
        replica.before_call = maybe_cancel

    ctx = _ctx(created["job_id"], sid, created["id"], lambda: flag["cancelled"])
    with pytest.raises(JobCancelledError):
        services.ingest.handler(ctx)
    # Stopped long before the 15 batches: at most the queue (6) and one batch per stream more.
    assert 3 <= len(calls) <= 12
    stored = services.ingest.repo(sid).get(created["id"])
    assert (stored.status, stored.failure_reason) == ("failed", errors.CANCELLED[0])
    assert _index(services, sid).count() == 0
    assert _chunks(services, sid) == []


def test_model_failure_mid_way_fails_cleanly(
    stream_client: TestClient, replicas: list[Replica]
) -> None:
    services = services_of(stream_client)
    sid, _created = _paste(stream_client)
    services.providers.ingest_embedder(1).fail_after = 4  # type: ignore[attr-defined]
    run_jobs(services)
    source = stream_client.get(f"/api/spaces/{sid}/sources").json()[0]
    assert source["status"] == "failed"
    assert source["failure_reason"] == errors.INTERNAL[0]
    assert _index(services, sid).count() == 0
    assert _chunks(services, sid) == []


def test_refresh_replaces_changed_vectors_without_duplicates(
    stream_client: TestClient, tmp_path: Path
) -> None:
    services = services_of(stream_client)
    sid = stream_client.post("/api/spaces", json={"name": "S"}).json()["id"]
    folder = Path(services.paths.browse_root) / "proj"
    folder.mkdir(parents=True)
    for i in range(12):
        (folder / f"m{i:02}.py").write_text(
            "\n\n".join(f"def f{i}_{k}(x):\n    return x + {k}\n" for k in range(30))
        )
    tools = services.ingest.tools
    tools.scanner = lambda _root: []
    created = stream_client.post(f"/api/spaces/{sid}/sources/folder", json={"path": "proj"}).json()
    run_jobs(services)
    first = _chunks(services, sid)
    assert first and _index(services, sid).count() == len(first)
    # Incremental refresh (SPEC §7.5): only the changed file gets new chunks and vectors.
    (folder / "m03.py").write_text("def changed(x):\n    return x * 2\n")
    assert stream_client.post(f"/api/spaces/{sid}/sources/{created['id']}/refresh").status_code
    run_jobs(services)
    second = _chunks(services, sid)
    assert _index(services, sid).count() == len(second) == len(first) - 29
    old_ids = {c.id for c in first if json.loads(c.locator)["file"] != "m03.py"}
    new_ids = {c.id for c in second if json.loads(c.locator)["file"] == "m03.py"}
    assert old_ids == {c.id for c in second} - new_ids
    assert len(new_ids) == 1 and new_ids.isdisjoint({c.id for c in first})
    files = [json.loads(c.locator)["file"] for c in second]  # chunk order follows file order
    assert files == sorted(files) and len(set(files)) == 12
    source = stream_client.get(f"/api/spaces/{sid}/sources").json()[0]
    assert source["stats"]["changed_files"] == 1
    assert source["stats"]["unchanged_files"] == 11


def test_replicas_are_released_when_no_more_indexing_waits(
    stream_client: TestClient, replicas: list[Replica]
) -> None:
    """Replicas hold ~0.55 GB each; idle indexing must not keep them (D-054)."""
    services = services_of(stream_client)
    _paste(stream_client)
    assert run_jobs(services) == 1
    assert len(replicas) == 3  # the job used three stream replicas
    assert services.providers._replicas == {}  # released after the last ingest job
    services.providers.ingest_embedder(1)  # the next indexing job loads them again
    assert len(replicas) == 4


def test_others_unfinished_counts_only_other_jobs_of_that_kind(
    stream_client: TestClient,
) -> None:
    jobs = services_of(stream_client).jobs
    first = jobs.enqueue("ingest", "a")
    jobs.enqueue("ingest", "b")
    jobs.enqueue("overview", "c")
    assert jobs.others_unfinished("ingest", first.id) == 1


def test_a_successful_ingest_ends_with_the_job_done(stream_client: TestClient) -> None:
    """Regression (Gate 3): a bare `raise` in `finally` failed every successful ingest job."""
    services = services_of(stream_client)
    sid, created = _paste(stream_client)
    assert run_jobs(services) == 1
    source = stream_client.get(f"/api/spaces/{sid}/sources").json()[0]
    assert source["status"] == "ready"
    job = services.jobs.get(created["job_id"])
    assert job is not None and job.status == "done", job
    assert job.error is None
    listed = {j["id"]: j for j in stream_client.get("/api/jobs").json()["jobs"]}
    assert listed[created["job_id"]]["status"] == "done"


def test_a_failing_ingest_ends_with_the_job_failed(
    stream_client: TestClient, replicas: list[Replica]
) -> None:
    services = services_of(stream_client)
    sid, created = _paste(stream_client)
    services.providers.ingest_embedder(1).fail_after = 0  # type: ignore[attr-defined]
    run_jobs(services)
    source = stream_client.get(f"/api/spaces/{sid}/sources").json()[0]
    assert source["status"] == "failed"
    job = services.jobs.get(created["job_id"])
    assert job is not None and job.status == "failed"
