"""The RAG answer pipeline.

    question ─► retrieve (hybrid, RERANK_CANDIDATES) ─► rerank ─► top RERANK_TOP_K
             ─► grounded prompt (citable documents) ─► Claude ─► answer + citations

Answer types:
- `knowledge_base`: the answer cites at least one retrieved source.
- `not_found`: nothing relevant was retrieved (the LLM is not called and no answer is
  invented), or the model answered without citing any source, i.e. it reported that
  the documents don't contain the answer.
- `general`: no knowledge base was selected; answered from general knowledge.
- `image`: answered from attached images only (no knowledge base, nothing relevant retrieved,
  or no document cited).
- `multimodal`: attached images plus cited knowledge-base documents.

Progress (`AnswerEvents`, used by WebSocket chat): stage changes, the sources chosen for
the prompt, then the answer text as Claude generates it.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import CitationSpan, ImagePart, LLMProvider, TextStream
from app.rag.conversation import HistoryMessage, history_as_messages
from app.rag.embeddings import EmbeddingProvider
from app.rag.prompts import (
    GENERAL_SYSTEM_PROMPT,
    GROUNDED_SYSTEM_PROMPT,
    IMAGE_SYSTEM_PROMPT,
    MULTIMODAL_SYSTEM_PROMPT,
    build_general_messages,
    build_grounded_messages,
)
from app.rag.reranking import RankedChunk, Reranker, rerank_or_fallback
from app.rag.retrieval import RetrievalResult, normalize_query, retrieve

logger = logging.getLogger(__name__)

NOT_FOUND_MESSAGE = (
    "I couldn't find information about that in the selected knowledge base. "
    "Try rephrasing the question, or check that the relevant documents have been uploaded and indexed."
)


class Stage(StrEnum):
    REWRITING = "rewriting"  # turning a follow-up into a standalone search query
    RETRIEVING = "retrieving"
    RERANKING = "reranking"
    GENERATING = "generating"


class AnswerEvents(TextStream, Protocol):
    """Progress of one answer. Implementations must not raise for a slow or gone listener."""

    async def stage(self, stage: Stage) -> None: ...

    async def sources(self, sources: list[RankedChunk]) -> None: ...


class AnswerType(StrEnum):
    KNOWLEDGE_BASE = "knowledge_base"
    NOT_FOUND = "not_found"
    GENERAL = "general"
    IMAGE = "image"
    MULTIMODAL = "multimodal"


@dataclass(slots=True)
class Quote:
    text: str
    # Verified offsets of `text` within the source chunk's content, for highlighting.
    # None if the quote could not be located exactly (never a guessed position).
    start: int | None
    end: int | None


@dataclass(slots=True)
class Citation:
    source_number: int  # 1-based position in `RagAnswer.sources`
    source: RankedChunk
    quotes: list[Quote]
    # Character ranges of the answer supported by this source.
    answer_spans: list[tuple[int, int]]


@dataclass(slots=True)
class RagAnswer:
    question: str
    answer: str
    answer_type: AnswerType
    sources: list[RankedChunk]
    citations: list[Citation]
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False
    reranked: bool = False
    retrieval: RetrievalResult | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def grounded(self) -> bool:
        return bool(self.citations)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def locate_quote(content: str, quote: str, start: int | None, end: int | None) -> tuple[int, int] | None:
    """Offsets of `quote` in `content`: the model-reported range if it matches exactly,
    otherwise the first exact occurrence, otherwise None."""
    if start is not None and end is not None and 0 <= start < end <= len(content):
        reported = content[start:end]
        if reported.strip() == quote:
            leading = len(reported) - len(reported.lstrip())
            return start + leading, start + leading + len(quote)
    position = content.find(quote)
    return (position, position + len(quote)) if position >= 0 else None


def map_citations(sources: list[RankedChunk], spans: list[CitationSpan]) -> list[Citation]:
    """Group the model's citations by source, keeping first-cited order and unique quotes.
    Citations pointing at a document index we didn't send are dropped (and logged)."""
    by_index: dict[int, Citation] = {}
    for span in spans:
        if not 0 <= span.document_index < len(sources):
            logger.warning("citation_index_out_of_range", extra={"document_index": span.document_index})
            continue
        citation = by_index.get(span.document_index)
        if citation is None:
            citation = Citation(span.document_index + 1, sources[span.document_index], [], [])
            by_index[span.document_index] = citation
        text = span.cited_text.strip()
        if text and all(existing.text != text for existing in citation.quotes):
            location = locate_quote(citation.source.chunk.content, text, span.source_start, span.source_end)
            if location is None:
                logger.info("citation_quote_not_located", extra={"source_number": citation.source_number})
            start, end = location or (None, None)
            citation.quotes.append(Quote(text, start, end))
        if (span.answer_start, span.answer_end) not in citation.answer_spans:
            citation.answer_spans.append((span.answer_start, span.answer_end))
    return list(by_index.values())


