import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import KnowledgeBase
from app.rag import embeddings, reranking
from app.rag.retrieval import FusionMethod, RetrievalFilters, RetrievalResult, SearchMode, retrieve, topic_of
from app.schemas.retrieval import SearchHit, SearchOptions, SearchParameters, SearchRequest, SearchResponse


async def ensure_knowledge_bases_owned(
    db: AsyncSession, user_id: uuid.UUID, knowledge_base_ids: list[uuid.UUID]
) -> None:
    owned = set(
        await db.scalars(
            select(KnowledgeBase.id).where(
                KnowledgeBase.id.in_(knowledge_base_ids), KnowledgeBase.user_id == user_id
            )
        )
    )
    if owned != set(knowledge_base_ids):
        # Same response whether the KB doesn't exist or belongs to someone else.
        raise NotFoundError("One or more knowledge bases were not found.")


async def search(
    db: AsyncSession,
    user_id: uuid.UUID,
    knowledge_base_ids: list[uuid.UUID],
    query: str,
    *,
    mode: SearchMode = SearchMode.HYBRID,
    limit: int | None = None,
    filters: RetrievalFilters | None = None,
    options: SearchOptions | None = None,
) -> tuple[RetrievalResult, SearchParameters]:
    settings = get_settings()
    if knowledge_base_ids:
        await ensure_knowledge_bases_owned(db, user_id, knowledge_base_ids)
    else:  # all of the user's knowledge bases
        knowledge_base_ids = list(
            await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.user_id == user_id))
        )
    options = options or SearchOptions()
    if options.topic:
        query = topic_of(query)
    parameters = SearchParameters(
        candidates=options.candidates or settings.top_k,
        limit=limit or settings.rerank_top_k,
        similarity_threshold=(
            options.similarity_threshold
            if options.similarity_threshold is not None
            else settings.similarity_threshold
        ),
        fusion=options.fusion or FusionMethod(settings.retrieval_fusion),
        alpha=options.alpha if options.alpha is not None else settings.hybrid_alpha,
        dedup_threshold=settings.dedup_threshold,
        rerank=options.rerank,
    )
    result = await retrieve(
        db,
        embeddings.get_embedding_provider(),
        user_id=user_id,
        knowledge_base_ids=knowledge_base_ids,
        query=query,
        candidates=parameters.candidates,
        # Reranking scores a larger pool (as answers do) and keeps the best `limit`.
        limit=max(settings.rerank_candidates, parameters.limit) if parameters.rerank else parameters.limit,
        similarity_threshold=parameters.similarity_threshold,
        mode=mode,
        filters=filters,
        dedup_threshold=parameters.dedup_threshold,
        fusion=parameters.fusion,
        alpha=parameters.alpha,
    )
    return result, parameters


async def search_request(db: AsyncSession, user_id: uuid.UUID, request: SearchRequest) -> SearchResponse:
    result, parameters = await search(
        db,
        user_id,
        request.knowledge_base_ids,
        request.query,
        mode=request.mode,
        limit=request.limit,
        filters=request.filters.to_filters() if request.filters else None,
        options=request.options,
    )
    hits = [SearchHit.model_validate(chunk) for chunk in result.chunks]
    reranker_name: str | None = None
    timings = dict(result.timings_ms)
    if parameters.rerank and result.chunks:
        started = time.perf_counter()
        reranker = reranking.get_reranker()
        ranked, applied = await reranking.rerank_or_fallback(
            reranker, result.query, result.chunks, parameters.limit
        )
        timings["rerank"] = round((time.perf_counter() - started) * 1000, 2)
        # Re-inserted so "total" stays last, after the stage it now includes.
        timings["total"] = round(timings.pop("total", 0.0) + timings["rerank"], 2)
        reranker_name = reranker.model_name if applied else None
        hits = [
            SearchHit.model_validate(item.chunk).model_copy(
                update={
                    "rerank_score": item.rerank_score,
                    "relevance": reranking.relevance_label(reranker.model_name, item.rerank_score)
                    if applied
                    else None,
                }
            )
            for item in ranked
        ]
    else:
        hits = hits[: parameters.limit]
    return SearchResponse(
        query=result.query,
        mode=result.mode,
        results=hits,
        reranker=reranker_name,
        vector_candidates=result.vector_candidates,
        keyword_candidates=result.keyword_candidates,
        filtered_out=result.filtered_out,
        duplicates_removed=result.duplicates_removed,
        filter_documents=result.filter_documents,
        similarity_threshold=parameters.similarity_threshold,
        parameters=parameters,
        timings_ms=timings,
    )
