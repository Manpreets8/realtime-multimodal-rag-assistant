import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import TokenPayload, hash_password, verify_password
from app.models import RevokedToken, User
from app.schemas.auth import LoginRequest, PasswordChange, ProfileUpdate, RegisterRequest

logger = logging.getLogger(__name__)

_EMAIL_TAKEN = "An account with this email already exists."
_INVALID_CREDENTIALS = "Invalid email or password."


async def register_user(db: AsyncSession, data: RegisterRequest) -> User:
    if await db.scalar(select(exists().where(User.email == data.email))):
        raise ConflictError(_EMAIL_TAKEN)

    user = User(
        email=data.email,
        hashed_password=await hash_password(data.password),
        full_name=data.full_name,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Two concurrent registrations for the same email: the unique index wins.
        await db.rollback()
        raise ConflictError(_EMAIL_TAKEN) from exc
    await db.refresh(user)
    logger.info("user_registered", extra={"user_id": str(user.id)})
    return user


async def authenticate_user(db: AsyncSession, data: LoginRequest) -> User:
    user = await db.scalar(select(User).where(User.email == data.email))
    valid, new_hash = await verify_password(data.password, user.hashed_password if user else None)

    if user is None or not valid:
        logger.info("login_failed")
        raise UnauthorizedError(_INVALID_CREDENTIALS)
    if not user.is_active:
        raise ForbiddenError("This account has been disabled.")

    if new_hash:  # hashing parameters were upgraded since this password was stored
        user.hashed_password = new_hash
        await db.commit()
    logger.info("login_succeeded", extra={"user_id": str(user.id)})
    return user


class WrongPasswordError(AppError):
    # Not 401: the caller is signed in, and a 401 would end their session in the app.
    status_code = 400
    code = "wrong_password"
    message = "Your current password is incorrect."


async def update_profile(db: AsyncSession, user: User, data: ProfileUpdate) -> User:
    user.full_name = data.full_name
    await db.commit()
    await db.refresh(user)
    return user


async def end_other_sessions(db: AsyncSession, user: User) -> User:
    """Invalidate every token issued so far; the caller gets a new one for this session."""
    user.session_version += 1
    await db.commit()
    await db.refresh(user)
    logger.info("sessions_ended", extra={"user_id": str(user.id)})
    return user


async def change_password(db: AsyncSession, user: User, data: PasswordChange) -> User:
    valid, _ = await verify_password(data.current_password, user.hashed_password)
    if not valid:
        logger.info("password_change_rejected", extra={"user_id": str(user.id)})
        raise WrongPasswordError()
    if data.new_password == data.current_password:
        raise AppError("Choose a password different from your current one.")
    user.hashed_password = await hash_password(data.new_password)
    # A changed password signs out every other session (e.g. a device it may have leaked from).
    user.session_version += 1
    await db.commit()
    await db.refresh(user)
    logger.info("password_changed", extra={"user_id": str(user.id)})
    return user


async def get_active_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    user = await db.get(User, user_id)
    return user if user and user.is_active else None


async def is_token_revoked(db: AsyncSession, jti: str) -> bool:
    return bool(await db.scalar(select(exists().where(RevokedToken.jti == jti))))


async def revoke_token(db: AsyncSession, token: TokenPayload) -> None:
    await db.execute(
        insert(RevokedToken)
        .values(jti=token.jti, expires_at=token.expires_at)
        .on_conflict_do_nothing(index_elements=[RevokedToken.jti])
    )
    # Housekeeping: expired tokens are rejected by signature checks anyway.
    await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC)))
    await db.commit()
