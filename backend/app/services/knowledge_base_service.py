"""Knowledge-base operations. Every query is scoped to the owning user."""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Document, DocumentStatus, KnowledgeBase
from app.schemas.knowledge_base import KnowledgeBaseCreate, KnowledgeBaseRead, KnowledgeBaseUpdate
from app.services.storage import LocalFileStorage

logger = logging.getLogger(__name__)

_NAME_TAKEN = "You already have a knowledge base with this name."


async def get_owned(db: AsyncSession, user_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBase:
    """Fetch a knowledge base owned by `user_id`. Other users' KBs are reported as not found."""
    kb = await db.scalar(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id)
    )
    if kb is None:
        raise NotFoundError("Knowledge base not found.")
    return kb


async def _to_read(db: AsyncSession, kb: KnowledgeBase) -> KnowledgeBaseRead:
    rows = await db.execute(
        select(Document.status, func.count())
        .where(Document.knowledge_base_id == kb.id)
        .group_by(Document.status)
    )
    counts = {DocumentStatus(status): count for status, count in rows.all()}
    return KnowledgeBaseRead.model_validate(kb).model_copy(
        update={"document_count": sum(counts.values()), "status_counts": counts}
    )


async def create(db: AsyncSession, user_id: uuid.UUID, data: KnowledgeBaseCreate) -> KnowledgeBaseRead:
    kb = KnowledgeBase(user_id=user_id, name=data.name, description=data.description)
    db.add(kb)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(_NAME_TAKEN) from exc
    await db.refresh(kb)
    logger.info("knowledge_base_created", extra={"knowledge_base_id": str(kb.id)})
    return KnowledgeBaseRead.model_validate(kb)


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int, offset: int
) -> list[KnowledgeBaseRead]:
    document_count = func.count(Document.id).label("document_count")
    rows = await db.execute(
        select(KnowledgeBase, document_count)
        .outerjoin(Document, Document.knowledge_base_id == KnowledgeBase.id)
        .where(KnowledgeBase.user_id == user_id)
        .group_by(KnowledgeBase.id)
        .order_by(KnowledgeBase.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    listed = rows.all()
    # Status breakdown for every listed knowledge base in one grouped query (no query per item).
    counts: dict[uuid.UUID, dict[DocumentStatus, int]] = {kb.id: {} for kb, _ in listed}
    if counts:
        status_rows = await db.execute(
            select(Document.knowledge_base_id, Document.status, func.count())
            .where(Document.knowledge_base_id.in_(counts))
            .group_by(Document.knowledge_base_id, Document.status)
        )
        for kb_id, status, count in status_rows:
            counts[kb_id][DocumentStatus(status)] = count
    return [
        KnowledgeBaseRead.model_validate(kb).model_copy(
            update={"document_count": count, "status_counts": counts[kb.id]}
        )
        for kb, count in listed
    ]


async def get(db: AsyncSession, user_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBaseRead:
    return await _to_read(db, await get_owned(db, user_id, kb_id))


async def update(
    db: AsyncSession, user_id: uuid.UUID, kb_id: uuid.UUID, data: KnowledgeBaseUpdate
) -> KnowledgeBaseRead:
    kb = await get_owned(db, user_id, kb_id)
    for field in data.model_fields_set:
        setattr(kb, field, getattr(data, field))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(_NAME_TAKEN) from exc
    await db.refresh(kb)
    return await _to_read(db, kb)


async def delete(db: AsyncSession, storage: LocalFileStorage, user_id: uuid.UUID, kb_id: uuid.UUID) -> None:
    kb = await get_owned(db, user_id, kb_id)
    storage_keys = list(
        await db.scalars(select(Document.storage_key).where(Document.knowledge_base_id == kb.id))
    )
    await db.delete(kb)  # documents are removed by ON DELETE CASCADE
    await db.commit()

    # Files are removed after the commit: a crash here leaves orphaned files
    # (harmless, can be swept) rather than rows pointing at missing files.
    for key in storage_keys:
        try:
            await storage.delete(key)
        except OSError:
            logger.exception("document_file_delete_failed", extra={"storage_key": key})
    logger.info(
        "knowledge_base_deleted",
        extra={"knowledge_base_id": str(kb_id), "documents_deleted": len(storage_keys)},
    )
