"""Shared FastAPI dependencies. Protect a route with `current_user: CurrentUser`, or restrict it to
administrators with `admin: AdminUser`."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import InvalidTokenError, TokenPayload, decode_access_token
from app.db.session import get_db
from app.models import User
from app.services import auth_service
from app.services.storage import LocalFileStorage, get_storage
from app.workers.job_queue import JobQueue, get_ingestion_queue

DbSession = Annotated[AsyncSession, Depends(get_db)]
Storage = Annotated[LocalFileStorage, Depends(get_storage)]
Ingestion = Annotated[JobQueue, Depends(get_ingestion_queue)]

# auto_error=False so a missing header produces our own 401 envelope, not FastAPI's 403.
_bearer = HTTPBearer(auto_error=False, description="JWT from /auth/login or /auth/register")


async def get_token_payload(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenPayload:
    if credentials is None:
        raise UnauthorizedError("Authentication is required.")
    try:
        payload = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise UnauthorizedError("Your session is invalid or has expired. Please log in again.") from exc
    if await auth_service.is_token_revoked(db, payload.jti):
        raise UnauthorizedError("Your session has ended. Please log in again.")
    return payload


async def get_current_user(
    db: DbSession, payload: Annotated[TokenPayload, Depends(get_token_payload)]
) -> User:
    user = await auth_service.get_active_user(db, payload.subject)
    if user is None:
        raise UnauthorizedError("Your session is invalid or has expired. Please log in again.")
    return user


CurrentToken = Annotated[TokenPayload, Depends(get_token_payload)]
CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(current_user: CurrentUser) -> User:
    # The role comes from the database row loaded above, so a demotion applies to the next request.
    if not current_user.is_admin:
        raise ForbiddenError("This area is for administrators.")
    return current_user


AdminUser = Annotated[User, Depends(get_admin_user)]


def _per_user_limit(scope: rate_limit.Scope):
    async def dependency(current_user: CurrentUser) -> None:
        await rate_limit.enforce(scope, str(current_user.id))

    return dependency


# Rate limits per authenticated user (429 with Retry-After). Add as a parameter: `_: ChatLimit`.
ChatLimit = Annotated[None, Depends(_per_user_limit(rate_limit.Scope.CHAT))]
UploadLimit = Annotated[None, Depends(_per_user_limit(rate_limit.Scope.UPLOADS))]
VoiceLimit = Annotated[None, Depends(_per_user_limit(rate_limit.Scope.VOICE))]
