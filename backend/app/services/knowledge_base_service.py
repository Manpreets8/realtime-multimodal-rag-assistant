"""Knowledge-base operations. Every query is scoped to the owning user."""

import logging
import uuid

from sqlalchemy import Select, func, literal_column, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Conversation, Document, DocumentStatus, KnowledgeBase
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseSort,
    KnowledgeBaseUpdate,
)
from app.services.storage import LocalFileStorage
from app.utils.sql import LIKE_ESCAPE, contains_pattern

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


def _with_stats() -> Select:
    """Knowledge bases with their totals, computed in the database (no query per item)."""
    documents = (
        select(
            Document.knowledge_base_id.label("kb_id"),
            func.count(Document.id).label("documents"),
            func.coalesce(func.sum(Document.chunk_count), 0).label("passages"),
            func.coalesce(func.sum(Document.size_bytes), 0).label("bytes"),
            func.max(Document.updated_at).label("activity"),
        )
        .group_by(Document.knowledge_base_id)
        .subquery()
    )
    conversations = (
        select(Conversation.knowledge_base_id.label("kb_id"), func.count(Conversation.id).label("count"))
        .where(Conversation.knowledge_base_id.is_not(None))
        .group_by(Conversation.knowledge_base_id)
        .subquery()
    )
    last_activity = func.greatest(
        KnowledgeBase.updated_at, func.coalesce(documents.c.activity, KnowledgeBase.updated_at)
    )
    return (
        select(
            KnowledgeBase,
            func.coalesce(documents.c.documents, 0),
            func.coalesce(documents.c.passages, 0),
            func.coalesce(documents.c.bytes, 0),
            func.coalesce(conversations.c.count, 0),
            last_activity.label("last_activity_at"),
        )
        .outerjoin(documents, documents.c.kb_id == KnowledgeBase.id)
        .outerjoin(conversations, conversations.c.kb_id == KnowledgeBase.id)
    )


async def _status_counts(
    db: AsyncSession, kb_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[DocumentStatus, int]]:
    counts: dict[uuid.UUID, dict[DocumentStatus, int]] = {kb_id: {} for kb_id in kb_ids}
    if kb_ids:
        rows = await db.execute(
            select(Document.knowledge_base_id, Document.status, func.count())
            .where(Document.knowledge_base_id.in_(kb_ids))
            .group_by(Document.knowledge_base_id, Document.status)
        )
        for kb_id, status, count in rows:
            counts[kb_id][DocumentStatus(status)] = count
    return counts


async def _read_rows(db: AsyncSession, query: Select) -> list[KnowledgeBaseRead]:
    rows = (await db.execute(query)).all()
    counts = await _status_counts(db, [row[0].id for row in rows])
    # int(): PostgreSQL returns SUM over integers as numeric (Decimal), and model_copy skips validation.
    return [
        KnowledgeBaseRead.model_validate(kb).model_copy(
            update={
                "document_count": int(documents),
                "status_counts": counts[kb.id],
                "passage_count": int(passages),
                "total_bytes": int(total_bytes),
                "conversation_count": int(conversations),
                "last_activity_at": last_activity_at,
            }
        )
        for kb, documents, passages, total_bytes, conversations, last_activity_at in rows
    ]


async def _to_read(db: AsyncSession, kb: KnowledgeBase) -> KnowledgeBaseRead:
    [read] = await _read_rows(db, _with_stats().where(KnowledgeBase.id == kb.id))
    return read


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
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    search: str | None = None,
    sort: KnowledgeBaseSort = "recent",
) -> list[KnowledgeBaseRead]:
    query = _with_stats().where(KnowledgeBase.user_id == user_id)
    if search and search.strip():
        pattern = contains_pattern(search.strip())
        query = query.where(
            or_(
                KnowledgeBase.name.ilike(pattern, escape=LIKE_ESCAPE),
                KnowledgeBase.description.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    order = {
        "recent": [literal_column("last_activity_at").desc()],
        "name": [func.lower(KnowledgeBase.name)],
        "created": [KnowledgeBase.created_at.desc()],
    }[sort]
    return await _read_rows(db, query.order_by(*order, KnowledgeBase.id).limit(limit).offset(offset))


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
