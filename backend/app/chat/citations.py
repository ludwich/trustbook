"""Turn validated sentences into numbered citations (per answer, in order of first use)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from app.chat.output import DraftSentence
from app.chat.schemas import CitationOut, SentenceOut, SourceUsed
from app.retrieval.service import RetrievedChunk


def number_citations(
    sentences: Sequence[DraftSentence], chunks: Mapping[str, RetrievedChunk]
) -> list[SentenceOut]:
    """Map prompt labels (``c3``) to real chunks and number them 1, 2, … by first use.

    Every label must be in ``chunks`` (the validator guarantees it); an unknown label raises
    ``KeyError`` rather than producing a citation to nothing.
    """
    numbers: dict[str, int] = {}
    out: list[SentenceOut] = []
    for sentence in sentences:
        citations: list[CitationOut] = []
        for chunk_label in dict.fromkeys(sentence.chunk_ids):
            chunk = chunks[chunk_label]
            n = numbers.setdefault(chunk.chunk_id, len(numbers) + 1)
            if any(c.n == n for c in citations):
                continue
            citations.append(
                CitationOut(
                    n=n,
                    chunk_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    source_title=chunk.source_title,
                    locator=dict(chunk.locator),
                )
            )
        out.append(SentenceOut(text=sentence.text, citations=citations))
    return out


def sources_used(sentences: Iterable[SentenceOut]) -> list[SourceUsed]:
    """The distinct sources cited, in order of first citation."""
    seen: dict[str, SourceUsed] = {}
    for sentence in sentences:
        for citation in sentence.citations:
            if citation.source_id not in seen:
                seen[citation.source_id] = SourceUsed(
                    source_id=citation.source_id, title=citation.source_title
                )
    return list(seen.values())
