import uuid

import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


async def test_hash_and_verify_password() -> None:
    hashed = await hash_password("s3cret-password")

    assert hashed != "s3cret-password"
    assert (await verify_password("s3cret-password", hashed))[0] is True
    assert (await verify_password("wrong-password", hashed))[0] is False


async def test_hashes_are_salted() -> None:
    assert await hash_password("same-password-1") != await hash_password("same-password-1")


async def test_verify_against_missing_user_returns_false() -> None:
    assert await verify_password("anything", None) == (False, None)


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token, expires_in = create_access_token(user_id)

    payload = decode_access_token(token)

    assert payload.subject == user_id
    assert len(payload.jti) == 32
    assert expires_in > 0


def test_each_token_has_a_unique_jti() -> None:
    user_id = uuid.uuid4()
    first, _ = create_access_token(user_id)
    second, _ = create_access_token(user_id)

    assert decode_access_token(first).jti != decode_access_token(second).jti


def test_tampered_token_is_rejected() -> None:
    token, _ = create_access_token(uuid.uuid4())
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[::-1]}"

    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)
