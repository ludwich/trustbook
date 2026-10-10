"""Shared fixtures. Real network access is blocked for every test."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.capabilities.bundled import ProviderRegistry
from app.capabilities.http import OutboundHttp, RequestLogRepository
from app.config import Paths
from app.db.engine import dispose_all, get_engine
from app.db.migrate import migrate
from app.jobs.handlers import HandlerRegistry
from app.main import create_app
from tests.fakes import (
    FakeEmbedder,
    FakeGrounding,
    FakeReranker,
    FakeTranscriber,
    Router,
    write_models_lock,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Tests must not use the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    yield
    dispose_all()


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(
        data=tmp_path / "data",
        browse_root=tmp_path / "host",
        models_dir=tmp_path / "models",
        static_dir=tmp_path / "static-missing",
        searxng_url="http://searxng:8080",
        version="test",
        mock_llm=True,
    )


@pytest.fixture
def mock_paths(paths: Paths) -> Paths:
    return paths


@pytest.fixture
def no_mock_paths(paths: Paths) -> Paths:
    return replace(paths, mock_llm=False)


@pytest.fixture
def models_present(paths: Paths) -> Paths:
    write_models_lock(paths.models_dir, present=True)
    return paths


@pytest.fixture
def fake_providers(paths: Paths) -> ProviderRegistry:
    registry = ProviderRegistry(paths.models_dir, factories={})
    registry.override(
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        grounding=FakeGrounding(),
        transcriber=FakeTranscriber(),
    )
    return registry


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def global_engine(paths: Paths) -> Any:
    migrate(paths.global_db, "global")
    return get_engine(paths.global_db)


@pytest.fixture
def request_log(global_engine: Any) -> RequestLogRepository:
    return RequestLogRepository(global_engine)


@pytest.fixture
def http(request_log: RequestLogRepository, router: Router) -> OutboundHttp:
    return OutboundHttp(request_log, transport=router.transport)


AppFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(
    paths: Paths, fake_providers: ProviderRegistry, router: Router
) -> Iterator[AppFactory]:
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        options: dict[str, Any] = {
            "paths": paths,
            "providers": fake_providers,
            "transport": router.transport,
            "handlers": HandlerRegistry(),
            "start_workers": False,
            "calibrate_on_start": False,
            "probe_search_on_start": False,
        }
        options.update(overrides)
        client = TestClient(create_app(**options))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: AppFactory) -> TestClient:
    return make_client()


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll ``predicate`` until it is true or fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")
