import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class DocumentStats(BaseModel):
    total: int
    indexed: int
    processing: int  # uploaded (waiting for a worker) or being processed
    failed: int


class DailyCount(BaseModel):
    date: date  # UTC day
    count: int


class AIAnswerStats(BaseModel):
    """Answers generated in chat over the period. One-off answers (the Ask tab, image questions)
    are not saved to a conversation and are not included."""

    days: int
    answers: int
    input_tokens: int
    output_tokens: int
    by_day: list[DailyCount]


class DashboardStats(BaseModel):
    knowledge_bases: int
    documents: DocumentStats
    conversations: int
    ai_answers: AIAnswerStats


ActivityKind = Literal[
    "knowledge_base_created", "document_uploaded", "document_ready", "document_failed", "conversation_started"
]


class ActivityItem(BaseModel):
    kind: ActivityKind
    at: datetime
    title: str
    detail: str | None = None
    knowledge_base_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None


class DashboardResponse(BaseModel):
    stats: DashboardStats
    activity: list[ActivityItem]
