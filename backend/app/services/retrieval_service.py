import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import KnowledgeBase
from app.rag import embeddings
from app.rag.retrieval import FusionMethod, RetrievalFilters, RetrievalResult, SearchMode, retrieve
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
    await ensure_knowledge_bases_owned(db, user_id, knowledge_base_ids)
    options = options or SearchOptions()
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
    )
    result = await retrieve(
        db,
        embeddings.get_embedding_provider(),
        user_id=user_id,
        knowledge_base_ids=knowledge_base_ids,
        query=query,
        candidates=parameters.candidates,
        limit=parameters.limit,
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
    return SearchResponse(
        query=result.query,
        mode=result.mode,
        results=[SearchHit.model_validate(chunk) for chunk in result.chunks],
        vector_candidates=result.vector_candidates,
        keyword_candidates=result.keyword_candidates,
        filtered_out=result.filtered_out,
        duplicates_removed=result.duplicates_removed,
        filter_documents=result.filter_documents,
        similarity_threshold=parameters.similarity_threshold,
        parameters=parameters,
        timings_ms=result.timings_ms,
    )
