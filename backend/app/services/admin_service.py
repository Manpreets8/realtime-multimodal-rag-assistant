"""Account administration. Admins manage accounts (role, enabled) and see activity counts;
no function here reads the content of another user's documents, chats or images."""

import logging
import uuid

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Conversation, Document, KnowledgeBase, User, UserRole
from app.schemas.admin import AdminUserList, AdminUserRead, AdminUserUpdate
from app.schemas.auth import UserRead

logger = logging.getLogger(__name__)


def _with_counts() -> Select:
    knowledge_bases = (
        select(func.count(KnowledgeBase.id)).where(KnowledgeBase.user_id == User.id).scalar_subquery()
    )
    documents = (
        select(func.count(Document.id))
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(KnowledgeBase.user_id == User.id)
        .scalar_subquery()
    )
    conversations = (
        select(func.count(Conversation.id)).where(Conversation.user_id == User.id).scalar_subquery()
    )
    return select(
        User,
        knowledge_bases.label("knowledge_bases"),
        documents.label("documents"),
        conversations.label("conversations"),
    )


def _to_read(user: User, knowledge_bases: int, documents: int, conversations: int) -> AdminUserRead:
    return AdminUserRead(
        **UserRead.model_validate(user).model_dump(),
        knowledge_bases=knowledge_bases,
        documents=documents,
        conversations=conversations,
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_users(db: AsyncSession, *, search: str | None, limit: int, offset: int) -> AdminUserList:
    condition = None
    if search and search.strip():
        pattern = f"%{_escape_like(search.strip())}%"
        condition = or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"))

    query = _with_counts().order_by(User.created_at.desc(), User.id).limit(limit).offset(offset)
    total_query = select(func.count(User.id))
    if condition is not None:
        query = query.where(condition)
        total_query = total_query.where(condition)

    rows = (await db.execute(query)).all()
    return AdminUserList(items=[_to_read(*row) for row in rows], total=await db.scalar(total_query) or 0)


async def update_user(
    db: AsyncSession, actor: User, user_id: uuid.UUID, data: AdminUserUpdate
) -> AdminUserRead:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    # An admin can never lock themselves out, so there is always at least one active admin.
    if user.id == actor.id and (data.role is UserRole.USER or data.is_active is False):
        raise ConflictError("You can't remove your own admin access or disable your own account.")

    changes = {}
    if data.role is not None and data.role is not user.role:
        changes["role"] = data.role.value
        user.role = data.role
    if data.is_active is not None and data.is_active != user.is_active:
        changes["is_active"] = data.is_active
        user.is_active = data.is_active
    if changes:
        await db.commit()
        # Audit trail: who changed whom, and what. A disabled user's sessions stop at their next
        # request, because every request re-reads the account.
        logger.info(
            "admin_user_updated",
            extra={"actor_id": str(actor.id), "target_user_id": str(user.id), **changes},
        )

    row = (await db.execute(_with_counts().where(User.id == user.id))).one()
    return _to_read(*row)


async def set_role_by_email(db: AsyncSession, email: str, role: UserRole) -> User:
    """Used by the command line to create the first administrator."""
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise NotFoundError(f"No account with the email {email!r}. Sign up first.")
    user.role = role
    await db.commit()
    logger.info("user_role_set_from_cli", extra={"target_user_id": str(user.id), "role": role.value})
    return user
