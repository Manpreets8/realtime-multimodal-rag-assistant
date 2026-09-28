import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.document import Document


class KnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan", passive_deletes=True
    )


# A user cannot have two knowledge bases whose names differ only by case.
Index(
    "uq_knowledge_bases_user_id_lower_name",
    KnowledgeBase.user_id,
    func.lower(KnowledgeBase.name),
    unique=True,
)
