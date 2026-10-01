"""Conversations: sending messages through the RAG pipeline and persisting history.

A question and its answer are stored together, in one transaction, only after the
answer succeeded. A failed request (LLM error, timeout, ...) stores nothing, so
history never contains dangling questions; the client simply retries.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.llm import factory as llm_factory
from app.llm.base import ImagePart
from app.models import Citation, Conversation, ImageUpload, KnowledgeBase, Message, MessageRole
from app.rag import embeddings, reranking
from app.rag.conversation import HistoryMessage, rewrite_query, trim_history
from app.rag.pipeline import AnswerEvents, AnswerType, RagAnswer, Stage, answer_question, pipeline_stats
from app.rag.prompts import DEFAULT_IMAGE_QUESTION
from app.schemas.chat import (
    ChatCitation,
    ChatRequest,
    ChatResponse,
    ChatSource,
    ConversationDetail,
    ConversationSummary,
    ConversationUpdate,
    ImageRef,
    MessageRead,
    QuoteOut,
    Usage,
)
from app.services import image_service
from app.services.retrieval_service import ensure_knowledge_bases_owned
from app.services.storage import LocalFileStorage

logger = logging.getLogger(__name__)

TITLE_LENGTH = 60
# Messages are ordered by created_at. The wall clock can return the same value for a whole
# turn (on Windows it advances in ~1-16 ms ticks), so each timestamp is forced past the
# previous one instead of trusting clock resolution.
_TICK = timedelta(microseconds=1)
PREVIEW_LENGTH = 120


def make_title(message: str) -> str:
    text = " ".join(message.split())
    if len(text) <= TITLE_LENGTH:
        return text
    cut = text[:TITLE_LENGTH].rsplit(" ", 1)[0] or text[:TITLE_LENGTH]
    return cut.rstrip(".,;:!?") + "…"


# --- reading -----------------------------------------------------------------------


async def _get_owned(db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation


def _summary_query():
    message_count = (
        select(func.count(Message.id)).where(Message.conversation_id == Conversation.id).scalar_subquery()
    )
    last_message = (
        select(func.left(Message.content, PREVIEW_LENGTH))
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    return select(
        Conversation,
        KnowledgeBase.name.label("knowledge_base_name"),
        message_count.label("message_count"),
        last_message.label("last_message_preview"),
    ).outerjoin(KnowledgeBase, KnowledgeBase.id == Conversation.knowledge_base_id)


def _summary(row) -> ConversationSummary:
    conversation: Conversation = row[0]
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        knowledge_base_id=conversation.knowledge_base_id,
        knowledge_base_name=row.knowledge_base_name,
        message_count=row.message_count,
        last_message_preview=row.last_message_preview,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


async def list_conversations(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int, offset: int
) -> list[ConversationSummary]:
    rows = await db.execute(
        _summary_query()
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_summary(row) for row in rows]


async def get_summary(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> ConversationSummary:
    row = (
        await db.execute(
            _summary_query().where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Conversation not found.")
    return _summary(row)


def message_read(message: Message) -> MessageRead:
    if message.role is MessageRole.USER:
        return MessageRead(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            images=[
                ImageRef(
                    id=i.id, filename=i.filename, media_type=i.media_type, width=i.width, height=i.height
                )
                for i in message.images
            ],
        )
    cited = [c for c in message.citations if c.cited]
    return MessageRead(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        answer_type=message.answer_type,
        grounded=bool(cited),
        knowledge_base_id=message.knowledge_base_id,
        retrieval_query=message.retrieval_query,
        model=message.model,
        usage=Usage(input_tokens=message.input_tokens, output_tokens=message.output_tokens)
        if message.model and message.input_tokens is not None
        else None,
        truncated=message.truncated,
        timings_ms=message.timings_ms,
        retrieval=message.retrieval_stats,
        citations=[
            ChatCitation(
                source_number=c.source_number,
                document_id=c.document_id,
                filename=c.filename,
                page_number=c.page_number,
                section=c.section,
                quotes=[QuoteOut(**quote) for quote in c.quotes],
                answer_spans=[tuple(span) for span in c.answer_spans],
            )
            for c in cited
        ],
        sources=[
            ChatSource(
                number=c.source_number,
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                knowledge_base_id=c.knowledge_base_id,
                filename=c.filename,
                page_number=c.page_number,
                section=c.section,
                content=c.content,
                rerank_score=c.rerank_score,
                similarity=c.similarity,
            )
            for c in message.citations
        ],
    )


async def get_conversation(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> ConversationDetail:
    summary = await get_summary(db, user_id, conversation_id)
    messages = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .options(selectinload(Message.citations))
        .order_by(Message.created_at, Message.id)
    )
    return ConversationDetail(**summary.model_dump(), messages=[message_read(m) for m in messages])


async def _history(
    db: AsyncSession, storage: LocalFileStorage, conversation_id: uuid.UUID, limit: int, max_images: int
) -> list[HistoryMessage]:
    """Recent messages, oldest first. Images from the most recent user messages are
    included (up to `max_images` in total) so follow-ups can refer to them."""
    if limit == 0:
        return []
    messages = list(
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
    )  # newest first
    budget = max_images
    history: list[HistoryMessage] = []
    for message in messages:
        blocks: list[ImagePart] = []
        if message.role is MessageRole.USER and message.images and budget > 0:
            kept = message.images[-budget:]
            budget -= len(kept)
            blocks = await image_service.model_blocks(storage, kept)
        history.append(HistoryMessage(message.role.value, message.content, tuple(blocks)))
    return list(reversed(history))


# --- writing -----------------------------------------------------------------------


async def update_conversation(
    db: AsyncSession, user_id: uuid.UUID, conversation_id: uuid.UUID, data: ConversationUpdate
) -> ConversationSummary:
    conversation = await _get_owned(db, user_id, conversation_id)
    if "knowledge_base_id" in data.model_fields_set:
        if data.knowledge_base_id is not None:
            await ensure_knowledge_bases_owned(db, user_id, [data.knowledge_base_id])
        conversation.knowledge_base_id = data.knowledge_base_id
    if data.title is not None:
        conversation.title = data.title
    await db.commit()
    return await get_summary(db, user_id, conversation_id)


async def delete_conversation(
    db: AsyncSession, storage: LocalFileStorage, user_id: uuid.UUID, conversation_id: uuid.UUID
) -> None:
    conversation = await _get_owned(db, user_id, conversation_id)
    image_keys = list(
        await db.scalars(
            select(ImageUpload.storage_key)
            .join(Message, Message.id == ImageUpload.message_id)
            .where(Message.conversation_id == conversation_id)
        )
    )
    await db.delete(conversation)  # messages, citations and image rows cascade
    await db.commit()
    await image_service.delete_files(storage, image_keys)  # after commit: at worst an orphaned file


def _citation_rows(result: RagAnswer) -> list[Citation]:
    cited = {citation.source_number: citation for citation in result.citations}
    rows = []
    for number, ranked in enumerate(result.sources, start=1):
        chunk = ranked.chunk
        citation = cited.get(number)
        rows.append(
            Citation(
                source_number=number,
                cited=citation is not None,
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                knowledge_base_id=chunk.knowledge_base_id,
                filename=chunk.filename,
                page_number=chunk.page_number,
                section=chunk.section,
                content=chunk.content,
                rerank_score=ranked.rerank_score,
                similarity=chunk.similarity,
                quotes=[{"text": q.text, "start": q.start, "end": q.end} for q in citation.quotes]
                if citation
                else [],
                answer_spans=[list(span) for span in citation.answer_spans] if citation else [],
            )
        )
    return rows


async def send_message(
    db: AsyncSession,
    storage: LocalFileStorage,
    user_id: uuid.UUID,
    request: ChatRequest,
    events: AnswerEvents | None = None,
) -> ChatResponse:
    """`events` receives progress and the answer text as it is generated (WebSocket chat).
    Nothing is saved unless the whole turn succeeds, including when the caller cancels."""
    settings = get_settings()
    received_at = datetime.now(UTC)
    llm = llm_factory.get_llm_provider()  # fail fast (503) before doing any work if no API key is configured

    conversation = await _get_owned(db, user_id, request.conversation_id) if request.conversation_id else None
    if conversation is not None and conversation.updated_at >= received_at:
        received_at = conversation.updated_at + _TICK
    switching = conversation is None or "knowledge_base_id" in request.model_fields_set
    knowledge_base_id = request.knowledge_base_id if switching else conversation.knowledge_base_id
    if knowledge_base_id is not None:
        await ensure_knowledge_bases_owned(db, user_id, [knowledge_base_id])

    if len(request.image_ids) > settings.max_images_per_message:
        raise AppError(f"Attach at most {settings.max_images_per_message} images per message.")
    if request.image_ids:
        llm_factory.ensure_vision(llm)
    images = await image_service.get_attachable(db, user_id, request.image_ids)
    image_blocks = await image_service.model_blocks(storage, images)
    question = request.message or DEFAULT_IMAGE_QUESTION

    history = (
        trim_history(
            await _history(
                db, storage, conversation.id, settings.chat_history_messages, settings.max_history_images
            ),
            settings.chat_history_messages,
            settings.chat_history_message_chars,
        )
        if conversation
        else []
    )

    # Follow-ups and image questions need a standalone text query for retrieval
    # (general chat has no retrieval).
    retrieval_query, rewrite_info = question, {}
    if knowledge_base_id and (history or image_blocks) and settings.query_rewrite_enabled:
        if events:
            await events.stage(Stage.REWRITING)
        retrieval_query, rewrite_info = await rewrite_query(
            llm, history, question, effort=settings.query_rewrite_effort, images=image_blocks
        )

    result = await answer_question(
        db,
        user_id=user_id,
        knowledge_base_ids=[knowledge_base_id] if knowledge_base_id else [],
        question=question,
        embedder=embeddings.get_embedding_provider(),
        reranker=reranking.get_reranker(),
        llm=llm,
        candidates=settings.top_k,
        rerank_candidates=settings.rerank_candidates,
        top_k=settings.rerank_top_k,
        similarity_threshold=settings.similarity_threshold,
        history=history,
        retrieval_query=retrieval_query,
        images=image_blocks,
        events=events,
        filters=request.filters.to_filters() if request.filters else None,
    )

    # Persist the question and its answer together.
    answered_at = max(datetime.now(UTC), received_at + _TICK)
    if conversation is None:
        conversation = Conversation(
            user_id=user_id, title=make_title(request.message), knowledge_base_id=knowledge_base_id
        )
        db.add(conversation)
    else:
        conversation.knowledge_base_id = knowledge_base_id
    conversation.updated_at = answered_at

    user_message = Message(
        conversation=conversation,
        role=MessageRole.USER,
        content=request.message,
        created_at=received_at,
        images=images,  # attaches the uploads (sets images.message_id)
    )
    stats = pipeline_stats(result)
    retrieval_stats = {**stats, **rewrite_info} if stats is not None else None
    timings = dict(result.timings_ms)
    if "rewrite_ms" in rewrite_info:
        timings["rewrite"] = rewrite_info["rewrite_ms"]
    assistant_message = Message(
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content=result.answer,
        created_at=answered_at,
        answer_type=result.answer_type.value,
        knowledge_base_id=knowledge_base_id,
        retrieval_query=retrieval_query if retrieval_query != question else None,
        model=result.model,
        input_tokens=result.input_tokens if result.model else None,
        output_tokens=result.output_tokens if result.model else None,
        truncated=result.truncated,
        timings_ms=timings,
        retrieval_stats=retrieval_stats,
        citations=_citation_rows(result),
    )
    db.add_all([user_message, assistant_message])
    await db.commit()

    logger.info(
        "chat_message_answered",
        extra={
            "conversation_id": str(conversation.id),
            "answer_type": result.answer_type.value,
            "history_messages": len(history),
            "query_rewritten": rewrite_info.get("rewritten", False),
            "general": result.answer_type is AnswerType.GENERAL,
        },
    )
    return ChatResponse(
        conversation=await get_summary(db, user_id, conversation.id),
        user_message=message_read(user_message),
        assistant_message=message_read(assistant_message),
    )
