import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import MessageRole

MAX_MESSAGE_LENGTH = 2000


class ChatRequest(BaseModel):
    message: str = Field(
        default="", max_length=MAX_MESSAGE_LENGTH, description="May be empty when images are attached"
    )
    conversation_id: uuid.UUID | None = Field(default=None, description="Omit to start a new conversation")
    knowledge_base_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Knowledge base to answer from; null = general chat. For an existing conversation, "
            "include this field only to switch knowledge base."
        ),
    )

    image_ids: list[uuid.UUID] = Field(
        default_factory=list,
        max_length=20,
        description="Images uploaded via POST /images to send with this message",
    )

    @field_validator("message")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("image_ids")
    @classmethod
    def _deduplicate(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _message_or_image(self) -> "ChatRequest":
        if not self.message and not self.image_ids:
            raise ValueError("Send a message, an image, or both.")
        return self


class QuoteOut(BaseModel):
    text: str
    start: int | None
    end: int | None


class ChatSource(BaseModel):
    """A source given to the model. IDs become null if the document was deleted or re-indexed;
    the snapshot fields (filename, location, content) always remain."""

    number: int
    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    knowledge_base_id: uuid.UUID | None
    filename: str
    page_number: int | None
    section: str | None
    content: str
    rerank_score: float | None
    similarity: float | None


class ChatCitation(BaseModel):
    source_number: int
    document_id: uuid.UUID | None
    filename: str
    page_number: int | None
    section: str | None
    quotes: list[QuoteOut]
    answer_spans: list[tuple[int, int]]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class ImageRef(BaseModel):
    id: uuid.UUID
    filename: str
    media_type: str
    width: int
    height: int


class MessageRead(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    images: list[ImageRef] = Field(default_factory=list)  # user messages
    # Assistant messages only:
    answer_type: str | None = None
    grounded: bool = False
    knowledge_base_id: uuid.UUID | None = None
    retrieval_query: str | None = None
    model: str | None = None
    usage: Usage | None = None
    truncated: bool = False
    timings_ms: dict[str, float] | None = None
    retrieval: dict[str, Any] | None = None
    citations: list[ChatCitation] = Field(default_factory=list)
    sources: list[ChatSource] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    knowledge_base_id: uuid.UUID | None
    knowledge_base_name: str | None
    message_count: int
    last_message_preview: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageRead]


class ChatResponse(BaseModel):
    conversation: ConversationSummary
    user_message: MessageRead
    assistant_message: MessageRead


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    knowledge_base_id: uuid.UUID | None = None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("Title cannot be empty.")
        return value

    @model_validator(mode="after")
    def _at_least_one(self) -> "ConversationUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null.")
        return self
