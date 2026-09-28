"""Document upload and management. Every query is scoped to the owning user."""

import logging
import uuid
from collections.abc import AsyncIterator, Sequence

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import ConflictError, InvalidDocumentError, NotFoundError
from app.models import Document, DocumentChunk, DocumentStatus
from app.schemas.document import ChunkContext, ContextChunk, DocumentRead
from app.services import ingestion_progress, knowledge_base_service
from app.services.storage import LocalFileStorage
from app.utils.files import resolve_file_type, sanitize_filename, validate_file_content

logger = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 1024 * 1024


async def _iter_upload(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(_READ_CHUNK_BYTES):
        yield chunk


async def upload(
    db: AsyncSession,
    storage: LocalFileStorage,
    user_id: uuid.UUID,
    kb_id: uuid.UUID,
    file: UploadFile,
) -> Document:
    kb = await knowledge_base_service.get_owned(db, user_id, kb_id)
    filename = sanitize_filename(file.filename)
    file_type = resolve_file_type(filename)

    document_id = uuid.uuid4()
    final_key = f"{user_id}/{kb.id}/{document_id}{file_type.extension}"
    temp_key = f"{final_key}.part"

    stored = await storage.write_stream(temp_key, _iter_upload(file), get_settings().max_file_size)
    try:
        if stored.size_bytes == 0:
            raise InvalidDocumentError("The file is empty.")
        await run_in_threadpool(validate_file_content, storage.path_for(temp_key), file_type)

        existing = await db.scalar(
            select(Document.filename).where(
                Document.knowledge_base_id == kb.id, Document.checksum_sha256 == stored.sha256
            )
        )
        if existing is not None:
            raise ConflictError(f"This file is already in the knowledge base as '{existing}'.")

        await storage.move(temp_key, final_key)
        document = Document(
            id=document_id,
            knowledge_base_id=kb.id,
            user_id=user_id,
            filename=filename,
            extension=file_type.extension,
            content_type=file_type.content_type,
            size_bytes=stored.size_bytes,
            checksum_sha256=stored.sha256,
            storage_key=final_key,
            status=DocumentStatus.UPLOADED,
        )
        db.add(document)
        try:
            await db.commit()
        except IntegrityError as exc:  # concurrent upload of identical content
            await db.rollback()
            raise ConflictError("This file is already in the knowledge base.") from exc
    except BaseException:
        await storage.delete(temp_key)
        await storage.delete(final_key)
        raise

    await db.refresh(document)
    logger.info(
        "document_uploaded",
        extra={
            "document_id": str(document.id),
            "knowledge_base_id": str(kb.id),
            "size_bytes": stored.size_bytes,
            "extension": file_type.extension,
        },
    )
    return document


async def list_for_knowledge_base(
    db: AsyncSession, user_id: uuid.UUID, kb_id: uuid.UUID, *, limit: int, offset: int
) -> list[Document]:
    await knowledge_base_service.get_owned(db, user_id, kb_id)
    result = await db.scalars(
        select(Document)
        .where(Document.knowledge_base_id == kb_id, Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result)


async def get_owned(db: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = await db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    if document is None:
        raise NotFoundError("Document not found.")
    return document


async def delete(
    db: AsyncSession, storage: LocalFileStorage, user_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    document = await get_owned(db, user_id, document_id)
    storage_key = document.storage_key
    await db.delete(document)
    await db.commit()
    try:
        await storage.delete(storage_key)
    except OSError:
        logger.exception("document_file_delete_failed", extra={"storage_key": storage_key})
    logger.info("document_deleted", extra={"document_id": str(document_id)})


async def list_chunks(
    db: AsyncSession, user_id: uuid.UUID, document_id: uuid.UUID, *, limit: int, offset: int
) -> list[DocumentChunk]:
    await get_owned(db, user_id, document_id)
    result = await db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id, DocumentChunk.user_id == user_id)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
        .offset(offset)
    )
    return list(result)


async def get_chunk_context(
    db: AsyncSession, user_id: uuid.UUID, chunk_id: uuid.UUID, *, neighbors: int
) -> ChunkContext:
    chunk = await db.scalar(
        select(DocumentChunk).where(DocumentChunk.id == chunk_id, DocumentChunk.user_id == user_id)
    )
    if chunk is None:
        raise NotFoundError("Source not found. The document may have been deleted or re-indexed.")
    document = await get_owned(db, user_id, chunk.document_id)
    nearby = list(
        await db.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == chunk.document_id,
                DocumentChunk.chunk_index.between(
                    chunk.chunk_index - neighbors, chunk.chunk_index + neighbors
                ),
                DocumentChunk.id != chunk.id,
            )
            .order_by(DocumentChunk.chunk_index)
        )
    )
    return ChunkContext(
        chunk=ContextChunk.model_validate(chunk),
        before=[ContextChunk.model_validate(c) for c in nearby if c.chunk_index < chunk.chunk_index],
        after=[ContextChunk.model_validate(c) for c in nearby if c.chunk_index > chunk.chunk_index],
        document=DocumentRead.model_validate(document),
    )


async def with_progress(documents: Sequence[Document]) -> list[DocumentRead]:
    """Documents as API models, with live ingestion progress for those being processed."""
    processing = [d.id for d in documents if d.status is DocumentStatus.PROCESSING]
    progress = await ingestion_progress.read_many(processing)
    return [
        DocumentRead.model_validate(document).model_copy(update={"progress": progress.get(document.id)})
        for document in documents
    ]
