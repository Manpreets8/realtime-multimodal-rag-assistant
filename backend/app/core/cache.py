"""Byte caches in Redis, shared by every API process and surviving restarts.

Every entry has a TTL, so under memory pressure Redis (maxmemory-policy volatile-lru)
evicts cache entries and never the job stream. A cache failure never fails the request:
reads miss and writes are skipped.
"""

import hashlib
import logging

from redis.exceptions import RedisError

from app.core import redis as redis_client
from app.core.redis import redis_key

logger = logging.getLogger(__name__)


def cache_key(namespace: str, *parts: str) -> str:
    """A fixed-length key from arbitrary (possibly long) parts."""
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return redis_key("cache", namespace, digest)


async def get_bytes(key: str) -> bytes | None:
    try:
        return await redis_client.get_redis().get(key)
    except RedisError:
        logger.warning("cache_read_failed", extra={"key": key.rsplit(":", 1)[0]}, exc_info=True)
        return None


async def set_bytes(key: str, value: bytes, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    try:
        await redis_client.get_redis().set(key, value, ex=ttl_seconds)
    except RedisError:
        logger.warning("cache_write_failed", extra={"key": key.rsplit(":", 1)[0]}, exc_info=True)
