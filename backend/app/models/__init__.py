"""Import every model here so Alembic autogenerate and relationship resolution see them."""

from app.models.conversation import Citation, Conversation, Message, MessageRole
from app.models.document import Document, DocumentStatus
from app.models.document_chunk import EMBEDDING_COLUMN_DIMENSIONS, DocumentChunk
from app.models.image import ImageUpload
from app.models.knowledge_base import KnowledgeBase
from app.models.revoked_token import RevokedToken
from app.models.user import User, UserRole

__all__ = [
    "EMBEDDING_COLUMN_DIMENSIONS",
    "Citation",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentStatus",
    "ImageUpload",
    "KnowledgeBase",
    "Message",
    "MessageRole",
    "RevokedToken",
    "User",
    "UserRole",
]
