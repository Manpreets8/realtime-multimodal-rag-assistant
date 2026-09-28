import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import ChatLimit, CurrentUser, DbSession, Storage
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationSummary,
    ConversationUpdate,
)
from app.services import chat_service

chat_router = APIRouter(prefix="/chat", tags=["chat"])
conversations_router = APIRouter(prefix="/conversations", tags=["chat"])

_NOT_FOUND = {404: {"description": "Conversation not found (or owned by another user)"}}


@chat_router.post(
    "",
    response_model=ChatResponse,
    summary="Send a message (starts a conversation when conversation_id is omitted)",
    responses={
        **_NOT_FOUND,
        422: {"description": "Invalid request, or the model declined to answer"},
        502: {"description": "The AI model returned an error (nothing was saved)"},
        503: {"description": "The AI model is not configured or unavailable (nothing was saved)"},
        504: {"description": "The AI model timed out (nothing was saved)"},
        429: {"description": "Rate limit exceeded (see Retry-After)"},
    },
)
async def send_message(
    request: ChatRequest, db: DbSession, storage: Storage, current_user: CurrentUser, _: ChatLimit
) -> ChatResponse:
    """Answers with the conversation's knowledge base (RAG with citations) or, with none
    selected, as a general assistant. Follow-up questions use the conversation history.
    The question and answer are saved together only if answering succeeds."""
    return await chat_service.send_message(db, storage, current_user.id, request)


@conversations_router.get(
    "", response_model=list[ConversationSummary], summary="Your conversations, most recent first"
)
async def list_conversations(
    db: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationSummary]:
    return await chat_service.list_conversations(db, current_user.id, limit=limit, offset=offset)


@conversations_router.get("/{conversation_id}", response_model=ConversationDetail, responses=_NOT_FOUND)
async def get_conversation(
    conversation_id: uuid.UUID, db: DbSession, current_user: CurrentUser
) -> ConversationDetail:
    return await chat_service.get_conversation(db, current_user.id, conversation_id)


@conversations_router.patch(
    "/{conversation_id}",
    response_model=ConversationSummary,
    responses=_NOT_FOUND,
    summary="Rename a conversation or change its knowledge base",
)
async def update_conversation(
    conversation_id: uuid.UUID, data: ConversationUpdate, db: DbSession, current_user: CurrentUser
) -> ConversationSummary:
    return await chat_service.update_conversation(db, current_user.id, conversation_id, data)


@conversations_router.delete(
    "/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND
)
async def delete_conversation(
    conversation_id: uuid.UUID, db: DbSession, storage: Storage, current_user: CurrentUser
) -> Response:
    await chat_service.delete_conversation(db, storage, current_user.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