async def answer_question(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    knowledge_base_ids: list[uuid.UUID],
    question: str,
    embedder: EmbeddingProvider,
    reranker: Reranker,
    llm: LLMProvider,
    candidates: int,
    rerank_candidates: int,
    top_k: int,
    similarity_threshold: float,
    history: list[HistoryMessage] | None = None,
    retrieval_query: str | None = None,
    images: list[ImagePart] | None = None,
    events: AnswerEvents | None = None,
) -> RagAnswer:
    """`history` is prior conversation (oldest first) sent before the question; `retrieval_query`
    (e.g. a rewritten follow-up) is used for search and reranking instead of the raw question;
    `images` are the images attached to this question; `events` receives progress."""
    started = time.perf_counter()
    question = normalize_query(question)
    search_query = normalize_query(retrieval_query or question)
    prior = history_as_messages(history or [])
    images = images or []

    async def ungrounded(system: str, answer_type: AnswerType, timings: dict[str, float]) -> RagAnswer:
        """Answer without documents: general knowledge, or the attached images only."""
        stage = time.perf_counter()
        if events:
            await events.stage(Stage.GENERATING)
        response = await llm.generate(
            system=system, messages=prior + build_general_messages(question, images), stream=events
        )
        timings = {**timings, "llm": _ms(stage), "total": _ms(started)}
        return RagAnswer(
            question=question,
            answer=response.text,
            answer_type=answer_type,
            sources=[],
            citations=[],
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            truncated=response.truncated,
            timings_ms=timings,
        )

    if not knowledge_base_ids:
        if images:
            return await ungrounded(IMAGE_SYSTEM_PROMPT, AnswerType.IMAGE, {})
        return await ungrounded(GENERAL_SYSTEM_PROMPT, AnswerType.GENERAL, {})

    stage = time.perf_counter()
    if events:
        await events.stage(Stage.RETRIEVING)
    retrieval = await retrieve(
        db,
        embedder,
        user_id=user_id,
        knowledge_base_ids=knowledge_base_ids,
        query=search_query,
        candidates=candidates,
        limit=rerank_candidates,
        similarity_threshold=similarity_threshold,
    )
    timings = {"retrieval": _ms(stage)}

    if not retrieval.chunks:
        if images:
            # Nothing relevant in the documents, but the question is about the image: answer from it.
            result = await ungrounded(IMAGE_SYSTEM_PROMPT, AnswerType.IMAGE, timings)
            result.retrieval = retrieval
            return result
        timings["total"] = _ms(started)
        logger.info("rag_not_found_no_context", extra={"filtered_out": retrieval.filtered_out, **timings})
        return RagAnswer(
            question=question,
            answer=NOT_FOUND_MESSAGE,
            answer_type=AnswerType.NOT_FOUND,
            sources=[],
            citations=[],
            retrieval=retrieval,
            timings_ms=timings,
        )

    stage = time.perf_counter()
    if events:
        await events.stage(Stage.RERANKING)
    sources, reranked = await rerank_or_fallback(reranker, search_query, retrieval.chunks, top_k)
    timings["rerank"] = _ms(stage)

    stage = time.perf_counter()
    if events:
        await events.sources(sources)
        await events.stage(Stage.GENERATING)
    response = await llm.generate(
        system=MULTIMODAL_SYSTEM_PROMPT if images else GROUNDED_SYSTEM_PROMPT,
        messages=prior + build_grounded_messages(question, sources, images),
        stream=events,
    )
    timings["llm"] = _ms(stage)

    citations = map_citations(sources, response.citations)
    if images:
        answer_type = AnswerType.MULTIMODAL if citations else AnswerType.IMAGE
    else:
        answer_type = AnswerType.KNOWLEDGE_BASE if citations else AnswerType.NOT_FOUND
    timings["total"] = _ms(started)
    logger.info(
        "rag_completed",
        extra={
            "answer_type": answer_type.value,
            "sources": len(sources),
            "cited_sources": len(citations),
            "images": len(images),
            "reranked": reranked,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "truncated": response.truncated,
            **{f"{name}_ms": value for name, value in timings.items()},
        },
    )
    return RagAnswer(
        question=question,
        answer=response.text,
        answer_type=answer_type,
        sources=sources,
        citations=citations,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        truncated=response.truncated,
        reranked=reranked,
        retrieval=retrieval,
        timings_ms=timings,
    )
