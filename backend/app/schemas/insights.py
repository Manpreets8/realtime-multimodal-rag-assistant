from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EntityType = Literal["person", "organization", "location", "product", "technology", "date", "other"]
ENTITY_TYPES: tuple[str, ...] = (
    "person",
    "organization",
    "location",
    "product",
    "technology",
    "date",
    "other",
)

MAX_SUMMARY_CHARS = 6000
MAX_ITEM_CHARS = 300


def _clean_list(values: list[str], limit: int) -> list[str]:
    """Trim, drop empties and case-insensitive duplicates, cap length and count."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = " ".join(str(value).split())[:MAX_ITEM_CHARS]
        if text and text.lower() not in seen:
            seen.add(text.lower())
            cleaned.append(text)
    return cleaned[:limit]


class InsightEntity(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_ITEM_CHARS)
    type: EntityType


class InsightContent(BaseModel):
    """What the model produced, validated and cleaned. Keywords and entity names are kept only
    if they occur in the document text (see insights_service)."""

    language: str | None = Field(default=None, max_length=50)  # as reported by the model, e.g. "English"
    short_summary: str = Field(max_length=MAX_SUMMARY_CHARS)
    detailed_summary: str = Field(max_length=MAX_SUMMARY_CHARS * 2)
    technical_summary: str = Field(max_length=MAX_SUMMARY_CHARS * 2)
    key_points: list[str]
    topics: list[str]
    keywords: list[str]
    entities: list[InsightEntity]
    # Keywords and entities the model returned that don't appear in the document (removed).
    ungrounded_removed: int = 0

    @field_validator("short_summary", "detailed_summary", "technical_summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return str(value).strip()[: MAX_SUMMARY_CHARS * 2]

    @field_validator("key_points")
    @classmethod
    def _key_points(cls, values: list[str]) -> list[str]:
        return _clean_list(values, 10)

    @field_validator("topics")
    @classmethod
    def _topics(cls, values: list[str]) -> list[str]:
        return _clean_list(values, 8)

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, values: list[str]) -> list[str]:
        return _clean_list(values, 20)

    @field_validator("entities", mode="before")
    @classmethod
    def _entities(cls, values: object) -> object:
        if not isinstance(values, list):
            return values
        seen: set[tuple[str, str]] = set()
        kept = []
        for item in values:
            if not isinstance(item, dict):
                continue
            name = " ".join(str(item.get("name", "")).split())[:MAX_ITEM_CHARS]
            kind = item.get("type") if item.get("type") in ENTITY_TYPES else "other"
            if name and (name.lower(), kind) not in seen:
                seen.add((name.lower(), kind))
                kept.append({"name": name, "type": kind})
        return kept[:25]


InsightState = Literal["none", "pending", "ready", "failed"]


class InsightRead(BaseModel):
    status: InsightState
    content: InsightContent | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    coverage: float | None = None  # share of the document the model read
    error_message: str | None = None
    requested_at: datetime | None = None
    generated_at: datetime | None = None
