"""The grilling engine: the first branches, then one question per turn with a recommended answer.

One model call per turn (D-093): the server picks the branch (:func:`app.ideation.map.next_node`)
and the call returns the question, the recommendation, new branches from the last answer and
risks. Recommendations are grounded per sentence like chat answers (D-094):

1. Retrieval over the idea plus the branch title, from the enabled, ready sources.
2. Layer 2: no sources, or a top reranker score below ``refuse_threshold`` → the model gets no
   chunks, so every recommendation sentence can only be a suggestion.
3. Layer 3: cited sentences must cite ids from the prompt; a sentence citing an unknown id is
   dropped. Sentences with an empty ``chunk_ids`` list are suggestions (labelled so).
4. Layers 4 and 5: the cited sentences are checked against their cited chunks with the same
   verifier as chat (grounding checker, code checks, code judge), and their citations chosen the
   same way. A failing cited sentence is removed (shown in the "Unverified, not from your
   sources" drawer), never turned into a suggestion. When the refusal rule fires over the cited
   sentences (more than half failing, or the first one), every cited sentence is removed. There
   is no regeneration, to keep turns short.

Questions, branch titles and risks carry no citations and are never shown as grounded (D-095);
the prompt forbids stating source facts in them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.chat.citations import number_citations
from app.chat.llm import AnswerCall, ChatAdapterFactory, generate_structured
from app.chat.output import DraftSentence
from app.chat.pipeline import check_sentences, choose_citations, failed_sentences_out
from app.chat.prompt import labelled
from app.chat.schemas import Stage
from app.grounding.verify import Verifier
from app.ideation import map as branch_map
from app.ideation.map import MapNode
from app.ideation.output import ParsedSeed, ParsedTurn, parse_seed, parse_turn
from app.ideation.prompt import (
    SEED_SCHEMA,
    TURN_SCHEMA,
    Exchange,
    seed_messages,
    turn_messages,
)
from app.ideation.repository import AskedQuestion
from app.ideation.schemas import (
    Grounding,
    Recommendation,
    RecommendationBadge,
    RecommendationSentence,
    Risk,
)
from app.retrieval.service import Retrieval, RetrievedChunk

logger = logging.getLogger("trustbook.ideation")

OnStage = Callable[[Stage], None]


def _round(seconds: float) -> float:
    return round(seconds, 3)


def retrieval_query(idea: str, branch: str) -> str:
    """What a turn retrieves for: the idea and the branch being asked about."""
    return f"{' '.join(idea.split())}\n{' '.join(branch.split())}"


def seed(
    idea: str,
    *,
    chat_adapter: ChatAdapterFactory,
    on_stage: OnStage = lambda _s: None,
    calls: list[AnswerCall] | None = None,
) -> ParsedSeed:
    """The idea's title and its 3-4 first branches (one call, one retry on unusable JSON)."""
    on_stage("generating")
    return generate_structured(
        chat_adapter(), seed_messages(idea), SEED_SCHEMA, parse_seed, calls=calls, think=False
    )


@dataclass(frozen=True)
class TurnContext:
    """What one turn knows: the idea, the whole map, the branch to ask about (None: nothing is
    left, so only new branches and risks), the answered branches (oldest first) and the last
    answer, whose new branches and risks this turn adds."""

    idea: str
    nodes: Sequence[MapNode]
    target: MapNode | None
    history: Sequence[Exchange] = ()
    last: Exchange | None = None


@dataclass(frozen=True)
class TurnResult:
    asked: AskedQuestion | None
    branches: list[tuple[str, str | None]] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)


def grounding_of(sentences: Sequence[RecommendationSentence]) -> Grounding:
    cited = sum(s.kind == "cited" for s in sentences)
    if cited == 0:
        return "suggestion"
    return "cited" if cited == len(sentences) else "partly_cited"


def badge_of(
    drafted_cited: int, kept_cited: int, removed: int, verifier: Verifier
) -> RecommendationBadge | None:
    """As in chat: None when no cited sentence is shown."""
    if drafted_cited == 0 or kept_cited == 0:
        return None
    if not verifier.enabled:
        return "unverified"
    return "partially_verified" if removed else "verified"


