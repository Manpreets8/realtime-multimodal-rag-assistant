"""Document ingestion: extract -> clean -> chunk -> embed -> store.

Runs in the ingestion worker process (app.workers.ingestion_worker) with its own
database session. Every stage is timed and logged with the document ID so a
failed or slow ingestion can be traced from the logs, and reported as live
progress (app.services.ingestion_progress) for the UI.
"""

import logging
import time
import uuid
from datetime import UTC, datetime

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
from app.rag.extraction import ExtractionError, extract_text
from app.services import ingestion_progress
from app.services.ingestion_progress import IngestionStage
from app.services.storage import LocalFileStorage, get_storage

logger = logging.getLogger(__name__)

_GENERIC_FAILURE = "Processing failed because of an internal error. Try again, or contact support."
_REPROCESSABLE = {DocumentStatus.FAILED, DocumentStatus.COMPLETED}


class IngestionError(Exception):
    """A user-facing ingestion failure (the message is stored on the document)."""


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
                raise IngestionError(f"No readable text was found in this document.{hint}")

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
            await ingestion_progress.report(document_id, IngestionStage.SAVING, len(chunks), len(chunks))
            if len(vectors) != len(chunks):
                raise EmbeddingError("The embedding service returned an unexpected number of vectors.")

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
            document.processed_at = datetime.now(UTC)
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
            "characters": sum(len(chunk.text) for chunk in chunks),
            "embedding_model": provider.model_name,
            "extraction_ms": extraction_ms,
            "chunking_ms": chunking_ms,
            "embedding_ms": embedding_ms,
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
    document.processing_started_at = None
    document.processed_at = None
    await db.commit()
    await db.refresh(document)
    return document
