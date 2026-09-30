"""Document ingestion: extract (text + metadata) -> clean -> chunk -> embed -> index.

Runs in the ingestion worker process (app.workers.ingestion_worker) with its own
database session. Every stage is timed and logged with the document ID so a
failed or slow ingestion can be traced from the logs, and reported as live
progress (app.services.ingestion_progress) for the UI.
"""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import ConflictError
from app.db.session import SessionLocal
from app.models import Document, DocumentChunk, DocumentStatus
from app.rag import embeddings
from app.rag.chunking import chunk_document
from app.rag.embeddings import EmbeddingError
from app.rag.extraction import ExtractedDocument, ExtractionError, extract_text
from app.services import ingestion_progress
from app.services.ingestion_progress import IngestionStage
from app.services.storage import LocalFileStorage, get_storage

logger = logging.getLogger(__name__)

_GENERIC_FAILURE = "Processing failed because of an internal error. Try again, or contact support."

# Failure codes stored on the document. Extraction errors carry their own (damaged_file,
# password_protected, ...); these cover the other ways a run can fail.
NO_TEXT = "no_text"
EMBEDDING_FAILED = "embedding_failed"
INTERNAL_ERROR = "internal_error"
_REPROCESSABLE = {DocumentStatus.FAILED, DocumentStatus.COMPLETED}


class IngestionError(Exception):
    """A user-facing ingestion failure (the message is stored on the document)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _metadata(extracted: ExtractedDocument) -> dict[str, Any]:
    """What extraction found, stored as JSON (see schemas.document.DocumentMetadata)."""
    text = [block.text for block in extracted.blocks]
    return {
        "title": extracted.title,
        "author": extracted.author,
        "document_date": extracted.document_date.isoformat() if extracted.document_date else None,
        "word_count": sum(len(part.split()) for part in text),
        "character_count": sum(len(part) for part in text),
        "section_count": len({block.section for block in extracted.blocks if block.section}),
        "table_count": extracted.table_count,
    }


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, ExtractionError | IngestionError):
        return exc.code
    if isinstance(exc, EmbeddingError):
        return EMBEDDING_FAILED
    return INTERNAL_ERROR


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def process_document(
    document_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    storage: LocalFileStorage | None = None,
) -> DocumentStatus | None:
    """Ingest one document. Returns the final status, or None if the document no longer exists."""
    storage = storage or get_storage()

    try:
        return await _process(document_id, session_factory, storage)
    finally:
        await ingestion_progress.clear(document_id)


async def _process(
    document_id: uuid.UUID, session_factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage
) -> DocumentStatus | None:
    settings = get_settings()
    log = {"document_id": str(document_id)}
    started = time.perf_counter()

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        if document is None:
            logger.info("ingestion_skipped_missing_document", extra=log)
            return None
        if document.status not in (DocumentStatus.UPLOADED, DocumentStatus.PROCESSING):
            logger.info("ingestion_skipped", extra={**log, "status": document.status.value})
            return document.status

        document.status = DocumentStatus.PROCESSING
        document.processing_started_at = datetime.now(UTC)
        document.error_message = None
        document.error_code = None
        await db.commit()
        logger.info("ingestion_started", extra={**log, "extension": document.extension})

        try:
            stage = time.perf_counter()
            await ingestion_progress.report(document_id, IngestionStage.EXTRACTING)
            extracted = await run_in_threadpool(
                extract_text, storage.path_for(document.storage_key), document.extension
            )
            extraction_ms = _elapsed_ms(stage)

            stage = time.perf_counter()
            await ingestion_progress.report(document_id, IngestionStage.CHUNKING)
            chunks = await run_in_threadpool(
                chunk_document, extracted, settings.chunk_size, settings.chunk_overlap
            )
            chunking_ms = _elapsed_ms(stage)
            if not chunks:
                hint = (
                    " It may be a scanned document; OCR is not supported."
                    if document.extension == ".pdf"
                    else ""
                )
                raise IngestionError(f"No readable text was found in this document.{hint}", NO_TEXT)

            stage = time.perf_counter()
            provider = embeddings.get_embedding_provider()
            # In batches, so progress can be reported (the provider batches requests itself too).
            vectors: list[list[float]] = []
            batch = settings.embedding_batch_size
            for start in range(0, len(chunks), batch):
                await ingestion_progress.report(document_id, IngestionStage.EMBEDDING, start, len(chunks))
                vectors.extend(
                    await provider.embed_documents([c.text for c in chunks[start : start + batch]])
                )
            embedding_ms = _elapsed_ms(stage)
            if len(vectors) != len(chunks):
                raise EmbeddingError("The embedding service returned an unexpected number of vectors.")

            stage = time.perf_counter()
            await ingestion_progress.report(document_id, IngestionStage.INDEXING, len(chunks), len(chunks))

            # Replace any chunks from a previous run in the same transaction as the new ones.
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            db.add_all(
                DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=document.knowledge_base_id,
                    user_id=document.user_id,
                    chunk_index=chunk.index,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    content=chunk.text,
                    char_count=len(chunk.text),
                    embedding=vector,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
            document.status = DocumentStatus.COMPLETED
            document.chunk_count = len(chunks)
            document.page_count = extracted.page_count
            document.extracted_metadata = _metadata(extracted)
            document.processed_at = datetime.now(UTC)
            await db.flush()  # the chunks and vectors are written here, before the statistics
            indexing_ms = _elapsed_ms(stage)
            characters = sum(len(chunk.text) for chunk in chunks)
            document.processing_stats = {
                "extraction_ms": extraction_ms,
                "chunking_ms": chunking_ms,
                "embedding_ms": embedding_ms,
                "indexing_ms": indexing_ms,
                "total_ms": _elapsed_ms(started),
                "embedding_model": provider.model_name,
                "characters": characters,
            }
            await db.commit()
        except Exception as exc:
            await db.rollback()
            user_message = (
                str(exc)
                if isinstance(exc, ExtractionError | IngestionError | EmbeddingError)
                else _GENERIC_FAILURE
            )
            logger.warning(
                "ingestion_failed",
                exc_info=not isinstance(exc, ExtractionError | IngestionError),
                extra={**log, "error": user_message, "duration_ms": _elapsed_ms(started)},
            )
            # Update by primary key: after rollback the ORM object is expired, and the
            # document may have been deleted meanwhile (then this updates 0 rows).
            await db.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(
                    status=DocumentStatus.FAILED,
                    error_message=user_message,
                    error_code=_failure_code(exc),
                    chunk_count=0,
                    processed_at=datetime.now(UTC),
                )
            )
            await db.commit()
            return DocumentStatus.FAILED

    logger.info(
        "ingestion_completed",
        extra={
            **log,
            "chunks": len(chunks),
            "pages": extracted.page_count,
            "characters": characters,
            "embedding_model": provider.model_name,
            "extraction_ms": extraction_ms,
            "chunking_ms": chunking_ms,
            "embedding_ms": embedding_ms,
            "indexing_ms": indexing_ms,
            "duration_ms": _elapsed_ms(started),
        },
    )
    return DocumentStatus.COMPLETED


async def reset_for_reprocessing(db: AsyncSession, document: Document) -> Document:
    """Queue a failed or completed document to be processed again."""
    if document.status not in _REPROCESSABLE:
        raise ConflictError("This document is already queued or being processed.")
    document.status = DocumentStatus.UPLOADED
    document.error_message = None
    document.error_code = None
    document.processing_stats = None  # the metadata stays until the new run replaces it
    document.processing_started_at = None
    document.processed_at = None
    await db.commit()
    await db.refresh(document)
    return document
