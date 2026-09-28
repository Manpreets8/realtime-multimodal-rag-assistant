import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import DocumentStatus

NAME_MAX_LENGTH = 100
DESCRIPTION_MAX_LENGTH = 1000


def _clean_name(value: str) -> str:
    value = " ".join(value.split())  # collapse internal whitespace
    if not value:
        raise ValueError("Name cannot be empty.")
    return value


def _clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)

    _name = field_validator("name")(_clean_name)
    _description = field_validator("description")(_clean_description)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)

    _description = field_validator("description")(_clean_description)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "KnowledgeBaseUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null.")
        return self


class KnowledgeBaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    document_count: int = 0
    status_counts: dict[DocumentStatus, int] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
