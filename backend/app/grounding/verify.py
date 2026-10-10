"""Grounding layer 4 (sentence verification) plus layer 5, and the refusal rule (SPEC §8).

Each sentence is scored against every chunk it cites with the bundled grounding checker:
``(premise = the cited chunk's text, hypothesis = the sentence)``, and, when it cites more than one
chunk, against all of them joined in citation order (a claim may need two cited chunks together:
``pool_connections=DEFAULT_POOLSIZE`` plus ``DEFAULT_POOLSIZE = 10``; D-042). The sentence
score (also against a focused window of the cited lines sharing its terms, D-044) is the
highest of those scores; it passes at ``score >= verify_threshold``. The premise is
the chunk text exactly as indexed, so a code chunk keeps its ``File: … (lines a-b)`` first line
(it is part of what the model was shown and lets "defined in sessions.py" be checked).

Layer 5 (:mod:`app.grounding.code_checks`) runs whether or not the checker is on; a sentence that
fails it fails, whatever its score.

Sentences citing code that fail the checker (and pass layer 5) get one more chance from the code
judge (:mod:`app.grounding.code_judge`, D-045), when it is on. It can only rescue a sentence,
never fail one, and its failures leave the checker's result in place.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from app.capabilities.bundled import ProviderRegistry
from app.grounding.code_checks import (
    SnapshotFiles,
    focus_premise,
    locator_problem,
    missing_identifiers,
    missing_literals,
)
from app.grounding.code_judge import JudgeItem, is_code_chunk
from app.retrieval.service import RetrievedChunk

logger = logging.getLogger("trustbook.grounding")

ScorePairs = Callable[[Sequence[tuple[str, str]]], list[float]]
"""Scores ``(premise, hypothesis)`` pairs, each in 0..1 (the grounding checker)."""

JudgeCode = Callable[[Sequence[JudgeItem]], list[bool] | None]
"""The code judge: one pass/fail per ``(sentence, cited code chunks)``, None when it failed."""

Method = Literal["checker", "code_judge"]

MAX_FAILED_SHARE = 0.5  # refuse (or regenerate) when more than half of the sentences fail


@dataclass(frozen=True)
class GroundingSettings:
    """Whether the grounding checker and the code judge are on; the pass threshold."""

    enabled: bool = True
    verify_threshold: float = 0.5
    code_judge: bool = True


@dataclass(frozen=True)
class SentenceCheck:
    """Outcome of layers 4 and 5 for one sentence.

    ``score``: highest checker score over the cited chunks, each alone and (2+ citations) all
    joined (None when the checker is off).
    ``chunk_scores``: per cited chunk, in the sentence's citation order.
    ``problems``: layer 5 failures (missing identifiers, file/line ranges not in the snapshot).
    ``method``: what passed it: ``checker``, ``code_judge`` (the checker failed it and the code
    judge rescued it), or None (the sentence failed, or the checker is off).
    """

    score: float | None
    chunk_scores: tuple[float | None, ...]
    problems: tuple[str, ...] = ()
    passed: bool = True
    method: Method | None = None

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

    @property
    def methods(self) -> list[Method | None]:
        return [c.method for c in self.checks]


def _clean(value: float) -> float:
    score = float(value)
    if not math.isfinite(score):
        return 0.0
    return min(1.0, max(0.0, score))


class Verifier:
    """Runs layers 4 and 5 over the sentences of one reply.

    ``score_pairs`` None means the grounding checker is turned off: layer 4 is skipped (answers
    are badged "unverified"), layer 5 still runs. ``code_judge`` None means sentences about code
    are decided by the checker alone (Phase 2 behaviour).
    """

    def __init__(
        self,
        score_pairs: ScorePairs | None,
        *,
        threshold: float,
        files: SnapshotFiles | None = None,
        code_judge: JudgeCode | None = None,
    ) -> None:
        self._score_pairs = score_pairs
        self._threshold = threshold
        self._files = files or SnapshotFiles(None)
        self._code_judge = code_judge

    @property
    def enabled(self) -> bool:
        return self._score_pairs is not None

    @property
    def threshold(self) -> float:
        return self._threshold

    def check(self, sentences: Sequence[tuple[str, Sequence[RetrievedChunk]]]) -> Checked:
        """Check ``(sentence text, cited chunks)`` pairs; one checker call for the whole reply."""
        self._joined: dict[int, float] = {}
        scores = self._scores(sentences)
        checks: list[SentenceCheck] = []
        for i, ((text, cited), chunk_scores) in enumerate(zip(sentences, scores, strict=True)):
            problems = self._code_problems(text, cited)
            known = [s for s in chunk_scores if s is not None]
            if i in self._joined:
                known.append(self._joined[i])
            score = max(known) if known else None
            layer4 = not self.enabled or (score is not None and score >= self._threshold)
            passed = layer4 and not problems and bool(cited)
            checks.append(
                SentenceCheck(
                    score=score,
                    chunk_scores=tuple(chunk_scores),
                    problems=tuple(problems),
                    passed=passed,
                    method="checker" if self.enabled and passed else None,
                )
            )
        return Checked(self._judge(sentences, checks))

    def _judge(
        self,
        sentences: Sequence[tuple[str, Sequence[RetrievedChunk]]],
        checks: list[SentenceCheck],
    ) -> list[SentenceCheck]:
        """Second chance for sentences citing code that failed only the checker (D-045)."""
        if self._code_judge is None or not self.enabled:
            return checks
        pending = [
            i
            for i, (check, (_, cited)) in enumerate(zip(checks, sentences, strict=True))
            if not check.passed and not check.problems and any(is_code_chunk(c) for c in cited)
        ]
        if not pending:
            return checks
        items = [
            (sentences[i][0], [c for c in sentences[i][1] if is_code_chunk(c)]) for i in pending
        ]
        verdicts = self._code_judge(items)
        if verdicts is None or len(verdicts) != len(items):
            if verdicts is not None:
                logger.warning("The code judge returned the wrong number of verdicts; ignored")
            return checks
        result = list(checks)
        for i, ok in zip(pending, verdicts, strict=True):
            if ok:
                result[i] = replace(result[i], passed=True, method="code_judge")
        rescued = sum(verdicts)
        logger.info("Code judge: %d of %d sentence(s) about code passed", rescued, len(items))
        return result

    def _scores(
        self, sentences: Sequence[tuple[str, Sequence[RetrievedChunk]]]
    ) -> list[list[float | None]]:
        if self._score_pairs is None:
            return [[None] * len(cited) for _, cited in sentences]
        pairs = [(chunk.text, text) for text, cited in sentences for chunk in cited]
        joined = [
            ("\n\n".join(chunk.text for chunk in cited), text)
            for text, cited in sentences
            if len(cited) > 1
        ]
        focused_index: list[int] = []
        focused: list[tuple[str, str]] = []
        for i, (text, cited) in enumerate(sentences):
            window = focus_premise(text, [chunk.text for chunk in cited])
            if window:
                focused_index.append(i)
                focused.append((window, text))
        raw = self._score_pairs(pairs + joined + focused) if pairs else []
        if len(raw) != len(pairs) + len(joined) + len(focused):
            raise RuntimeError("The grounding checker returned the wrong number of scores")
        flat = iter(_clean(v) for v in raw)
        per_chunk: list[list[float | None]] = [
            [next(flat) for _ in cited] for _, cited in sentences
        ]
        self._joined = {i: next(flat) for i, (_, cited) in enumerate(sentences) if len(cited) > 1}
        for i in focused_index:
            score = next(flat)
            self._joined[i] = max(self._joined.get(i, 0.0), score)
        return per_chunk

    def _code_problems(self, text: str, cited: Sequence[RetrievedChunk]) -> list[str]:
        problems = [
            f"`{name}` is not in the cited chunks"
            for name in missing_identifiers(text, (c.text for c in cited))
        ]
        if any("file" in chunk.locator for chunk in cited):
            problems.extend(
                f"{literal} is not in the cited code"
                for literal in missing_literals(text, (c.text for c in cited))
            )
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
    code_judge: JudgeCode | None = None,
) -> Verifier:
    """The verifier for one answer or overview in a space (checker off → layer 5 only).

    ``code_judge`` is used only when ``settings.code_judge`` is on.
    """
    return Verifier(
        score_pairs if settings.enabled else None,
        threshold=settings.verify_threshold,
        files=SnapshotFiles(space_folder / "sources" if space_folder is not None else None),
        code_judge=code_judge if settings.code_judge else None,
    )
