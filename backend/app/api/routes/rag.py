from fastapi import APIRouter

from app.api.deps import ChatLimit, CurrentUser, DbSession
from app.schemas.rag import AnswerRequest, AnswerResponse
from app.services import rag_service

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post(
    "/answer",
    response_model=AnswerResponse,
    summary="Answer a question from your knowledge bases, with citations",
    responses={
        404: {"description": "A knowledge base does not exist or is owned by another user"},
        422: {"description": "Invalid request, or the model declined to answer"},
        502: {"description": "The AI model returned an error"},
        503: {"description": "The AI model is not configured or temporarily unavailable"},
        504: {"description": "The AI model timed out"},
    },
)
async def answer(
    request: AnswerRequest, db: DbSession, current_user: CurrentUser, _: ChatLimit
) -> AnswerResponse:
    """Retrieves, reranks and asks the LLM to answer using only the retrieved sources.

    - `answer_type=knowledge_base`: grounded answer; `citations` map to `sources`.
    - `answer_type=not_found`: nothing relevant was found (no LLM call), or the model
      reported that the sources don't contain the answer.
    - `answer_type=general`: no knowledge base selected; general-knowledge answer.
    """
    return await rag_service.answer(db, current_user.id, request)
