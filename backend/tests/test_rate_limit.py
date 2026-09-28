import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core import rate_limit
from app.core import redis as redis_client
from app.core.config import get_settings, parse_rate
from app.core.rate_limit import Scope, hit, retry_after
from tests.conftest import DEFAULT_PASSWORD, RegisterFn, bearer
from tests.ws_client import websocket

# --- the algorithm ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "parsed"),
    [("20/1m", (20, 60)), ("10/15m", (10, 900)), ("5/d", (5, 86_400)), (" 3 / 30s ", (3, 30))],
)
def test_parse_rate(value: str, parsed: tuple[int, int]) -> None:
    assert parse_rate(value) == parsed


@pytest.mark.parametrize("value", ["20", "0/1m", "ten/1m", "5/1w", "5/-1m"])
def test_invalid_rates_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid rate limit"):
        parse_rate(value)


@pytest.mark.parametrize(
    ("previous", "current", "elapsed", "expected"),
    [
        (10, 0, 0, 6),  # 10/min last minute: 10 * (1 - t/60) <= 9 after 6 s
        (0, 10, 30, 36),  # all used this minute: wait for the next window, then 6 s of decay
        (20, 5, 10, 38),  # 20 * (1 - (10 + t)/60) + 5 <= 9
    ],
)
def test_retry_after(previous: int, current: int, elapsed: float, expected: float) -> None:
    assert retry_after(10, 60, elapsed, previous, current) == pytest.approx(expected)


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.mark.integration
async def test_sliding_window(redis_db: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    window_start = 60 * 16_667.0  # a window boundary
    clock = Clock(window_start + 20)  # 20 s into a 60 s window
    monkeypatch.setattr(rate_limit.time, "time", clock.time)

    results = [await hit(Scope.CHAT, "u1", "3/1m") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert results[3].retry_after == 40 + 20  # next window, then a third of it decays (3 * 2/3 = 2)

    # Early in the next window the previous 3 still weigh 3 * (1 - 1/60) = 2.95: still full.
    clock.now = window_start + 61
    assert not (await hit(Scope.CHAT, "u1", "3/1m")).allowed
    # 21 s in, the weight is 3 * 39/60 = 1.95: room for exactly one.
    clock.now = window_start + 81
    assert (await hit(Scope.CHAT, "u1", "3/1m")).allowed
    assert not (await hit(Scope.CHAT, "u1", "3/1m")).allowed
    # Other identities and scopes are independent.
    assert (await hit(Scope.CHAT, "u2", "3/1m")).allowed
    assert (await hit(Scope.VOICE, "u1", "3/1m")).allowed


@pytest.mark.integration
async def test_windows_expire_from_redis(redis_db: Redis) -> None:
    await hit(Scope.CHAT, "u1", "3/1m")

    keys = [key async for key in redis_db.scan_iter(match="rag:ratelimit:chat:u1:*")]
    assert len(keys) == 1
    assert 60_000 < await redis_db.pttl(keys[0]) <= 120_000


async def test_fails_open_when_redis_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    unreachable = Redis.from_url("redis://localhost:1/15", socket_connect_timeout=0.2)
    monkeypatch.setattr(redis_client, "get_redis", lambda: unreachable)
    try:
        results = [await hit(Scope.CHAT, "u1", "1/1m") for _ in range(3)]
    finally:
        await unreachable.aclose()

    assert all(r.allowed for r in results)


# --- the API ------------------------------------------------------------------------------------


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


@pytest.mark.integration
async def test_chat_is_limited_per_user_with_retry_after(
    client: AsyncClient, alice: dict, register_user: RegisterFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "2/1m")
    bob = bearer((await register_user(email="bob@example.com"))["access_token"])

    statuses = [
        (await client.post("/api/v1/chat", json={"message": "Hi"}, headers=alice)).status_code
        for _ in range(3)
    ]
    limited = await client.post("/api/v1/chat", json={"message": "Hi"}, headers=alice)
    other_user = await client.post("/api/v1/chat", json={"message": "Hi"}, headers=bob)

    assert statuses == [200, 200, 429]
    assert limited.status_code == 429
    body = limited.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["message"].startswith("Too many requests. Please try again in")
    assert int(limited.headers["retry-after"]) == body["details"]["retry_after"] > 0
    assert other_user.status_code == 200


@pytest.mark.integration
async def test_unauthenticated_requests_get_401_not_429(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "1/1m")
    for _ in range(3):
        assert (await client.post("/api/v1/chat", json={"message": "Hi"})).status_code == 401


@pytest.mark.integration
async def test_login_is_limited_per_address_and_account(
    client: AsyncClient, register_user: RegisterFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register_user(email="alice@example.com")
    monkeypatch.setattr(get_settings(), "rate_limit_login", "3/15m")

    async def login(email: str, password: str) -> int:
        response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        return response.status_code

    guesses = [await login("alice@example.com", f"wrong-{n}") for n in range(3)]
    blocked_even_with_the_right_password = await login("Alice@Example.com", DEFAULT_PASSWORD)
    other_account = await login("bob@example.com", "whatever")

    assert guesses == [401, 401, 401]
    assert blocked_even_with_the_right_password == 429  # case-insensitive email
    assert other_account == 401


@pytest.mark.integration
async def test_registration_is_limited_per_address(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_register", "2/1h")

    statuses = [
        (
            await client.post(
                "/api/v1/auth/register", json={"email": f"user{n}@example.com", "password": DEFAULT_PASSWORD}
            )
        ).status_code
        for n in range(3)
    ]

    assert statuses == [201, 201, 429]


@pytest.mark.integration
async def test_uploads_and_voice_are_limited(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_uploads", "1/1h")
    monkeypatch.setattr(get_settings(), "rate_limit_voice", "1/1m")
    from tests.images import PNG

    images = [
        (
            await client.post("/api/v1/images", files={"file": ("a.png", PNG, "image/png")}, headers=alice)
        ).status_code
        for _ in range(2)
    ]
    speech = [
        (await client.post("/api/v1/voice/synthesize", json={"text": "Hello."}, headers=alice)).status_code
        for _ in range(2)
    ]

    assert images == [201, 429]
    assert speech == [200, 429]


@pytest.mark.integration
async def test_websocket_turns_share_the_chat_limit(
    app: FastAPI, client: AsyncClient, register_user: RegisterFn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "1/1m")
    token = (await register_user(email="carol@example.com"))["access_token"]
    await client.post("/api/v1/chat", json={"message": "Over REST"}, headers=bearer(token))

    async with websocket(app, token=token) as ws:
        await ws.send_json({"type": "chat", "id": "t1", "message": "Over the socket"})
        event = await ws.receive_json()

    assert event["type"] == "error" and event["id"] == "t1"
    assert event["error"]["code"] == "rate_limited"
    assert event["error"]["details"]["retry_after"] > 0


@pytest.mark.integration
async def test_limits_can_be_disabled(
    client: AsyncClient, alice: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_chat", "1/1m")
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)

    for _ in range(3):
        assert (await client.post("/api/v1/chat", json={"message": "Hi"}, headers=alice)).status_code == 200
