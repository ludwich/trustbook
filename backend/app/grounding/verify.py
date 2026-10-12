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

With the judge on (:mod:`app.grounding.code_judge`, D-045 and D-063), one judge call per check
covers two kinds of sentence:

- **Rescue:** a sentence that fails the checker (and passes layer 5) and cites code passes if the
  judge accepts it on its cited code chunks alone (D-045), in the batch **and** when asked again
  with the sentence alone. Sentences citing only prose (web pages, documents, transcripts) are
  never rescued: the deterministic rescue gates rest on code anchors, which prose rarely has, and
  word overlap cannot tell a negated or role-swapped claim from a true one (D-064).
- **Confirmation:** a sentence the checker passed (with a score below :data:`CONFIRM_BELOW`, by
  default every one) fails if the judge's verdict contradicts it (a veto). HHEM passes role-swapped
  false claims on technical prose at 0.58–0.98 (D-063). Anchors missing from the judge's evidence
  lines veto unless the docstring field those lines belong to holds them (D-073).

A failed sentence keeps why it failed (:meth:`SentenceCheck.failure`) and the names no cited chunk
holds (``missing``), for diagnostics and the regeneration feedback (D-074, D-075).

Any judge failure (not set up, endpoint error, unusable reply, timeout) leaves the checker's
results in place.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from app.capabilities.bundled import ProviderRegistry
from app.grounding.code_checks import (
    SnapshotFiles,
    focus_premise,
    locator_problem,
    missing_identifiers,
    missing_literals,
)
from app.grounding.code_judge import (
    JudgeItem,
    JudgeOutcome,
    is_code_chunk,
    missing_plain_identifiers,
)
from app.retrieval.service import RetrievedChunk

logger = logging.getLogger("trustbook.grounding")

ScorePairs = Callable[[Sequence[tuple[str, str]]], list[float]]
"""Scores ``(premise, hypothesis)`` pairs, each in 0..1 (the grounding checker)."""

JudgeCode = Callable[[Sequence[JudgeItem]], list[JudgeOutcome] | None]
"""The judge: one outcome per ``(sentence, cited chunks)``, None when the call failed."""


@runtime_checkable
class FollowsUp(Protocol):
    """A judge that can ask again about one sentence, naming what its evidence lines lacked."""

    def follow_up(self, item: JudgeItem, unanchored: Sequence[str]) -> JudgeOutcome | None: ...


Method = Literal["checker", "code_judge"]

MAX_FAILED_SHARE = 0.5  # refuse (or regenerate) when more than half of the sentences fail
# Checker passes below this score are confirmed by the judge (D-063). Measured on live data, HHEM
# passed false claims at 0.58-0.98 and true ones at 0.56-0.99: no band separates them, so every
# checker pass is confirmed. A finite bound is for tests and tuning only.
CONFIRM_BELOW = math.inf


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
    ``method``: what passed it: ``checker``, ``code_judge`` (the checker failed it and the judge
    rescued it), or None (the sentence failed, or the checker is off).
    ``vetoed``: the checker passed it but the judge's confirmation contradicted it, so it failed.
    ``missing``: names and literals of the sentence that no cited chunk contains (layer 5, or the
    anchors behind a veto); the regeneration feedback says where the context has them (D-074).
    ``judge_reason``: why the judge vetoed it, or did not rescue it ("" when not asked).
    """

    score: float | None
    chunk_scores: tuple[float | None, ...]
    problems: tuple[str, ...] = ()
    passed: bool = True
    method: Method | None = None
    vetoed: bool = False
    missing: tuple[str, ...] = ()
    judge_reason: str = ""

    def failure(self, threshold: float) -> str:
        """Why the sentence failed, in a few words ("" when it passed); for diagnostics."""
        if self.passed:
            return ""
        if self.problems:
            return "; ".join(self.problems)
        if self.vetoed:
            return f"judge veto: {self.judge_reason}" if self.judge_reason else "judge veto"
        if not self.chunk_scores:
            return "no citations"
        score = "none" if self.score is None else f"{self.score:.2f}"
        why = f"checker score {score} < {threshold:g}"
        return f"{why}; judge: {self.judge_reason}" if self.judge_reason else why

    @property
    def diagnostic_score(self) -> float | None:
        """The score the eval runner sees: 0.0 when a code check failed or the judge vetoed it
        (it fails at any threshold)."""
        if self.problems or self.vetoed:
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

    @property
    def vetoed(self) -> list[int]:
        """Indices of the sentences the judge's confirmation failed."""
        return [i for i, c in enumerate(self.checks) if c.vetoed]