@dataclass(frozen=True)
class Checked:
    """The recommendation after layers 4 and 5, with the flat citation list and diagnostics."""

    recommendation: Recommendation
    citations: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def check_recommendation(
    query: str,
    parsed: ParsedTurn,
    chunks: Mapping[str, RetrievedChunk],
    verifier: Verifier,
) -> Checked:
    """Verify the cited sentences, remove the failing ones (all of them when the refusal rule
    fires) and number the kept citations. Suggestions pass through as they are."""
    cited = parsed.cited
    kept_by_index: dict[int, DraftSentence] = {}
    scores_by_index: dict[int, tuple[float | None, ...]] = {}
    removed: list[str] = []
    diagnostics: dict[str, Any] = {}
    if cited:
        started = time.perf_counter()
        checked = check_sentences(verifier, cited, chunks)
        diagnostics["sentence_scores"] = [c.diagnostic_score for c in checked.checks]
        diagnostics["sentence_methods"] = checked.methods
        diagnostics["failed_sentences"] = [
            f.model_dump() for f in failed_sentences_out(cited, checked, verifier.threshold)
        ]
        if checked.refuse:
            removed = [s.text for s in cited]
            diagnostics["refusal_rule"] = True
            logger.info("Removed all %d cited recommendation sentence(s)", len(cited))
        else:
            kept, removed = choose_citations(query, cited, checked, chunks, verifier)
            for item in kept:
                kept_by_index[item.index] = item.sentence
                scores_by_index[item.index] = item.scores
        diagnostics["verification_s"] = _round(time.perf_counter() - started)
        diagnostics["judge_calls"] = verifier.judge_calls

    order = sorted(kept_by_index)
    numbered = number_citations([kept_by_index[i] for i in order], chunks)
    numbered_by_index = dict(zip(order, numbered, strict=True))
    sentences: list[RecommendationSentence] = []
    citations: list[dict[str, Any]] = []
    cited_seen = 0
    for draft in parsed.sentences:
        if not draft.chunk_ids:
            sentences.append(RecommendationSentence(text=draft.text, kind="suggestion"))
            continue
        index = cited_seen
        cited_seen += 1
        if index not in numbered_by_index:
            continue
        sentence = numbered_by_index[index]
        sentences.append(
            RecommendationSentence(text=sentence.text, citations=sentence.citations, kind="cited")
        )
        labels = kept_by_index[index].chunk_ids
        for label, score in zip(labels, scores_by_index[index], strict=True):
            citations.append({"chunk_id": chunks[label].chunk_id, "verifier_score": score})
    recommendation = Recommendation(
        grounding=grounding_of(sentences),
        badge=badge_of(len(cited), len(kept_by_index), len(removed), verifier),
        sentences=sentences,
        removed=removed,
    )
    return Checked(recommendation, citations, diagnostics)


def turn(
    context: TurnContext,
    *,
    retrieve: Callable[[str], Retrieval],
    chat_adapter: ChatAdapterFactory,
    refuse_threshold: float,
    verifier: Verifier,
    on_stage: OnStage = lambda _s: None,
    calls: list[AnswerCall] | None = None,
) -> TurnResult:
    """Ask about ``context.target`` (or only add branches and risks when it is None).

    Raises :class:`~app.chat.output.InvalidOutputError` when the reply is unusable twice; adapter
    errors propagate.
    """
    started = time.perf_counter()
    meter: list[AnswerCall] = calls if calls is not None else []
    target = context.target
    chunks: list[RetrievedChunk] = []
    query = ""
    diagnostics: dict[str, Any] = {}
    if target is not None:
        on_stage("retrieving")
        query = retrieval_query(context.idea, target.title)
        retrieval = retrieve(query)
        top = retrieval.top_score
        diagnostics["top_rerank"] = top
        diagnostics["retrieval_s"] = _round(time.perf_counter() - started)
        if (
            retrieval.source_ids
            and retrieval.chunks
            and top is not None
            and top >= refuse_threshold
        ):
            chunks = list(retrieval.chunks)
        else:
            diagnostics["below_threshold"] = True  # layer 2: suggestions only

    on_stage("generating")
    labels = labelled(chunks)
    messages = turn_messages(
        context.idea,
        chunks,
        context.nodes,
        target=target,
        history=context.history,
        last=context.last,
    )
    parsed = generate_structured(
        chat_adapter(),
        messages,
        TURN_SCHEMA,
        lambda text: parse_turn(text, labels.keys(), expect_question=target is not None),
        calls=meter,
        think=False,  # D-103: a turn is a short structured reply; thinking used the whole budget
    )
    diagnostics["dropped"] = len(parsed.dropped)

    asked: AskedQuestion | None = None
    if target is not None:
        if parsed.cited:
            on_stage("checking")
        checked = check_recommendation(query, parsed, labels, verifier)
        diagnostics.update(checked.diagnostics)
        diagnostics["answer_calls"] = len(meter)
        diagnostics["prompt_tokens"] = [c.prompt_tokens for c in meter]
        diagnostics["generation_s"] = [_round(c.seconds) for c in meter]
        diagnostics["total_s"] = _round(time.perf_counter() - started)
        asked = AskedQuestion(
            question=parsed.question,
            recommendation=checked.recommendation,
            citations=checked.citations,
            diagnostics=diagnostics,
        )

    names = {label: node_id for node_id, label in branch_map.labels(context.nodes).items()}
    last_id = context.last.node_id if context.last is not None else None
    proposed = [(title, names.get(parent or "")) for title, parent in parsed.branches]
    branches = (
        branch_map.place_branches(context.nodes, proposed, default_parent=last_id)
        if context.last is not None
        else []
    )
    risks = [Risk(text=r, node_id=last_id) for r in parsed.risks] if context.last else []
    return TurnResult(asked, branches, risks)
