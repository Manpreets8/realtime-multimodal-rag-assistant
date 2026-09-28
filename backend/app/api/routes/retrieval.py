from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.retrieval import SearchRequest, SearchResponse
from app.services import retrieval_service

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Hybrid (vector + keyword) search over your knowledge bases",
    responses={404: {"description": "A knowledge base does not exist or is owned by another user"}},
)
async def search(request: SearchRequest, db: DbSession, current_user: CurrentUser) -> SearchResponse:
    """Returns the chunks the RAG pipeline would use as context, with per-retriever
    scores and ranks and per-stage timings, for inspecting and debugging retrieval."""
    return await retrieval_service.search_request(db, current_user.id, request)
