"""The grounded answer pipeline (grounding layers 2 and 3).

1. Retrieve from the enabled, ready sources. None → refuse (``no_sources``).
2. Layer 2: top reranker score below ``refuse_threshold`` → refuse (``not_in_sources``) before
   any model call.
3. Layer 3: numbered chunks, forced-citation JSON, one retry on unusable JSON, sentences without
   valid chunk ids dropped. No sentences returned → ``not_in_sources``; sentences returned but
   none valid, or unusable JSON twice → ``invalid_output``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.chat.citations import number_citations
from app.chat.llm import ChatAdapterFactory, generate_structured
from app.chat.output import InvalidOutputError, parse_answer
from app.chat.prompt import ANSWER_SCHEMA, answer_messages, labelled
from app.chat.schemas import Refusal, RefusalReason, SentenceOut, Stage
from app.chat.text import content_terms, question_topic
from app.retrieval.service import Retrieval

logger = logging.getLogger("trustbook.chat")

NOT_IN_SOURCES = "Not in your sources"
NO_CITED_ANSWER = "No answer with citations"


@dataclass(frozen=True)
class Answered:
    sentences: list[SentenceOut]
    dropped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Refused:
    refusal: Refusal


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


def answer(
    question: str,
    *,
    retrieve: Callable[[str], Retrieval],
    chat_adapter: ChatAdapterFactory,
    refuse_threshold: float,
    on_stage: Callable[[Stage], None] = lambda _stage: None,
) -> Outcome:
    """Answer ``question`` from the space's sources or refuse. Adapter errors propagate."""
    on_stage("retrieving")
    retrieval = retrieve(question)
    if not retrieval.source_ids:
        return Refused(refusal("no_sources", question))
    top = retrieval.top_score
    if top is None or top < refuse_threshold or not retrieval.chunks:
        logger.info("Refused before generating: top score %s < %s", top, refuse_threshold)
        return Refused(refusal("not_in_sources", question))

    on_stage("generating")
    chunks = labelled(retrieval.chunks)
    adapter = chat_adapter()
    try:
        parsed = generate_structured(
            adapter,
            answer_messages(question, retrieval.chunks),
            ANSWER_SCHEMA,
            lambda text: parse_answer(text, chunks.keys()),
        )
    except InvalidOutputError:
        logger.info("Refused: the chat reply was unusable twice")
        return Refused(refusal("invalid_output", question))

    on_stage("checking")
    if not parsed.sentences:
        if parsed.dropped:
            logger.info("Refused: all %d sentence(s) lacked valid citations", len(parsed.dropped))
            return Refused(refusal("invalid_output", question))
        return Refused(refusal("not_in_sources", question))
    if parsed.dropped:
        logger.info("Dropped %d sentence(s) without valid citations", len(parsed.dropped))
    return Answered(number_citations(parsed.sentences, chunks), list(parsed.dropped))
