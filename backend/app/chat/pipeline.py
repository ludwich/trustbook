"""The grounded answer pipeline (grounding layers 2 to 5, SPEC §8).

1. Retrieve from the enabled, ready sources. None → refuse (``no_sources``).
2. Layer 2: top reranker score below ``refuse_threshold`` → refuse (``not_in_sources``) before
   any model call.
3. Layer 3: numbered chunks, forced-citation JSON, one retry on unusable JSON, sentences without
   valid chunk ids and parts that are only JSON/Markdown syntax dropped, repeated sentences
   merged into their first occurrence (D-117). No sentences returned → ``not_in_sources``;
   sentences returned but none valid, or unusable JSON twice → ``invalid_output``. The model's
   citations are checked as given (never completed, D-064).
4. Layers 4 and 5 (:mod:`app.grounding.verify`): every sentence is checked against its cited
   chunks. When the refusal rule would fire (more than half failing, or the first sentence
   failing), one regeneration with the same chunks plus
   :data:`~app.chat.prompt.REGENERATE_INSTRUCTION` and the list of the first reply's failed
   sentences with their reasons (:func:`failed_sentences`, D-074) is checked again (D-063); its
   citations are verified as given. Then the refusal
   rule: more than half failing, or the first sentence failing → refuse (``unsupported``).
   Otherwise the failing sentences are removed (``partially_verified``) or none fail
   (``verified``). With the grounding checker off, layer 4 is skipped and the badge is
   ``unverified``. The judge rescues sentences about code the checker failed and confirms checker
   passes (D-045, D-063, D-064); ``diagnostics.sentence_methods`` records which passed them and
   ``diagnostics.failed_sentences`` the failed sentences and why (D-075).
5. Citations of passing sentences are chosen (:mod:`app.chat.citation_choice`, D-052 and its
   extension): supporting chunks only, near-copies collapsed, the defining chunk preferred.

Citation numbers are given after removal, in order of first use among the kept sentences.

Parent context (D-077, D-079): the retrieval's promoted chunks are ordinary numbered chunks; its
code outline goes into the prompt after the chunks and nowhere else. No check reads it: it is not
a chunk, so nothing only the outline shows can verify. Transcript windows (D-091) go after the
outline, the same way: citations always carry the cited child's own times. The diagnostics count
such failures (``outline_only``, ``window_only``) and record the time, judge calls and answer
calls of each step (D-080).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import combinations

from app.chat.citation_choice import choose, upgrade_candidates
from app.chat.citations import number_citations
from app.chat.llm import AnswerCall, ChatAdapterFactory, generate_structured
from app.chat.output import DraftSentence, InvalidOutputError, ParsedAnswer, parse_answer
from app.chat.prompt import (
    ANSWER_SCHEMA,
    TEMPERATURE,
    FailedSentence,
    answer_messages,
    labelled,
)
from app.chat.schemas import (
    Badge,
    CitationChange,
    Diagnostics,
    ExpansionOut,
    FailedSentenceOut,
    Generation,
    GenerationUsed,
    Refusal,
    RefusalReason,
    SentenceOut,
    Stage,
    Timings,
    WindowOut,
)
from app.chat.text import content_terms, question_topic
from app.grounding.code_checks import name_in, unescape_markdown, without_location
from app.grounding.verify import Checked, Verifier
from app.retrieval.parents import (
    WINDOW_PLACEHOLDER,
    outline_fingerprint,
    outline_text,
    window_fingerprint,
    window_text,
)
from app.retrieval.service import Retrieval, RetrievedChunk

logger = logging.getLogger("trustbook.chat")

NOT_IN_SOURCES = "Not in your sources"
NO_CITED_ANSWER = "No answer with citations"
NOT_VERIFIED = "Couldn't verify an answer"


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
# ``unsupported`` (D-118, G3-2): an answer was drafted from the sources, but verification (layers
# 4 and 5) did not confirm enough of it, so nothing is shown. Not "add sources": they may hold it.
UNSUPPORTED_SUGGESTION = (
    "An answer was drafted from your sources, but Trustbook could not verify it against the "
    "passages it cited, so it is not shown. Try rephrasing the question, or name the file or "
    "function you mean."
)


def suggestion_for(question: str) -> str:
    """``Add sources that cover “…”, then ask again.`` built from the question's own words."""
    topic = question_topic(question)
    if not content_terms(topic):  # nothing meaningful left ("What is it?")
        return GENERIC_SUGGESTION
    return f"Add sources that cover “{topic}”, then ask again."


