import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.document import ChunkContext
from app.services import document_service

router = APIRouter(prefix="/chunks", tags=["documents"])


@router.get(
    "/{chunk_id}",
    response_model=ChunkContext,
    summary="A chunk with its neighbouring chunks, for viewing a citation in context",
    responses={404: {"description": "Chunk not found (or owned by another user)"}},
)
async def get_chunk_in_context(
    chunk_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    neighbors: Annotated[int, Query(ge=0, le=3, description="Chunks to include before and after")] = 1,
) -> ChunkContext:
    return await document_service.get_chunk_context(db, current_user.id, chunk_id, neighbors=neighbors)
