import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import KnowledgeBase
from app.rag import embeddings
from app.rag.retrieval import RetrievalResult, SearchMode, retrieve
from app.schemas.retrieval import SearchHit, SearchRequest, SearchResponse


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
) -> RetrievalResult:
    settings = get_settings()
    await ensure_knowledge_bases_owned(db, user_id, knowledge_base_ids)
    return await retrieve(
        db,
        embeddings.get_embedding_provider(),
        user_id=user_id,
        knowledge_base_ids=knowledge_base_ids,
        query=query,
        candidates=settings.top_k,
        limit=limit or settings.rerank_top_k,
        similarity_threshold=settings.similarity_threshold,
        mode=mode,
    )


async def search_request(db: AsyncSession, user_id: uuid.UUID, request: SearchRequest) -> SearchResponse:
    result = await search(
        db, user_id, request.knowledge_base_ids, request.query, mode=request.mode, limit=request.limit
    )
    return SearchResponse(
        query=result.query,
        mode=result.mode,
        results=[SearchHit.model_validate(chunk) for chunk in result.chunks],
        vector_candidates=result.vector_candidates,
        keyword_candidates=result.keyword_candidates,
        filtered_out=result.filtered_out,
        similarity_threshold=get_settings().similarity_threshold,
        timings_ms=result.timings_ms,
    )