def _clean(value: float) -> float:
    score = float(value)
    if not math.isfinite(score):
        return 0.0
    return min(1.0, max(0.0, score))


class Verifier:
    """Runs layers 4 and 5 over the sentences of one reply.

    ``score_pairs`` None means the grounding checker is turned off: layer 4 is skipped (answers
    are badged "unverified"), layer 5 still runs. ``code_judge`` None means every sentence is
    decided by the checker alone (Phase 2 behaviour). ``judge_seconds`` adds up the time spent
    waiting for the judge over every :meth:`check` of this verifier.
    """

    def __init__(
        self,
        score_pairs: ScorePairs | None,
        *,
        threshold: float,
        files: SnapshotFiles | None = None,
        code_judge: JudgeCode | None = None,
        confirm_below: float = CONFIRM_BELOW,
    ) -> None:
        self._score_pairs = score_pairs
        self._threshold = threshold
        self._files = files or SnapshotFiles(None)
        self._code_judge = code_judge
        self._confirm_below = confirm_below
        self.judge_seconds = 0.0
        self.judge_calls = 0

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
            problems, missing = self._code_problems(text, cited)
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
                    missing=tuple(missing),
                )
            )
        return Checked(self._judge(sentences, checks))

    def _judge(
        self,
        sentences: Sequence[tuple[str, Sequence[RetrievedChunk]]],
        checks: list[SentenceCheck],
    ) -> list[SentenceCheck]:
        """Rescue failed sentences and confirm checker passes in one judge call (D-045, D-063).

        Rescue: failed only the checker (no layer 5 problems) and cites code; the judge sees only
        the cited code chunks (D-045, D-064). Confirmation: passed on the checker with a score
        below the confirmation bound; the judge sees every cited chunk. A batch verdict that would
        change the checker's result, a rescue or a veto, is asked again with the sentence alone
        and stands only if that verdict agrees: in a batch of similar statements the judge's
        verdicts were noisy (D-063), and a judge-only decision gets the same second look both ways.
        """
        if self._code_judge is None or not self.enabled:
            return checks
        rescue = [
            i
            for i, (check, (_, cited)) in enumerate(zip(checks, sentences, strict=True))
            if not check.passed and not check.problems and any(is_code_chunk(c) for c in cited)
        ]
        confirm = [
            i
            for i, check in enumerate(checks)
            if check.passed
            and check.method == "checker"
            and check.score is not None
            and check.score < self._confirm_below
        ]
        pending = sorted(rescue + confirm)
        if not pending:
            return checks
        items = [
            (
                sentences[i][0],
                [c for c in sentences[i][1] if is_code_chunk(c)]
                if i in rescue
                else sentences[i][1],
            )
            for i in pending
        ]
        outcomes = self._ask_judge(items)
        if outcomes is None or len(outcomes) != len(items):
            if outcomes is not None:
                logger.warning("The judge returned the wrong number of verdicts; ignored")
            return checks
        result = list(checks)
        rescued = vetoed = 0
        for n, (i, outcome) in enumerate(zip(pending, outcomes, strict=True)):
            if i in rescue and not outcome.accepted and outcome.unanchored:
                # The judge said "supported" but its lines lack names the cited chunks hold:
                # asked once more, alone, naming them (D-085). Its verdict must pass every rule
                # and is the second, alone look a rescue needs.
                again_one = self._follow_up(items[n], outcome.unanchored)
                if again_one is not None and again_one.accepted:
                    result[i] = replace(result[i], passed=True, method="code_judge")
                    rescued += 1
                    continue
                outcome = again_one or outcome
            if i in rescue and not outcome.accepted:
                # The names the judge found in no cited chunk feed only the regeneration feedback
                # (D-074, D-082); the sentence fails either way.
                missing = tuple(dict.fromkeys([*result[i].missing, *outcome.absent]))
                result[i] = replace(result[i], missing=missing, judge_reason=outcome.reason)
            elif i in rescue:
                again = self._ask_judge([items[n]])
                if again is None or len(again) != 1 or not again[0].accepted:
                    logger.info("Judge rescue of sentence %d not repeated alone; failed", i + 1)
                    why = again[0].reason if again and len(again) == 1 else "no second verdict"
                    result[i] = replace(result[i], judge_reason=f"not repeated alone ({why})")
                    continue
                result[i] = replace(result[i], passed=True, method="code_judge")
                rescued += 1
            elif outcome.vetoed:
                # Asked again alone; a veto only for lines lacking names the cited chunks hold
                # is asked naming them (D-085).
                alone = self._follow_up(items[n], outcome.unanchored)
                if alone is None:
                    again = self._ask_judge([items[n]])
                    alone = again[0] if again and len(again) == 1 else None
                if alone is None or not alone.vetoed:
                    logger.info("Judge veto of sentence %d not repeated alone; kept", i + 1)
                    continue
                missing = tuple(dict.fromkeys([*result[i].missing, *alone.absent]))
                result[i] = replace(
                    result[i],
                    passed=False,
                    method=None,
                    vetoed=True,
                    missing=missing,
                    judge_reason=alone.reason,
                )
                vetoed += 1
                logger.info("Judge vetoed sentence %d (%s)", i + 1, alone.reason)
        logger.info(
            "Judge: %d of %d failed sentence(s) rescued, %d of %d confirmation(s) vetoed",
            rescued,
            len(rescue),
            vetoed,
            len(confirm),
        )
        return result

    def _ask_judge(self, items: Sequence[JudgeItem]) -> list[JudgeOutcome] | None:
        assert self._code_judge is not None
        started = time.perf_counter()
        try:
            return self._code_judge(items)
        finally:
            self.judge_seconds += time.perf_counter() - started
            self.judge_calls += 1

    def _follow_up(self, item: JudgeItem, unanchored: Sequence[str]) -> JudgeOutcome | None:
        """The judge's follow-up on ``item`` naming ``unanchored`` (D-085); None when there is
        nothing to name, the judge cannot follow up, or the call failed."""
        if not unanchored or not isinstance(self._code_judge, FollowsUp):
            return None
        started = time.perf_counter()
        try:
            return self._code_judge.follow_up(item, unanchored)
        finally:
            self.judge_seconds += time.perf_counter() - started
            self.judge_calls += 1

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

    def _code_problems(
        self, text: str, cited: Sequence[RetrievedChunk]
    ) -> tuple[list[str], list[str]]:
        """Layer 5 problems, and the names and literals among them that no cited chunk has."""
        problems, missing = name_problems(text, cited)
        for chunk in cited:
            problem = locator_problem(self._files, chunk.source_id, chunk.locator)
            if problem is not None:
                problems.append(problem)
        return list(dict.fromkeys(problems)), list(dict.fromkeys(missing))


def name_problems(text: str, cited: Sequence[RetrievedChunk]) -> tuple[list[str], list[str]]:
    """Layer 5's name and literal checks of ``text`` on ``cited``: the problems, and the names
    and literals that no cited chunk has. Backticked names always (D-042); for a sentence citing a
    file chunk also unbackticked code names (D-084) and literals (D-044). The citation choice
    after verification keeps these passing on the chunks it keeps (D-052, D-086)."""
    texts = [c.text for c in cited]
    missing = missing_identifiers(text, texts)
    problems = [f"`{name}` is not in the cited chunks" for name in missing]
    if any("file" in chunk.locator for chunk in cited):
        plain = missing_plain_identifiers(text, texts)
        missing.extend(plain)
        problems.extend(f"{name} is not in the cited chunks" for name in plain)
        literals = missing_literals(text, texts)
        missing.extend(literals)
        problems.extend(f"{literal} is not in the cited code" for literal in literals)
    return problems, missing


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
