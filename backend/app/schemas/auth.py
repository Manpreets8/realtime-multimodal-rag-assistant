import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def _normalise_email(value: str) -> str:
    return value.strip().lower()


def _password_strength(value: str) -> str:
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must contain at least one letter and one number.")
    return value


def _blank_name_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    full_name: str | None = Field(default=None, max_length=120)

    _normalise = field_validator("email", mode="after")(_normalise_email)
    _strong = field_validator("password")(_password_strength)
    _name = field_validator("full_name")(_blank_name_to_none)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(max_length=120)

    _name = field_validator("full_name")(_blank_name_to_none)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    _strong = field_validator("new_password")(_password_strength)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)

    _normalise = field_validator("email", mode="after")(_normalise_email)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int = Field(description="Token lifetime in seconds")
    user: UserRead
