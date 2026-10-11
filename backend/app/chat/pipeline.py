"""The grounded answer pipeline (grounding layers 2 to 5, SPEC §8).

1. Retrieve from the enabled, ready sources. None → refuse (``no_sources``).
2. Layer 2: top reranker score below ``refuse_threshold`` → refuse (``not_in_sources``) before
   any model call.
3. Layer 3: numbered chunks, forced-citation JSON, one retry on unusable JSON, sentences without
   valid chunk ids dropped. No sentences returned → ``not_in_sources``; sentences returned but
   none valid, or unusable JSON twice → ``invalid_output``. A sentence naming a backticked
   identifier that only an uncited context chunk holds also cites that chunk (D-056).
4. Layers 4 and 5 (:mod:`app.grounding.verify`): every sentence is checked against its cited
   chunks. When the refusal rule would fire (more than half failing, or the first sentence
   failing), one regeneration with the same chunks plus
   :data:`~app.chat.prompt.REGENERATE_INSTRUCTION` is checked again (D-056). Then the refusal
   rule: more than half failing, or the first sentence failing → refuse (``unsupported``).
   Otherwise the failing sentences are removed (``partially_verified``) or none fail
   (``verified``). With the grounding checker off, layer 4 is skipped and the badge is
   ``unverified``. The judge rescues sentences the checker failed and confirms borderline checker
   passes (D-045, D-056); ``diagnostics.sentence_methods`` records which passed them.
5. Citations of passing sentences are chosen (:mod:`app.chat.citation_choice`, D-052 and its
   extension): supporting chunks only, near-copies collapsed, the defining chunk preferred.

Citation numbers are given after removal, in order of first use among the kept sentences.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from app.chat.citation_choice import choose, complete_citations, upgrade_candidates
from app.chat.citations import number_citations
from app.chat.llm import ChatAdapterFactory, generate_structured
from app.chat.output import DraftSentence, InvalidOutputError, ParsedAnswer, parse_answer
from app.chat.prompt import ANSWER_SCHEMA, TEMPERATURE, answer_messages, labelled
from app.chat.schemas import (
    Badge,
    CitationChange,
    Diagnostics,
    Generation,
    GenerationUsed,
    Refusal,
    RefusalReason,
    SentenceOut,
    Stage,
)
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


def prune_citations(
    sentence: DraftSentence, scores: tuple[float | None, ...], threshold: float | None
) -> tuple[DraftSentence, tuple[float | None, ...]]:
    """Keep only the cited chunks that support the sentence on their own, when any does (D-052).

    Models over-cite: a sentence verified by one chunk often also cites a loosely related one.
    When at least one cited chunk passes the threshold alone, the others are dropped; when only
    the joined or focused premise passed, every cited chunk stays (they support it together).
    """
    if threshold is None or len(sentence.chunk_ids) < 2:
        return sentence, scores
    keep = [i for i, s in enumerate(scores) if s is not None and s >= threshold]
    if not keep or len(keep) == len(sentence.chunk_ids):
        return sentence, scores
    ids = tuple(sentence.chunk_ids[i] for i in keep)
    return replace(sentence, chunk_ids=ids), tuple(scores[i] for i in keep)


def kept_and_removed(
    sentences: list[DraftSentence], checked: Checked, threshold: float | None = None
) -> tuple[list[tuple[DraftSentence, tuple[float | None, ...]]], list[str]]:
    """Passing sentences with their per-chunk scores, and the texts of the failing ones.

    With ``threshold``, sentences the checker passed cite only the chunks that support them on
    their own (when any does); see :func:`prune_citations`.
    """
    kept: list[tuple[DraftSentence, tuple[float | None, ...]]] = []
    removed: list[str] = []
    for sentence, check in zip(sentences, checked.checks, strict=True):
        if check.passed:
            prune = threshold if check.method == "checker" else None
            kept.append(prune_citations(sentence, check.chunk_scores, prune))
        else:
            removed.append(sentence.text)
    return kept, removed


@dataclass(frozen=True)
class Kept:
    """A sentence that passed, with its index among the model's sentences and per-chunk scores."""

    index: int
    sentence: DraftSentence
    scores: tuple[float | None, ...]


