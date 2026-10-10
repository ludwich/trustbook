"""The grounded answer pipeline (grounding layers 2 to 5, SPEC §8).

1. Retrieve from the enabled, ready sources. None → refuse (``no_sources``).
2. Layer 2: top reranker score below ``refuse_threshold`` → refuse (``not_in_sources``) before
   any model call.
3. Layer 3: numbered chunks, forced-citation JSON, one retry on unusable JSON, sentences without
   valid chunk ids dropped. No sentences returned → ``not_in_sources``; sentences returned but
   none valid, or unusable JSON twice → ``invalid_output``.
4. Layers 4 and 5 (:mod:`app.grounding.verify`): every sentence is checked against its cited
   chunks. More than half failing → one regeneration with the same chunks plus
   :data:`~app.chat.prompt.REGENERATE_INSTRUCTION`, checked again. Then the refusal rule: more
   than half failing, or the first sentence failing → refuse (``unsupported``). Otherwise the
   failing sentences are removed (``partially_verified``) or none fail (``verified``). With the
   grounding checker off, layer 4 is skipped and the badge is ``unverified``.

Citation numbers are given after removal, in order of first use among the kept sentences.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.chat.citations import number_citations
from app.chat.llm import ChatAdapterFactory, generate_structured
from app.chat.output import DraftSentence, InvalidOutputError, ParsedAnswer, parse_answer
from app.chat.prompt import ANSWER_SCHEMA, answer_messages, labelled
from app.chat.schemas import Badge, Diagnostics, Refusal, RefusalReason, SentenceOut, Stage
from app.chat.text import content_terms, question_topic
from app.grounding.verify import Checked, Verifier
from app.retrieval.service import Retrieval, RetrievedChunk

logger = logging.getLogger("trustbook.chat")

NOT_IN_SOURCES = "Not in your sources"
NO_CITED_ANSWER = "No answer with citations"


@dataclass(frozen=True)
class Answered:
    """A verified answer. ``scores[i]`` maps each chunk id cited by sentence ``i`` to its
    grounding checker score (None when the checker is off)."""

    sentences: list[SentenceOut]
    dropped: list[str] = field(default_factory=list)
    badge: Badge = "verified"
    removed: list[str] = field(default_factory=list)
    scores: list[dict[str, float | None]] = field(default_factory=list)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)


@dataclass(frozen=True)
class Refused:
    refusal: Refusal
    diagnostics: Diagnostics = field(default_factory=Diagnostics)


Outcome = Answered | Refused


NO_SOURCES_SUGGESTION = "Add or tick at least one ready source, then ask again."
GENERIC_SUGGESTION = "Add sources about this topic, then ask again."


def suggestion_for(question: str) -> str:
    """``Add sources that cover “…”, then ask again.`` built from the question's own words."""
    topic = question_topic(question)
    if not content_terms(topic):  # nothing meaningful left ("What is it?")
        return GENERIC_SUGGESTION
    return f"Add sources that cover “{topic}”, then ask again."


def refusal(reason: RefusalReason, question: str) -> Refusal:
    """The plain-English refusal for ``reason``.

    ``message`` is only the title the UI shows ("Not in your sources"); ``suggestion`` is the
    subtitle, so the two never repeat each other.
    """
    if reason == "no_sources":
        return Refusal(reason=reason, message=NOT_IN_SOURCES, suggestion=NO_SOURCES_SUGGESTION)
    if reason == "invalid_output":
        return Refusal(
            reason=reason,
            message=NO_CITED_ANSWER,
            suggestion="The chat model's reply did not cite your sources, so it is not shown. "
            "Ask again or rephrase the question.",
        )
    return Refusal(reason=reason, message=NOT_IN_SOURCES, suggestion=suggestion_for(question))


def check_sentences(
    verifier: Verifier,
    sentences: list[DraftSentence],
    chunks: Mapping[str, RetrievedChunk],
) -> Checked:
    """Layers 4 and 5 for validated sentences (labels must be in ``chunks``)."""
    return verifier.check([(s.text, [chunks[i] for i in s.chunk_ids]) for s in sentences])


