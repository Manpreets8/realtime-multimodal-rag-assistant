"""The signed-in user's workspace at a glance. Every query is scoped to that user.

The activity feed is derived from existing records (creation and processing timestamps)
rather than a separate event log, so it can never disagree with the data it describes.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Document, DocumentStatus, KnowledgeBase, Message, MessageRole
from app.schemas.dashboard import (
    ActivityItem,
    AIAnswerStats,
    DailyCount,
    DashboardResponse,
    DashboardStats,
    DocumentStats,
)

AI_PERIOD_DAYS = 30
CHART_DAYS = 14
ACTIVITY_ITEMS = 12


async def _document_stats(db: AsyncSession, user_id: uuid.UUID) -> DocumentStats:
    rows = await db.execute(
        select(Document.status, func.count()).where(Document.user_id == user_id).group_by(Document.status)
    )
    counts = {DocumentStatus(status): count for status, count in rows.all()}
    return DocumentStats(
        total=sum(counts.values()),
        indexed=counts.get(DocumentStatus.COMPLETED, 0),
        processing=counts.get(DocumentStatus.UPLOADED, 0) + counts.get(DocumentStatus.PROCESSING, 0),
        failed=counts.get(DocumentStatus.FAILED, 0),
    )


async def _ai_answer_stats(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> AIAnswerStats:
    answers = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id, Message.role == MessageRole.ASSISTANT)
    )
    since = now - timedelta(days=AI_PERIOD_DAYS)
    subquery = answers.where(Message.created_at >= since).subquery()
    count, input_tokens, output_tokens = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(subquery.c.input_tokens), 0),
                func.coalesce(func.sum(subquery.c.output_tokens), 0),
            ).select_from(subquery)
        )
    ).one()

    first_day = now.date() - timedelta(days=CHART_DAYS - 1)
    recent = answers.where(
        Message.created_at >= datetime.combine(first_day, datetime.min.time(), UTC)
    ).subquery()
    day = cast(func.timezone("UTC", recent.c.created_at), Date)
    per_day: dict[date, int] = dict(
        (await db.execute(select(day, func.count()).select_from(recent).group_by(day))).all()
    )
    by_day = [
        DailyCount(
            date=first_day + timedelta(days=offset), count=per_day.get(first_day + timedelta(days=offset), 0)
        )
        for offset in range(CHART_DAYS)
    ]
    return AIAnswerStats(
        days=AI_PERIOD_DAYS,
        answers=count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        by_day=by_day,
    )


async def _activity(db: AsyncSession, user_id: uuid.UUID) -> list[ActivityItem]:
    items: list[ActivityItem] = []

    knowledge_bases = await db.execute(
        select(KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.created_at)
        .where(KnowledgeBase.user_id == user_id)
        .order_by(KnowledgeBase.created_at.desc())
        .limit(ACTIVITY_ITEMS)
    )
    items += [
        ActivityItem(kind="knowledge_base_created", at=at, title=name, knowledge_base_id=kb_id)
        for kb_id, name, at in knowledge_bases
    ]

    documents = await db.execute(
        select(Document, KnowledgeBase.name)
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(Document.user_id == user_id)
        .order_by(
            func.greatest(
                Document.created_at, func.coalesce(Document.processed_at, Document.created_at)
            ).desc()
        )
        .limit(ACTIVITY_ITEMS)
    )
    for document, kb_name in documents:
        items.append(
            ActivityItem(
                kind="document_uploaded",
                at=document.created_at,
                title=document.filename,
                detail=kb_name,
                knowledge_base_id=document.knowledge_base_id,
            )
        )
        if document.processed_at and document.status is DocumentStatus.COMPLETED:
            passages = "passage" if document.chunk_count == 1 else "passages"
            items.append(
                ActivityItem(
                    kind="document_ready",
                    at=document.processed_at,
                    title=document.filename,
                    detail=f"{document.chunk_count} {passages} indexed",
                    knowledge_base_id=document.knowledge_base_id,
                )
            )
        elif document.processed_at and document.status is DocumentStatus.FAILED:
            items.append(
                ActivityItem(
                    kind="document_failed",
                    at=document.processed_at,
                    title=document.filename,
                    detail=document.error_message,
                    knowledge_base_id=document.knowledge_base_id,
                )
            )

    conversations = await db.execute(
        select(Conversation.id, Conversation.title, Conversation.created_at)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
        .limit(ACTIVITY_ITEMS)
    )
    items += [
        ActivityItem(kind="conversation_started", at=at, title=title, conversation_id=conversation_id)
        for conversation_id, title, at in conversations
    ]

    items.sort(key=lambda item: item.at, reverse=True)
    return items[:ACTIVITY_ITEMS]


async def get_dashboard(
    db: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> DashboardResponse:
    now = now or datetime.now(UTC)
    knowledge_bases = await db.scalar(
        select(func.count(KnowledgeBase.id)).where(KnowledgeBase.user_id == user_id)
    )
    conversations = await db.scalar(
        select(func.count(Conversation.id)).where(Conversation.user_id == user_id)
    )
    return DashboardResponse(
        stats=DashboardStats(
            knowledge_bases=knowledge_bases or 0,
            documents=await _document_stats(db, user_id),
            conversations=conversations or 0,
            ai_answers=await _ai_answer_stats(db, user_id, now),
        ),
        activity=await _activity(db, user_id),
    )