@dataclass
class _Changes:
    added: dict[int, tuple[str, ...]] = field(default_factory=dict)
    dropped: dict[int, tuple[str, ...]] = field(default_factory=dict)
    swapped: dict[int, list[tuple[str, str]]] = field(default_factory=dict)

    def out(self, chunks: Mapping[str, RetrievedChunk]) -> list[CitationChange]:
        ids = sorted(set(self.added) | set(self.dropped) | set(self.swapped))
        changes = [
            CitationChange(
                sentence=i,
                added=[chunks[label].chunk_id for label in self.added.get(i, ())],
                dropped=[chunks[label].chunk_id for label in self.dropped.get(i, ())],
                swapped=[
                    [chunks[a].chunk_id, chunks[b].chunk_id] for a, b in self.swapped.get(i, [])
                ],
            )
            for i in ids
        ]
        return [c for c in changes if c.added or c.dropped or c.swapped]


def complete_all(
    sentences: list[DraftSentence], chunks: Mapping[str, RetrievedChunk]
) -> tuple[list[DraftSentence], dict[int, tuple[str, ...]]]:
    """:func:`~app.chat.citation_choice.complete_citations` for every sentence of a reply."""
    completed: list[DraftSentence] = []
    added: dict[int, tuple[str, ...]] = {}
    for i, sentence in enumerate(sentences):
        done, labels = complete_citations(sentence, chunks)
        completed.append(done)
        if labels:
            added[i] = labels
    if added:
        logger.info("Completed the citations of %d sentence(s)", len(added))
    return completed, added


def choose_citations(
    question: str,
    sentences: list[DraftSentence],
    checked: Checked,
    chunks: Mapping[str, RetrievedChunk],
    verifier: Verifier,
    changes: _Changes | None = None,
) -> tuple[list[Kept], list[str]]:
    """The passing sentences with their chosen citations, and the texts of the failing ones.

    Checker-passed sentences get D-052 pruning, the near-copy collapse and the guarded upgrade
    (:mod:`app.chat.citation_choice`); judge-rescued sentences keep their citations, since the
    evidence anchoring already ties them to cited lines.
    """
    log = changes if changes is not None else _Changes()
    kept: list[Kept] = []
    removed: list[str] = []
    for i, (sentence, check) in enumerate(zip(sentences, checked.checks, strict=True)):
        if not check.passed:
            removed.append(sentence.text)
            continue
        if check.method != "checker":
            kept.append(Kept(i, sentence, check.chunk_scores))
            continue
        choice = choose(question, sentence, check.chunk_scores, chunks, verifier.threshold)
        if choice.dropped:
            log.dropped[i] = choice.dropped
        kept.append(Kept(i, choice.sentence, choice.scores))
    if verifier.enabled:
        kept = _upgrade(kept, checked, chunks, verifier, log)
    return kept, removed


def _upgrade(
    kept: list[Kept],
    checked: Checked,
    chunks: Mapping[str, RetrievedChunk],
    verifier: Verifier,
    log: _Changes,
) -> list[Kept]:
    """Swap near-copy citations for the defining chunk when it passes verification alone."""
    pairs = [
        (n, cited, candidate)
        for n, item in enumerate(kept)
        if checked.checks[item.index].method == "checker"
        for cited, candidate in upgrade_candidates(item.sentence, chunks)
    ]
    if not pairs:
        return kept
    results = verifier.check(
        [(kept[n].sentence.text, [chunks[candidate]]) for n, _, candidate in pairs]
    ).checks
    upgraded = list(kept)
    for (n, cited, candidate), result in zip(pairs, results, strict=True):
        if not result.passed or result.problems:
            continue
        item = upgraded[n]
        labels = list(item.sentence.chunk_ids)
        at = labels.index(cited)
        labels[at] = candidate
        scores = list(item.scores)
        scores[at] = result.chunk_scores[0] if result.chunk_scores else None
        upgraded[n] = Kept(
            item.index, replace(item.sentence, chunk_ids=tuple(labels)), tuple(scores)
        )
        log.swapped.setdefault(item.index, []).append((cited, candidate))
        logger.info("Cited the defining chunk instead of a near-copy (sentence %d)", item.index)
    return upgraded


