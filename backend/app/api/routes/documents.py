import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession, Ingestion, Storage, UploadLimit
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import Document, DocumentChunk
from app.schemas.document import DocumentChunkRead, DocumentRead, SupportedFileType, UploadConfig
from app.services import document_service, ingestion_service
from app.utils.files import SUPPORTED_FILE_TYPES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

_NOT_FOUND = {404: {"description": "Document not found (or owned by another user)"}}


# Declared before "/{document_id}" so the literal path is matched first.
@router.get("/upload-config", response_model=UploadConfig, summary="Upload limits and supported file types")
async def upload_config(current_user: CurrentUser) -> UploadConfig:
    return UploadConfig(
        max_file_size=get_settings().max_file_size,
        supported_types=[
            SupportedFileType(extension=t.extension, content_type=t.content_type, label=t.label)
            for t in SUPPORTED_FILE_TYPES.values()
        ],
    )


@router.post(
    "/upload",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Knowledge base not found"},
        409: {"description": "Identical file already in this knowledge base"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported file type"},
        422: {"description": "File content does not match its type, or is empty"},
        429: {"description": "Upload rate limit exceeded (see Retry-After)"},
    },
)
async def upload_document(
    db: DbSession,
    storage: Storage,
    queue: Ingestion,
    current_user: CurrentUser,
    _: UploadLimit,
    knowledge_base_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File(description="PDF, DOCX, TXT or Markdown file")],
) -> Document:
    """Store the file and queue it for ingestion by a worker. Returns immediately with status
    `uploaded`; poll `GET /documents/{id}` for `processing` (with `progress`) -> `completed` / `failed`."""
    document = await document_service.upload(db, storage, current_user.id, knowledge_base_id, file)
    await queue.enqueue(document.id)
    return document


@router.get("/{document_id}", response_model=DocumentRead, responses=_NOT_FOUND)
async def get_document(document_id: uuid.UUID, db: DbSession, current_user: CurrentUser) -> DocumentRead:
    document = await document_service.get_owned(db, current_user.id, document_id)
    return (await document_service.with_progress([document]))[0]


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_NOT_FOUND, 409: {"description": "Already queued or processing"}},
    summary="Queue a failed or completed document for ingestion again",
)
async def reprocess_document(
    document_id: uuid.UUID, db: DbSession, queue: Ingestion, current_user: CurrentUser
) -> Document:
    document = await document_service.get_owned(db, current_user.id, document_id)
    document = await ingestion_service.reset_for_reprocessing(db, document)
    await queue.enqueue(document.id)
    return document


@router.get(
    "/{document_id}/chunks",
    response_model=list[DocumentChunkRead],
    responses=_NOT_FOUND,
    summary="The indexed chunks of a document, in order",
)
async def list_document_chunks(
    document_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentChunk]:
    return await document_service.list_chunks(db, current_user.id, document_id, limit=limit, offset=offset)


@router.get("/{document_id}/download", response_class=FileResponse, responses=_NOT_FOUND)
async def download_document(
    document_id: uuid.UUID,
    db: DbSession,
    storage: Storage,
    current_user: CurrentUser,
    inline: Annotated[
        bool,
        Query(description="Display PDFs in the browser instead of downloading (other types always download)"),
    ] = False,
) -> FileResponse:
    document = await document_service.get_owned(db, current_user.id, document_id)
    if not await storage.exists(document.storage_key):
        logger.error("document_file_missing", extra={"document_id": str(document.id)})
        raise NotFoundError("The file for this document is no longer available.")
    return FileResponse(
        storage.path_for(document.storage_key),
        media_type=document.content_type,
        filename=document.filename,  # RFC 6266 Content-Disposition with a safe filename encoding
        # Only PDFs may render inline: an uploaded HTML/SVG-like payload must never execute in our origin.
        content_disposition_type="inline" if inline and document.extension == ".pdf" else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_document(
    document_id: uuid.UUID, db: DbSession, storage: Storage, current_user: CurrentUser
) -> Response:
    await document_service.delete(db, storage, current_user.id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