def refusal(reason: RefusalReason, question: str) -> Refusal:
    """The plain-English refusal for ``reason``.

    ``message`` is only the title the UI shows ("Not in your sources"); ``suggestion`` is the
    subtitle, so the two never repeat each other. A verification refusal (``unsupported``) has
    its own title and suggestion (D-118): the sources were not found lacking, the answer drafted
    from them could not be verified.
    """
    if reason == "no_sources":
        return Refusal(reason=reason, message=NOT_IN_SOURCES, suggestion=NO_SOURCES_SUGGESTION)
    if reason == "unsupported":
        return Refusal(reason=reason, message=NOT_VERIFIED, suggestion=UNSUPPORTED_SUGGESTION)
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
    dropped: dict[int, tuple[str, ...]] = field(default_factory=dict)
    swapped: dict[int, list[tuple[str, str]]] = field(default_factory=dict)

    def out(self, chunks: Mapping[str, RetrievedChunk]) -> list[CitationChange]:
        ids = sorted(set(self.dropped) | set(self.swapped))
        changes = [
            CitationChange(
                sentence=i,
                dropped=[chunks[label].chunk_id for label in self.dropped.get(i, ())],
                swapped=[
                    [chunks[a].chunk_id, chunks[b].chunk_id] for a, b in self.swapped.get(i, [])
                ],
            )
            for i in ids
        ]
        return [c for c in changes if c.dropped or c.swapped]


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


def _plain(chunk: RetrievedChunk) -> str:
    return unescape_markdown(without_location(chunk.text))


def _holds(name: str, chunk: RetrievedChunk) -> bool:
    return name_in(name, [_plain(chunk)])


def joint_cover(
    name: str, cited: Sequence[str], chunks: Mapping[str, RetrievedChunk]
) -> tuple[str, ...]:
    """The fewest other context chunks (one, else two; the first in label order) that, cited
    together with ``cited``, hold ``name`` by layer 5's own test (:func:`name_in` over all their
    texts: ``requests.codes.too_many`` passes on a docstring with ``requests.codes`` plus the
    table with ``too_many``); ``()`` when none do (D-120). For the feedback only."""
    base = [_plain(chunks[label]) for label in cited if label in chunks]
    if name_in(name, base):
        return ()  # the cited chunks already hold it: nothing to add
    others = [label for label in chunks if label not in cited]
    for size in (1, 2):
        for group in combinations(others, size):
            if name_in(name, [*base, *(_plain(chunks[label]) for label in group)]):
                return group
    return ()


def failed_sentences(
    sentences: Sequence[DraftSentence],
    checked: Checked,
    chunks: Mapping[str, RetrievedChunk],
) -> list[FailedSentence]:
    """The failed sentences of a reply for the regeneration feedback (D-074): for each name or
    literal no cited chunk holds, the other context chunks that hold it (layer 5's test), or,
    when none holds it alone, the chunks that hold it together with the cited ones (D-120).
    A name the cited chunks do hold (the code judge's ``absent`` names: its evidence lines lacked
    it) is left out, so the feedback never asks for a citation that cannot help."""
    failed: list[FailedSentence] = []
    for sentence, check in zip(sentences, checked.checks, strict=True):
        if check.passed:
            continue
        missing: list[tuple[str, tuple[str, ...]]] = []
        combined: list[str] = []
        cited = [_plain(chunks[label]) for label in sentence.chunk_ids if label in chunks]
        for name in check.missing:
            if name_in(name, cited):
                continue
            places = tuple(
                label
                for label, chunk in chunks.items()
                if label not in sentence.chunk_ids and _holds(name, chunk)
            )
            if not places:
                places = joint_cover(name, sentence.chunk_ids, chunks)
                if places:
                    combined.append(name)
            missing.append((name, places))
        failed.append(
            FailedSentence(
                sentence.text, tuple(sentence.chunk_ids), tuple(missing), tuple(combined)
            )
        )
    return failed


