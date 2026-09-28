"""Shared async Redis client.

Redis holds the ingestion job stream, rate-limit windows, caches and short-lived
processing state (job progress, locks, worker heartbeats). Nothing in it is the
source of truth: documents, messages and users live in PostgreSQL, so losing Redis
loses at most cached values and queued jobs, which the maintenance sweep re-queues
from the database.

All keys are prefixed `rag:` so the database can be shared with other applications.
"""

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings

KEY_PREFIX = "rag:"


def redis_key(*parts: str) -> str:
    return KEY_PREFIX + ":".join(parts)


def create_redis(*, socket_timeout: float | None = 5.0) -> Redis:
    """A new client. Blocking reads (the worker's XREADGROUP) need a timeout longer than the block."""
    return Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=2.0,
        socket_timeout=socket_timeout,
        health_check_interval=30,
    )


@lru_cache
def get_redis() -> Redis:
    """The process-wide client (connections are pooled)."""
    return create_redis()


async def close_redis() -> None:
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()