def kept_and_removed(
    sentences: list[DraftSentence], checked: Checked
) -> tuple[list[tuple[DraftSentence, tuple[float | None, ...]]], list[str]]:
    """Passing sentences with their per-chunk scores, and the texts of the failing ones."""
    kept: list[tuple[DraftSentence, tuple[float | None, ...]]] = []
    removed: list[str] = []
    for sentence, check in zip(sentences, checked.checks, strict=True):
        if check.passed:
            kept.append((sentence, check.chunk_scores))
        else:
            removed.append(sentence.text)
    return kept, removed


def answer(
    question: str,
    *,
    retrieve: Callable[[str], Retrieval],
    chat_adapter: ChatAdapterFactory,
    refuse_threshold: float,
    verifier: Verifier,
    on_stage: Callable[[Stage], None] = lambda _stage: None,
) -> Outcome:
    """Answer ``question`` from the space's sources or refuse. Adapter errors propagate."""
    on_stage("retrieving")
    retrieval = retrieve(question)
    if not retrieval.source_ids:
        return Refused(refusal("no_sources", question))
    top = retrieval.top_score
    diagnostics = Diagnostics(top_rerank=top)
    if top is None or top < refuse_threshold or not retrieval.chunks:
        logger.info("Refused before generating: top score %s < %s", top, refuse_threshold)
        return Refused(refusal("not_in_sources", question), diagnostics)

    on_stage("generating")
    chunks = labelled(retrieval.chunks)
    adapter = chat_adapter()

    def generate(regenerate: bool) -> ParsedAnswer:
        return generate_structured(
            adapter,
            answer_messages(question, retrieval.chunks, regenerate=regenerate),
            ANSWER_SCHEMA,
            lambda text: parse_answer(text, chunks.keys()),
        )

    try:
        parsed = generate(False)
    except InvalidOutputError:
        logger.info("Refused: the chat reply was unusable twice")
        return Refused(refusal("invalid_output", question), diagnostics)

    on_stage("checking")
    if not parsed.sentences:
        if parsed.dropped:
            logger.info("Refused: all %d sentence(s) lacked valid citations", len(parsed.dropped))
            return Refused(refusal("invalid_output", question), diagnostics)
        return Refused(refusal("not_in_sources", question), diagnostics)
    if parsed.dropped:
        logger.info("Dropped %d sentence(s) without valid citations", len(parsed.dropped))

    checked = check_sentences(verifier, parsed.sentences, chunks)
    regenerated = False
    if checked.mostly_failed:
        logger.info(
            "%d of %d sentence(s) failed verification; regenerating once",
            checked.failed,
            len(checked.checks),
        )
        regenerated = True
        on_stage("generating")
        try:
            second = generate(True)
        except InvalidOutputError:
            second = ParsedAnswer([])
        on_stage("checking")
        if not second.sentences:
            # The first reply was mostly unsupported and the second has nothing usable.
            first = [c.diagnostic_score for c in checked.checks]
            return Refused(
                refusal("unsupported", question),
                Diagnostics(top_rerank=top, sentence_scores=first, regenerated=True),
            )
        parsed = second
        checked = check_sentences(verifier, parsed.sentences, chunks)

    diagnostics = Diagnostics(
        top_rerank=top,
        sentence_scores=[c.diagnostic_score for c in checked.checks],
        regenerated=regenerated,
    )
    if checked.refuse:
        logger.info(
            "Refused: %d of %d sentence(s) failed verification (first passed: %s)",
            checked.failed,
            len(checked.checks),
            checked.checks[0].passed if checked.checks else False,
        )
        return Refused(refusal("unsupported", question), diagnostics)

    kept, removed = kept_and_removed(parsed.sentences, checked)
    sentences = number_citations([s for s, _ in kept], chunks)
    scores = [
        {chunks[label].chunk_id: score for label, score in zip(s.chunk_ids, cs, strict=True)}
        for s, cs in kept
    ]
    badge: Badge
    if not verifier.enabled:
        badge = "unverified"
    elif removed:
        badge = "partially_verified"
    else:
        badge = "verified"
    if removed:
        logger.info("Removed %d unsupported sentence(s)", len(removed))
    return Answered(
        sentences,
        list(parsed.dropped),
        badge=badge,
        removed=removed,
        scores=scores,
        diagnostics=diagnostics,
    )