def retrieval_fingerprint(chunks: Sequence[RetrievedChunk]) -> str | None:
    """A short hash of the ordered context chunk ids (None without a context)."""
    if not chunks:
        return None
    joined = "\n".join(chunk.chunk_id for chunk in chunks)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def answer(
    question: str,
    *,
    retrieve: Callable[[str], Retrieval],
    chat_adapter: ChatAdapterFactory,
    refuse_threshold: float,
    verifier: Verifier,
    on_stage: Callable[[Stage], None] = lambda _stage: None,
    generation: Generation | None = None,
) -> Outcome:
    """Answer ``question`` from the space's sources or refuse. Adapter errors propagate.

    ``generation`` overrides the answer call's temperature and seed (eval runs, F5); the seed
    also goes to the judge through the verifier the caller built.
    """
    on_stage("retrieving")
    retrieval = retrieve(question)
    if not retrieval.source_ids:
        return Refused(refusal("no_sources", question))
    top = retrieval.top_score
    fingerprint = retrieval_fingerprint(retrieval.chunks)
    diagnostics = Diagnostics(top_rerank=top, retrieval_fingerprint=fingerprint)
    if top is None or top < refuse_threshold or not retrieval.chunks:
        logger.info("Refused before generating: top score %s < %s", top, refuse_threshold)
        return Refused(refusal("not_in_sources", question), diagnostics)

    on_stage("generating")
    chunks = labelled(retrieval.chunks)
    adapter = chat_adapter()
    temperature = TEMPERATURE
    if generation is not None and generation.temperature is not None:
        temperature = generation.temperature
    seed = generation.seed if generation is not None else None
    used = GenerationUsed(temperature=temperature, seed=seed)
    diagnostics = diagnostics.model_copy(update={"generation": used})

    def generate(regenerate: bool) -> ParsedAnswer:
        return generate_structured(
            adapter,
            answer_messages(question, retrieval.chunks, regenerate=regenerate),
            ANSWER_SCHEMA,
            lambda text: parse_answer(text, chunks.keys()),
            temperature=temperature,
            seed=seed,
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

    sentences, added = complete_all(parsed.sentences, chunks)
    checked = check_sentences(verifier, sentences, chunks)
    regenerated = False
    if checked.refuse:
        # One regeneration whenever the refusal rule would fire: more than half failed, or the
        # first sentence did (D-056). The refusal rule applies again to the second reply.
        logger.info(
            "%d of %d sentence(s) failed verification (first passed: %s); regenerating once",
            checked.failed,
            len(checked.checks),
            checked.checks[0].passed,
        )
        regenerated = True
        on_stage("generating")
        try:
            second = generate(True)
        except InvalidOutputError:
            second = ParsedAnswer([])
        on_stage("checking")
        if not second.sentences:
            # The first reply was refused and the second has nothing usable.
            return Refused(
                refusal("unsupported", question),
                _diagnostics(diagnostics, checked, verifier, True, _Changes(added=added), chunks),
            )
        parsed = second
        sentences, added = complete_all(parsed.sentences, chunks)
        checked = check_sentences(verifier, sentences, chunks)

    changes = _Changes(added=added)
    if checked.refuse:
        logger.info(
            "Refused: %d of %d sentence(s) failed verification (first passed: %s)",
            checked.failed,
            len(checked.checks),
            checked.checks[0].passed if checked.checks else False,
        )
        return Refused(
            refusal("unsupported", question),
            _diagnostics(diagnostics, checked, verifier, regenerated, changes, chunks),
        )

    kept, removed = choose_citations(question, sentences, checked, chunks, verifier, changes)
    diagnostics = _diagnostics(diagnostics, checked, verifier, regenerated, changes, chunks)
    numbered = number_citations([k.sentence for k in kept], chunks)
    scores = [
        {
            chunks[label].chunk_id: score
            for label, score in zip(k.sentence.chunk_ids, k.scores, strict=True)
        }
        for k in kept
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
        numbered,
        list(parsed.dropped),
        badge=badge,
        removed=removed,
        scores=scores,
        diagnostics=diagnostics,
    )


def _diagnostics(
    base: Diagnostics,
    checked: Checked,
    verifier: Verifier,
    regenerated: bool,
    changes: _Changes,
    chunks: Mapping[str, RetrievedChunk],
) -> Diagnostics:
    return base.model_copy(
        update={
            "sentence_scores": [c.diagnostic_score for c in checked.checks],
            "regenerated": regenerated,
            "sentence_methods": checked.methods,
            "judge_vetoed": checked.vetoed,
            "judge_seconds": round(verifier.judge_seconds, 3) if verifier.judge_calls else None,
            "citation_changes": changes.out(chunks),
        }
    )
