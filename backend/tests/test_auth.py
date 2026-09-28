import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import User
from tests.conftest import DEFAULT_PASSWORD, RegisterFn, bearer

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"


def _forge_token(**overrides: object) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid.uuid4()),
        "jti": uuid.uuid4().hex,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        **overrides,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)


# --- Registration ----------------------------------------------------------


async def test_register_returns_token_and_public_user_fields(register_user: RegisterFn) -> None:
    body = await register_user(email="Ada@Example.com", full_name="  Ada Lovelace ")

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] == get_settings().access_token_expire_minutes * 60
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["full_name"] == "Ada Lovelace"
    assert "password" not in str(body["user"]).lower()


async def test_password_is_stored_as_argon2_hash(register_user: RegisterFn, db: AsyncSession) -> None:
    await register_user(email="hash@example.com")

    stored = await db.scalar(select(User.hashed_password).where(User.email == "hash@example.com"))

    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert DEFAULT_PASSWORD not in stored


async def test_duplicate_email_is_rejected_case_insensitively(
    client: AsyncClient, register_user: RegisterFn
) -> None:
    await register_user(email="dup@example.com")

    response = await client.post(
        f"{AUTH}/register", json={"email": "DUP@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"email": "not-an-email", "password": DEFAULT_PASSWORD}, "email"),
        ({"email": "a@example.com", "password": "short1"}, "password"),
        ({"email": "a@example.com", "password": "onlyletters"}, "password"),
        ({"email": "a@example.com", "password": "12345678"}, "password"),
        ({"email": "a@example.com", "password": "x1" * 65}, "password"),
    ],
)
async def test_register_validates_input(
    client: AsyncClient, db: AsyncSession, payload: dict, field: str
) -> None:
    response = await client.post(f"{AUTH}/register", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"] == ["body", field]


# --- Login -----------------------------------------------------------------


async def test_login_with_valid_credentials(client: AsyncClient, register_user: RegisterFn) -> None:
    await register_user(email="login@example.com")

    response = await client.post(
        f"{AUTH}/login", json={"email": "LOGIN@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "login@example.com"


@pytest.mark.parametrize("email", ["login@example.com", "nobody@example.com"])
async def test_login_failure_does_not_reveal_whether_account_exists(
    client: AsyncClient, register_user: RegisterFn, email: str
) -> None:
    await register_user(email="login@example.com")

    response = await client.post(f"{AUTH}/login", json={"email": email, "password": "wrong-password-1"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid email or password."


async def test_disabled_account_cannot_log_in(
    client: AsyncClient, register_user: RegisterFn, db: AsyncSession
) -> None:
    await register_user(email="disabled@example.com")
    await db.execute(update(User).where(User.email == "disabled@example.com").values(is_active=False))
    await db.commit()

    response = await client.post(
        f"{AUTH}/login", json={"email": "disabled@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 403


# --- Current user / protected routes ---------------------------------------


async def test_me_returns_current_user(client: AsyncClient, register_user: RegisterFn) -> None:
    body = await register_user(email="me@example.com")

    response = await client.get(f"{AUTH}/me", headers=bearer(body["access_token"]))

    assert response.status_code == 200
    assert response.json()["id"] == body["user"]["id"]


async def test_me_requires_authentication(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.get(f"{AUTH}/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("not-a-jwt", id="malformed"),
        pytest.param(_forge_token(exp=datetime.now(UTC) - timedelta(seconds=1)), id="expired"),
        pytest.param(_forge_token(type="refresh"), id="wrong-type"),
        pytest.param(
            jwt.encode(
                {"sub": str(uuid.uuid4()), "jti": "x", "type": "access", "iat": 0, "exp": 9_999_999_999},
                "attacker-secret-that-is-long-enough-32b",
                algorithm="HS256",
            ),
            id="wrong-signature",
        ),
        pytest.param(
            jwt.encode(
                {"sub": str(uuid.uuid4()), "jti": "x", "type": "access", "iat": 0, "exp": 9_999_999_999},
                None,
                algorithm="none",
            ),
            id="alg-none",
        ),
    ],
)
async def test_invalid_tokens_are_rejected(client: AsyncClient, db: AsyncSession, token: str) -> None:
    response = await client.get(f"{AUTH}/me", headers=bearer(token))

    assert response.status_code == 401


async def test_valid_token_for_deleted_user_is_rejected(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.get(f"{AUTH}/me", headers=bearer(_forge_token()))

    assert response.status_code == 401


async def test_token_of_deactivated_user_stops_working(
    client: AsyncClient, register_user: RegisterFn, db: AsyncSession
) -> None:
    body = await register_user(email="deact@example.com")
    await db.execute(update(User).where(User.email == "deact@example.com").values(is_active=False))
    await db.commit()

    response = await client.get(f"{AUTH}/me", headers=bearer(body["access_token"]))

    assert response.status_code == 401


# --- Logout ----------------------------------------------------------------


async def test_logout_revokes_only_the_current_token(client: AsyncClient, register_user: RegisterFn) -> None:
    first = (await register_user(email="multi@example.com"))["access_token"]
    login = await client.post(
        f"{AUTH}/login", json={"email": "multi@example.com", "password": DEFAULT_PASSWORD}
    )
    second = login.json()["access_token"]

    logout = await client.post(f"{AUTH}/logout", headers=bearer(first))

    assert logout.status_code == 204
    assert (await client.get(f"{AUTH}/me", headers=bearer(first))).status_code == 401
    assert (await client.get(f"{AUTH}/me", headers=bearer(second))).status_code == 200


async def test_logout_twice_is_rejected(client: AsyncClient, register_user: RegisterFn) -> None:
    token = (await register_user())["access_token"]
    await client.post(f"{AUTH}/logout", headers=bearer(token))

    response = await client.post(f"{AUTH}/logout", headers=bearer(token))

    assert response.status_code == 401
