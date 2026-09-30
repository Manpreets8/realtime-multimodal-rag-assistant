"""Password hashing (Argon2 via pwdlib) and JWT access tokens (PyJWT)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()

# Verified against when a login email does not exist, so response time does not
# reveal whether an account is registered.
_DUMMY_HASH = _password_hash.hash("dummy-password-for-timing-equalisation")

ACCESS_TOKEN_TYPE = "access"  # noqa: S105 - claim value, not a secret


@dataclass(frozen=True, slots=True)
class TokenPayload:
    subject: uuid.UUID
    jti: str
    expires_at: datetime
    # The account's session version when the token was issued; a password change (or "sign out
    # everywhere") increments the account's version, which invalidates every older token.
    session_version: int = 0


class InvalidTokenError(Exception):
    """Raised for any malformed, expired, or tampered token."""


# Argon2 is deliberately CPU-heavy (~tens of ms); run it off the event loop.
async def hash_password(password: str) -> str:
    return await run_in_threadpool(_password_hash.hash, password)


async def verify_password(password: str, hashed_password: str | None) -> tuple[bool, str | None]:
    """Return (is_valid, new_hash_if_parameters_changed)."""
    if hashed_password is None:
        await run_in_threadpool(_password_hash.verify, password, _DUMMY_HASH)
        return False, None
    return await run_in_threadpool(_password_hash.verify_and_update, password, hashed_password)


def create_access_token(user_id: uuid.UUID, session_version: int = 0) -> tuple[str, int]:
    """Return (encoded_jwt, lifetime_in_seconds)."""
    settings = get_settings()
    lifetime = timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "jti": uuid.uuid4().hex,
        "type": ACCESS_TOKEN_TYPE,
        "ver": session_version,
        "iat": now,
        "exp": now + lifetime,
    }
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str) -> TokenPayload:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pin the algorithm list: never let the token header choose it.
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "jti", "exp", "iat", "type"]},
        )
        if claims["type"] != ACCESS_TOKEN_TYPE:
            raise InvalidTokenError("unexpected token type")
        return TokenPayload(
            subject=uuid.UUID(claims["sub"]),
            jti=str(claims["jti"]),
            expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            session_version=int(claims.get("ver", 0)),  # tokens from before versioning count as 0
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc
