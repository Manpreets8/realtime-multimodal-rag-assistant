import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession
from app.schemas.admin import AdminUserList, AdminUserRead, AdminUserUpdate
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_ONLY = {
    401: {"description": "Missing, invalid, expired or revoked token"},
    403: {"description": "The caller is not an administrator"},
}


@router.get(
    "/users",
    response_model=AdminUserList,
    summary="List accounts with activity counts (administrators only)",
    responses=_ADMIN_ONLY,
)
async def list_users(
    db: DbSession,
    _: AdminUser,
    search: Annotated[str | None, Query(max_length=320, description="Part of an email or name")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUserList:
    return await admin_service.list_users(db, search=search, limit=limit, offset=offset)


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserRead,
    summary="Change an account's role or enable/disable it (administrators only)",
    responses={
        **_ADMIN_ONLY,
        404: {"description": "User not found"},
        409: {"description": "An administrator cannot demote or disable themselves"},
    },
)
async def update_user(
    user_id: uuid.UUID, data: AdminUserUpdate, db: DbSession, admin: AdminUser
) -> AdminUserRead:
    return await admin_service.update_user(db, admin, user_id, data)
