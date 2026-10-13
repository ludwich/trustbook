"""Retrieval and verification for one space, shared by chat answers and ideation (D-094).

Both use exactly the same retrieval (enabled, ready sources; hybrid search, rerank, parent
context) and the same verifier (grounding checker, code checks, code judge), so a recommendation
is held to the same standard as an answer.
"""

from __future__ import annotations

from collections.abc import Callable

from app.db.engine import get_engine
from app.grounding.code_judge import CodeJudge
from app.grounding.verify import Verifier, build_verifier, checker_scores
from app.retrieval.service import Retrieval, retrieve
from app.services import Services
from app.spaces.service import SpaceRecord


def retriever(services: Services, space: SpaceRecord) -> Callable[[str], Retrieval]:
    """Retrieval over the space's enabled, ready sources, with the current settings."""
    engine = get_engine(space.db_path)
    index = services.indexes.for_space(space.id)
    settings = services.settings.load()

    def run_retrieval(question: str) -> Retrieval:
        providers = services.providers
        with providers.use.shared():  # waits while calibration measures
            return retrieve(
                engine,
                index,
                providers.embedder(),
                providers.reranker(),
                question,
                parent_context=settings.retrieval.parent_context,
                outline=settings.retrieval.outline,
            )

    return run_retrieval


def verifier_factory(
    services: Services, space: SpaceRecord, seed: int | None = None
) -> Callable[[], Verifier]:
    """A fresh verifier per answer or turn (layers 4 and 5, the code judge with ``seed``)."""

    def verifier() -> Verifier:
        return build_verifier(
            services.grounding_settings(),
            checker_scores(services.providers),
            space.folder,
            CodeJudge(services.chat_adapter, seed=seed),
        )

    return verifier


def refuse_threshold(services: Services) -> float:
    """Layer 2's threshold (SPEC §8)."""
    return services.settings.load().thresholds.refuse
