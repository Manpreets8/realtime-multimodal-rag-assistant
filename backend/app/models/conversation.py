import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.image import ImageUpload

# JSONB on PostgreSQL; plain JSON elsewhere.
JsonType = JSON().with_variant(JSONB(), "postgresql")


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [member.value for member in members],
    )


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # The knowledge base new questions are answered from; NULL = general chat.
    # SET NULL keeps the conversation (and its history) if the knowledge base is deleted.
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"), index=True
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )


class Message(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, "message_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Assistant-message metadata (NULL on user messages).
    answer_type: Mapped[str | None] = mapped_column(String(20))
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="SET NULL")
    )
    retrieval_query: Mapped[str | None] = mapped_column(Text)  # the (rewritten) query used for search
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    timings_ms: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    retrieval_stats: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    images: Mapped[list["ImageUpload"]] = relationship(
        passive_deletes=True, order_by="ImageUpload.created_at", lazy="selectin"
    )
    citations: Mapped[list["Citation"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Citation.source_number",
    )


class Citation(UUIDPrimaryKeyMixin, Base):
    """One source given to the model for an assistant message.

    `cited` marks the sources the answer actually cites. The source text and its
    location are snapshotted so history still renders after the document is
    deleted or re-indexed (chunk_id / document_id then become NULL)."""

    __tablename__ = "citations"

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cited: Mapped[bool] = mapped_column(nullable=False)
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_chunks.id", ondelete="SET NULL"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    rerank_score: Mapped[float | None]
    similarity: Mapped[float | None]
    quotes: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list, nullable=False)
    answer_spans: Mapped[list[list[int]]] = mapped_column(JsonType, default=list, nullable=False)

    message: Mapped[Message] = relationship(back_populates="citations")