FAILED_TEXT_CHARS = 300  # diagnostics keep at most this much of a failed sentence and its reason


def _cap(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= FAILED_TEXT_CHARS else flat[: FAILED_TEXT_CHARS - 1] + "…"


def failed_sentences_out(
    sentences: Sequence[DraftSentence], checked: Checked, threshold: float
) -> list[FailedSentenceOut]:
    """``diagnostics.failed_sentences``: each failed sentence's text and why it failed (D-075)."""
    return [
        FailedSentenceOut(text=_cap(sentence.text), reason=_cap(check.failure(threshold)))
        for sentence, check in zip(sentences, checked.checks, strict=True)
        if not check.passed
    ]


def cleanup_diagnostics(parsed: ParsedAnswer) -> dict[str, object]:
    """Layer 3's clean-up of a reply (D-117): the syntax fragments it dropped and how many
    repeated sentences it merged."""
    if parsed.fragments or parsed.duplicates:
        logger.info(
            "Dropped %d syntax fragment(s), merged %d repeated sentence(s)",
            len(parsed.fragments),
            parsed.duplicates,
        )
    return {
        "syntax_fragments": [_cap(f) for f in parsed.fragments],
        "duplicate_sentences": parsed.duplicates,
    }


def retrieval_fingerprint(chunks: Sequence[RetrievedChunk]) -> str | None:
    """A short hash of the ordered context chunk ids (None without a context)."""
    if not chunks:
        return None
    joined = "\n".join(chunk.chunk_id for chunk in chunks)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def noncitable_only(
    sentences: Sequence[DraftSentence],
    checked: Checked,
    chunks: Mapping[str, RetrievedChunk],
    text: str,
    *,
    words: bool = False,
) -> int:
    """Diagnostics only (D-077, D-091): failed sentences missing a name or literal that the
    non-citable ``text`` shows and no labelled chunk holds. With ``words`` (transcript windows,
    whose prose layer 5 does not check), also failed sentences using a content word that ``text``
    has and no labelled chunk has. Never used to decide a sentence."""
    if not text:
        return 0
    held: set[str] = set()
    shown: set[str] = set()
    if words:
        for chunk in chunks.values():
            held.update(content_terms(unescape_markdown(chunk.text)))
        shown = set(content_terms(text)) - held
    count = 0
    for i, check in enumerate(checked.checks):
        if check.passed:
            continue
        if any(
            name_in(name, [text]) and not any(_holds(name, c) for c in chunks.values())
            for name in check.missing
        ) or (words and i < len(sentences) and shown & set(content_terms(sentences[i].text))):
            count += 1
    return count


def outline_only(
    sentences: Sequence[DraftSentence],
    checked: Checked,
    chunks: Mapping[str, RetrievedChunk],
    outline: str,
) -> int:
    """Failed sentences missing a name or literal only the outline shows (D-077)."""
    return noncitable_only(sentences, checked, chunks, outline)


def window_only(
    sentences: Sequence[DraftSentence],
    checked: Checked,
    chunks: Mapping[str, RetrievedChunk],
    windows: str,
) -> int:
    """Failed sentences stating something only a transcript window shows (D-091): a missing
    name, or a content word no labelled chunk has. ``windows``: the windows' text without the
    numbered chunks' placeholders."""
    return noncitable_only(sentences, checked, chunks, windows, words=True)


def _round(seconds: float) -> float:
    return round(seconds, 3)


@dataclass
class _Meter:
    """What one answer spent: time, answer calls and judge calls per step (D-080)."""

    started: float = field(default_factory=time.perf_counter)
    retrieval: Retrieval | None = None
    retrieval_s: float | None = None
    calls: list[AnswerCall] = field(default_factory=list)
    verification_s: list[float] = field(default_factory=list)
    judge_per_pass: list[int] = field(default_factory=list)
    outline_only: list[int] = field(default_factory=list)
    window_only: list[int] = field(default_factory=list)
    citation_s: float | None = None
    promoted_cited: tuple[int, int] = (0, 0)

    def check(
        self,
        verifier: Verifier,
        sentences: list[DraftSentence],
        chunks: Mapping[str, RetrievedChunk],
    ) -> Checked:
        """One verification pass (:func:`check_sentences`), timed and counted."""
        started, judged = time.perf_counter(), verifier.judge_calls
        checked = check_sentences(verifier, sentences, chunks)
        self.verification_s.append(_round(time.perf_counter() - started))
        self.judge_per_pass.append(verifier.judge_calls - judged)
        retrieval = self.retrieval
        outline = outline_text(retrieval.outline) if retrieval is not None else ""
        self.outline_only.append(outline_only(sentences, checked, chunks, outline))
        windows = ""
        if retrieval is not None and retrieval.transcript_windows:
            windows = window_text(retrieval.transcript_windows).replace(WINDOW_PLACEHOLDER, "")
        self.window_only.append(window_only(sentences, checked, chunks, windows))
        return checked

    def update(self, verifier: Verifier) -> dict[str, object]:
        update: dict[str, object] = {
            "judge_calls": verifier.judge_calls,
            "judge_calls_per_pass": list(self.judge_per_pass),
            "answer_calls": len(self.calls),
            "timings": Timings(
                retrieval_s=self.retrieval_s,
                generation_s=[_round(c.seconds) for c in self.calls],
                verification_s=list(self.verification_s),
                citation_s=self.citation_s,
                total_s=_round(time.perf_counter() - self.started),
            ),
            "prompt_tokens": [c.prompt_tokens for c in self.calls],
            "completion_tokens": [c.completion_tokens for c in self.calls],
            "outline_only": list(self.outline_only),
            "window_only": list(self.window_only),
            "promoted_cited": list(self.promoted_cited),
        }
        retrieval = self.retrieval
        if retrieval is not None:
            promoted = retrieval.promoted
            update["expansions"] = [
                ExpansionOut(chunk_id=c.chunk_id, kind=c.expansion or "", referrer=c.referrer)
                for c in promoted
            ]
            update["promoted_tokens"] = sum(max(0, c.token_count) for c in promoted)
            update["outline_tokens"] = sum(b.tokens for b in retrieval.outline)
            update["outline_fingerprint"] = outline_fingerprint(retrieval.outline)
            windows = retrieval.transcript_windows
            update["windows"] = [
                WindowOut(
                    source_id=w.source_id,
                    numbered=list(w.numbered_ids),
                    chunk_ids=list(w.chunk_ids),
                    tokens=w.tokens,
                )
                for w in windows
            ]
            update["window_tokens"] = sum(w.tokens for w in windows)
            update["window_fingerprint"] = window_fingerprint(windows)
        return update


def promoted_cited(kept: Sequence[Kept], chunks: Mapping[str, RetrievedChunk]) -> tuple[int, int]:
    """(kept sentences citing a promoted chunk, those of them also citing another chunk)."""
    citing = [k for k in kept if any(chunks[i].expansion is not None for i in k.sentence.chunk_ids)]
    return len(citing), sum(1 for k in citing if len(set(k.sentence.chunk_ids)) > 1)


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
    also goes to the judge through the verifier the caller built. Every outcome's diagnostics
    carry the time, answer calls and judge calls it took (D-080).
    """
    meter = _Meter()
    outcome = _answer(
        question,
        meter,
        retrieve=retrieve,
        chat_adapter=chat_adapter,
        refuse_threshold=refuse_threshold,
        verifier=verifier,
        on_stage=on_stage,
        generation=generation,
    )
    diagnostics = outcome.diagnostics.model_copy(update=meter.update(verifier))
    return replace(outcome, diagnostics=diagnostics)


def _answer(
    question: str,
    meter: _Meter,
    *,
    retrieve: Callable[[str], Retrieval],
    chat_adapter: ChatAdapterFactory,
    refuse_threshold: float,
    verifier: Verifier,
    on_stage: Callable[[Stage], None],
    generation: Generation | None,
) -> Outcome:
    on_stage("retrieving")
    started = time.perf_counter()
    retrieval = retrieve(question)
    meter.retrieval, meter.retrieval_s = retrieval, _round(time.perf_counter() - started)
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

    def generate(regenerate: bool, failed: Sequence[FailedSentence] = ()) -> ParsedAnswer:
        return generate_structured(
            adapter,
            answer_messages(
                question,
                retrieval.chunks,
                outline=retrieval.outline,
                windows=retrieval.transcript_windows,
                regenerate=regenerate,
                failed=failed,
            ),
            ANSWER_SCHEMA,
            lambda text: parse_answer(text, chunks.keys()),
            temperature=temperature,
            seed=seed,
            calls=meter.calls,
        )

    try:
        parsed = generate(False)
    except InvalidOutputError:
        logger.info("Refused: the chat reply was unusable twice")
        return Refused(refusal("invalid_output", question), diagnostics)
    diagnostics = diagnostics.model_copy(update=cleanup_diagnostics(parsed))

    on_stage("checking")
    if not parsed.sentences:
        if parsed.dropped:
            logger.info("Refused: all %d sentence(s) lacked valid citations", len(parsed.dropped))
            return Refused(refusal("invalid_output", question), diagnostics)
        return Refused(refusal("not_in_sources", question), diagnostics)
    if parsed.dropped:
        logger.info("Dropped %d sentence(s) without valid citations", len(parsed.dropped))

    sentences = parsed.sentences
    checked = meter.check(verifier, sentences, chunks)
    regenerated = False
    if checked.refuse:
        # One regeneration whenever the refusal rule would fire: more than half failed, or the
        # first sentence did (D-063). The refusal rule applies again to the second reply.
        logger.info(
            "%d of %d sentence(s) failed verification (first passed: %s); regenerating once",
            checked.failed,
            len(checked.checks),
            checked.checks[0].passed,
        )
        regenerated = True
        on_stage("generating")
        try:
            second = generate(True, failed_sentences(sentences, checked, chunks))
        except InvalidOutputError:
            second = ParsedAnswer([])
        on_stage("checking")
        if not second.sentences:
            # The first reply was refused and the second has nothing usable.
            return Refused(
                refusal("unsupported", question),
                _diagnostics(diagnostics, checked, verifier, True, _Changes(), chunks, sentences),
            )
        parsed = second
        diagnostics = diagnostics.model_copy(update=cleanup_diagnostics(parsed))
        sentences = parsed.sentences
        checked = meter.check(verifier, sentences, chunks)

    changes = _Changes()
    if checked.refuse:
        logger.info(
            "Refused: %d of %d sentence(s) failed verification (first passed: %s)",
            checked.failed,
            len(checked.checks),
            checked.checks[0].passed if checked.checks else False,
        )
        return Refused(
            refusal("unsupported", question),
            _diagnostics(diagnostics, checked, verifier, regenerated, changes, chunks, sentences),
        )

    started = time.perf_counter()
    kept, removed = choose_citations(question, sentences, checked, chunks, verifier, changes)
    meter.citation_s = _round(time.perf_counter() - started)
    meter.promoted_cited = promoted_cited(kept, chunks)
    diagnostics = _diagnostics(
        diagnostics, checked, verifier, regenerated, changes, chunks, sentences
    )
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
    sentences: Sequence[DraftSentence],
) -> Diagnostics:
    return base.model_copy(
        update={
            "sentence_scores": [c.diagnostic_score for c in checked.checks],
            "regenerated": regenerated,
            "sentence_methods": checked.methods,
            "judge_vetoed": checked.vetoed,
            "judge_seconds": round(verifier.judge_seconds, 3) if verifier.judge_calls else None,
            "citation_changes": changes.out(chunks),
            "failed_sentences": failed_sentences_out(sentences, checked, verifier.threshold),
        }
    )
