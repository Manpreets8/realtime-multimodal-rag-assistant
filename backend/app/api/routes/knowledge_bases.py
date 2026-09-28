import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession, Storage
from app.schemas.document import DocumentRead
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseRead, KnowledgeBaseUpdate
from app.services import document_service, knowledge_base_service

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge bases"])

_NOT_FOUND = {404: {"description": "Knowledge base not found (or owned by another user)"}}
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.post(
    "",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Name already used"}},
)
async def create_knowledge_base(
    data: KnowledgeBaseCreate, db: DbSession, current_user: CurrentUser
) -> KnowledgeBaseRead:
    return await knowledge_base_service.create(db, current_user.id, data)


@router.get("", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(
    db: DbSession, current_user: CurrentUser, limit: Limit = 100, offset: Offset = 0
) -> list[KnowledgeBaseRead]:
    return await knowledge_base_service.list_for_user(db, current_user.id, limit=limit, offset=offset)


@router.get("/{kb_id}", response_model=KnowledgeBaseRead, responses=_NOT_FOUND)
async def get_knowledge_base(kb_id: uuid.UUID, db: DbSession, current_user: CurrentUser) -> KnowledgeBaseRead:
    return await knowledge_base_service.get(db, current_user.id, kb_id)


@router.patch("/{kb_id}", response_model=KnowledgeBaseRead, responses=_NOT_FOUND)
async def update_knowledge_base(
    kb_id: uuid.UUID, data: KnowledgeBaseUpdate, db: DbSession, current_user: CurrentUser
) -> KnowledgeBaseRead:
    return await knowledge_base_service.update(db, current_user.id, kb_id, data)


@router.delete(
    "/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    summary="Delete a knowledge base and all of its documents",
)
async def delete_knowledge_base(
    kb_id: uuid.UUID, db: DbSession, storage: Storage, current_user: CurrentUser
) -> Response:
    await knowledge_base_service.delete(db, storage, current_user.id, kb_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{kb_id}/documents", response_model=list[DocumentRead], responses=_NOT_FOUND)
async def list_documents(
    kb_id: uuid.UUID, db: DbSession, current_user: CurrentUser, limit: Limit = 100, offset: Offset = 0
) -> list[DocumentRead]:
    documents = await document_service.list_for_knowledge_base(
        db, current_user.id, kb_id, limit=limit, offset=offset
    )
    return await document_service.with_progress(documents)
