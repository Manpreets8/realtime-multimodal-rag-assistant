from pydantic import BaseModel, model_validator

from app.models.user import UserRole
from app.schemas.auth import UserRead


class AdminUserRead(UserRead):
    """Account details and activity counts. Never the content of a user's documents or chats."""

    knowledge_bases: int
    documents: int
    conversations: int


class AdminUserList(BaseModel):
    items: list[AdminUserRead]
    total: int


class AdminUserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "AdminUserUpdate":
        if self.role is None and self.is_active is None:
            raise ValueError("Provide role and/or is_active.")
        return self
