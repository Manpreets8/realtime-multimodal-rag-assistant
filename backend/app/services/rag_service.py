import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm import factory as llm_factory
from app.rag import embeddings, reranking
from app.rag.pipeline import RagAnswer, answer_question
from app.schemas.rag import (
    AnswerRequest,
    AnswerResponse,
    CitationOut,
    QuoteOut,
    RetrievalStats,
    Source,
    Usage,
)
from app.services.retrieval_service import ensure_knowledge_bases_owned


async def answer(db: AsyncSession, user_id: uuid.UUID, request: AnswerRequest) -> AnswerResponse:
    settings = get_settings()
    if request.knowledge_base_ids:
        await ensure_knowledge_bases_owned(db, user_id, request.knowledge_base_ids)
    # Resolve the LLM before retrieval so a missing API key fails fast with a clear 503.
    llm = llm_factory.get_llm_provider()
    result = await answer_question(
        db,
        user_id=user_id,
        knowledge_base_ids=request.knowledge_base_ids,
        question=request.question,
        embedder=embeddings.get_embedding_provider(),
        reranker=reranking.get_reranker(),
        llm=llm,
        candidates=settings.top_k,
        rerank_candidates=settings.rerank_candidates,
        top_k=settings.rerank_top_k,
        similarity_threshold=settings.similarity_threshold,
    )
    return to_response(result)


def to_response(result: RagAnswer) -> AnswerResponse:
    sources = [
        Source(
            number=number,
            chunk_id=ranked.chunk.chunk_id,
            document_id=ranked.chunk.document_id,
            knowledge_base_id=ranked.chunk.knowledge_base_id,
            filename=ranked.chunk.filename,
            page_number=ranked.chunk.page_number,
            section=ranked.chunk.section,
            content=ranked.chunk.content,
            rerank_score=ranked.rerank_score,
            similarity=ranked.chunk.similarity,
        )
        for number, ranked in enumerate(result.sources, start=1)
    ]
    citations = [
        CitationOut(
            source_number=citation.source_number,
            document_id=citation.source.chunk.document_id,
            filename=citation.source.chunk.filename,
            page_number=citation.source.chunk.page_number,
            section=citation.source.chunk.section,
            quotes=[QuoteOut(text=q.text, start=q.start, end=q.end) for q in citation.quotes],
            answer_spans=citation.answer_spans,
        )
        for citation in result.citations
    ]
    retrieval = (
        RetrievalStats(
            vector_candidates=result.retrieval.vector_candidates,
            keyword_candidates=result.retrieval.keyword_candidates,
            filtered_out=result.retrieval.filtered_out,
            reranked=result.reranked,
        )
        if result.retrieval
        else None
    )
    return AnswerResponse(
        question=result.question,
        answer=result.answer,
        answer_type=result.answer_type,
        grounded=result.grounded,
        citations=citations,
        sources=sources,
        model=result.model,
        usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        if result.model
        else None,
        truncated=result.truncated,
        retrieval=retrieval,
        timings_ms=result.timings_ms,
    )
