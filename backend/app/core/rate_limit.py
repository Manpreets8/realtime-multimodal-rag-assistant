"""Rate limiting on Redis (sliding-window counter).

Limits are shared by every API process, so they hold however many workers serve the API;
an in-memory limiter would multiply the limit by the process count. They protect what
costs money or invites abuse: LLM answers, logins, sign-ups, uploads and voice.

Algorithm: two fixed-window counters, weighted. With a 60 s window at 15 s into the
current window, the estimate is `previous * 0.75 + current`. It needs two small keys
per client and scope, is accurate to within a few percent, and has none of a plain
fixed window's double burst at the boundary. The check and increment run in one Lua
script, so concurrent requests can't both slip under the limit.

If Redis is unreachable, requests are allowed (fail open) and a warning is logged:
the limiter protects the service, and it should not take the service down.
"""

import logging
import math
import time
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request
from redis.exceptions import RedisError

from app.core import redis as redis_client
from app.core.config import get_settings, parse_rate
from app.core.errors import AppError
from app.core.redis import redis_key

logger = logging.getLogger(__name__)


class Scope(StrEnum):
    LOGIN = "login"
    REGISTER = "register"
    CHAT = "chat"
    UPLOADS = "uploads"
    VOICE = "voice"


# KEYS: current window, previous window. ARGV: limit, weight of previous window, window ms.
# Returns {allowed, previous count, current count (after this request if allowed)}.
_SLIDING_WINDOW = """
local previous = tonumber(redis.call('GET', KEYS[2]) or '0')
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if previous * tonumber(ARGV[2]) + current + 1 > tonumber(ARGV[1]) then
  return {0, previous, current}
end
current = redis.call('INCR', KEYS[1])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
return {1, previous, current}
"""


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Please try again later."

    def __init__(self, retry_after: int) -> None:
        wait = f"{retry_after} seconds" if retry_after < 120 else f"{math.ceil(retry_after / 60)} minutes"
        super().__init__(
            f"Too many requests. Please try again in {wait}.", details={"retry_after": retry_after}
        )
        self.headers = {"Retry-After": str(retry_after)}


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int  # seconds; 0 when allowed


def retry_after(limit: int, window: float, elapsed: float, previous: int, current: int) -> float:
    """Seconds until one more request fits under `limit`.

    In this window the estimate is previous * (1 - (elapsed + t) / window) + current, which
    falls as the previous window's weight decays. If it can't fall far enough before the
    window ends, the current count becomes the previous one in the next window and decays
    in turn: current * (1 - tau / window)."""
    spare = limit - 1 - current
    if spare >= 0 and previous:
        t = window * (1 - spare / previous) - elapsed
        if t < window - elapsed:
            return max(t, 0.0)
    tau = window * (1 - (limit - 1) / current) if current > limit - 1 else 0.0
    return window - elapsed + tau


async def hit(scope: Scope, identity: str, rule: str) -> RateLimitResult:
    """Count one request for `identity` (a user ID or client address) against `rule` ("20/1m")."""
    limit, window_seconds = parse_rate(rule)
    now = time.time()
    window_index = int(now // window_seconds)
    elapsed = now - window_index * window_seconds
    base = redis_key("ratelimit", scope.value, identity)
    try:
        allowed, previous, current = await redis_client.get_redis().eval(
            _SLIDING_WINDOW,
            2,
            f"{base}:{window_index}",
            f"{base}:{window_index - 1}",
            limit,
            1 - elapsed / window_seconds,
            window_seconds * 1000,
        )
    except RedisError:
        logger.warning("rate_limit_unavailable", extra={"scope": scope.value}, exc_info=True)
        return RateLimitResult(True, limit, limit, 0)
    estimate = previous * (1 - elapsed / window_seconds) + current
    if allowed:
        return RateLimitResult(True, limit, max(int(limit - estimate), 0), 0)
    retry = math.ceil(retry_after(limit, window_seconds, elapsed, previous, current))
    logger.info("rate_limited", extra={"scope": scope.value, "retry_after": retry})
    return RateLimitResult(False, limit, 0, max(retry, 1))


def _rule(scope: Scope) -> str:
    return getattr(get_settings(), f"rate_limit_{scope.value}")


async def enforce(scope: Scope, identity: str) -> None:
    """Raise RateLimitedError (429 with Retry-After) when `identity` is over its limit."""
    if not get_settings().rate_limit_enabled:
        return
    result = await hit(scope, identity, _rule(scope))
    if not result.allowed:
        raise RateLimitedError(result.retry_after)


def client_address(request: Request) -> str:
    """The client IP. Behind a reverse proxy, run uvicorn with --proxy-headers and
    --forwarded-allow-ips so this is the real client, not the proxy."""
    return request.client.host if request.client else "unknown"
