"""Grounding layer 4 (sentence verification) plus layer 5, and the refusal rule (SPEC §8).

Each sentence is scored against every chunk it cites with the bundled grounding checker:
``(premise = the cited chunk's text, hypothesis = the sentence)``. The sentence score is the
highest of those scores; it passes at ``score >= verify_threshold``. The premise is the chunk text
exactly as indexed, so a code chunk keeps its ``File: … (lines a-b)`` first line (it is part of
what the model was shown and lets "defined in sessions.py" be checked).

Layer 5 (:mod:`app.grounding.code_checks`) runs whether or not the checker is on; a sentence that
fails it fails, whatever its score.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.capabilities.bundled import ProviderRegistry
from app.grounding.code_checks import SnapshotFiles, locator_problem, missing_identifiers
from app.retrieval.service import RetrievedChunk

ScorePairs = Callable[[Sequence[tuple[str, str]]], list[float]]
"""Scores ``(premise, hypothesis)`` pairs, each in 0..1 (the grounding checker)."""

MAX_FAILED_SHARE = 0.5  # refuse (or regenerate) when more than half of the sentences fail


@dataclass(frozen=True)
class GroundingSettings:
    """Whether the grounding checker is on, and the pass threshold for a sentence."""

    enabled: bool = True
    verify_threshold: float = 0.5


@dataclass(frozen=True)
class SentenceCheck:
    """Outcome of layers 4 and 5 for one sentence.

    ``score``: highest checker score over the cited chunks (None when the checker is off).
    ``chunk_scores``: per cited chunk, in the sentence's citation order.
    ``problems``: layer 5 failures (missing identifiers, file/line ranges not in the snapshot).
    """

    score: float | None
    chunk_scores: tuple[float | None, ...]
    problems: tuple[str, ...] = ()
    passed: bool = True

    @property
    def diagnostic_score(self) -> float | None:
        """The score the eval runner sees: 0.0 when a code check failed (fails at any threshold)."""
        if self.problems:
            return 0.0
        return self.score


@dataclass(frozen=True)
class Checked:
    """All sentence checks of one reply, in the reply's order."""

    checks: list[SentenceCheck] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(not c.passed for c in self.checks)

    @property
    def failed_share(self) -> float:
        return self.failed / len(self.checks) if self.checks else 1.0

    @property
    def mostly_failed(self) -> bool:
        """More than half of the sentences failed (exactly half is not "more than half")."""
        return self.failed_share > MAX_FAILED_SHARE

    @property
    def refuse(self) -> bool:
        """The refusal rule: more than half failed, or the first sentence failed."""
        return not self.checks or self.mostly_failed or not self.checks[0].passed


def _clean(value: float) -> float:
    score = float(value)
    if not math.isfinite(score):
        return 0.0
    return min(1.0, max(0.0, score))


class Verifier:
    """Runs layers 4 and 5 over the sentences of one reply.

    ``score_pairs`` None means the grounding checker is turned off: layer 4 is skipped (answers
    are badged "unverified"), layer 5 still runs.
    """

    def __init__(
        self,
        score_pairs: ScorePairs | None,
        *,
        threshold: float,
        files: SnapshotFiles | None = None,
    ) -> None:
        self._score_pairs = score_pairs
        self._threshold = threshold
        self._files = files or SnapshotFiles(None)

    @property
    def enabled(self) -> bool:
        return self._score_pairs is not None

    @property
    def threshold(self) -> float:
        return self._threshold

    def check(self, sentences: Sequence[tuple[str, Sequence[RetrievedChunk]]]) -> Checked:
        """Check ``(sentence text, cited chunks)`` pairs; one checker call for the whole reply."""
        scores = self._scores(sentences)
        checks: list[SentenceCheck] = []
        for (text, cited), chunk_scores in zip(sentences, scores, strict=True):
            problems = self._code_problems(text, cited)
            known = [s for s in chunk_scores if s is not None]
            score = max(known) if known else None
            layer4 = not self.enabled or (score is not None and score >= self._threshold)
            checks.append(
                SentenceCheck(
                    score=score,
                    chunk_scores=tuple(chunk_scores),
                    problems=tuple(problems),
                    passed=layer4 and not problems and bool(cited),
                )
            )
        return Checked(checks)

    def _scores(
        self, sentences: Sequence[tuple[str, Sequence[RetrievedChunk]]]
    ) -> list[list[float | None]]:
        if self._score_pairs is None:
            return [[None] * len(cited) for _, cited in sentences]
        pairs = [(chunk.text, text) for text, cited in sentences for chunk in cited]
        raw = self._score_pairs(pairs) if pairs else []
        if len(raw) != len(pairs):
            raise RuntimeError("The grounding checker returned the wrong number of scores")
        flat = iter(_clean(v) for v in raw)
        return [[next(flat) for _ in cited] for _, cited in sentences]

    def _code_problems(self, text: str, cited: Sequence[RetrievedChunk]) -> list[str]:
        problems = [
            f"`{name}` is not in the cited chunks"
            for name in missing_identifiers(text, (c.text for c in cited))
        ]
        for chunk in cited:
            problem = locator_problem(self._files, chunk.source_id, chunk.locator)
            if problem is not None:
                problems.append(problem)
        return list(dict.fromkeys(problems))


def checker_scores(providers: ProviderRegistry) -> ScorePairs:
    """The bundled grounding checker, used "shared" (it waits while calibration measures)."""

    def score(pairs: Sequence[tuple[str, str]]) -> list[float]:
        with providers.use.shared():
            return providers.grounding().score(pairs)

    return score


def build_verifier(
    settings: GroundingSettings,
    score_pairs: ScorePairs,
    space_folder: Path | None,
) -> Verifier:
    """The verifier for one answer or overview in a space (checker off → layer 5 only)."""
    return Verifier(
        score_pairs if settings.enabled else None,
        threshold=settings.verify_threshold,
        files=SnapshotFiles(space_folder / "sources" if space_folder is not None else None),
    )
