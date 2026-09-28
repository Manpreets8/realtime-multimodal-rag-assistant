"""Live ingestion progress ("Embedding 128/400 chunks"), kept in Redis only.

Progress changes many times per document and is worthless once the document is done,
so it doesn't belong in PostgreSQL: a hash per document with a TTL, removed when
processing ends. Reads and writes never fail the caller; without Redis the UI simply
shows "Processing" without detail.
"""

import logging
import time
import uuid
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel
from redis.exceptions import RedisError

from app.core import redis as redis_client
from app.core.redis import redis_key

logger = logging.getLogger(__name__)

PROGRESS_TTL_SECONDS = 3600


class IngestionStage(StrEnum):
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    SAVING = "saving"


class IngestionProgress(BaseModel):
    stage: IngestionStage
    done: int = 0  # embedding: chunks embedded so far
    total: int = 0  # embedding: chunks to embed
    updated_at: float


def _key(document_id: uuid.UUID) -> str:
    return redis_key("ingestion", "progress", str(document_id))


async def report(document_id: uuid.UUID, stage: IngestionStage, done: int = 0, total: int = 0) -> None:
    try:
        async with redis_client.get_redis().pipeline(transaction=True) as pipe:
            pipe.hset(
                _key(document_id),
                mapping={"stage": stage.value, "done": done, "total": total, "updated_at": time.time()},
            )
            pipe.expire(_key(document_id), PROGRESS_TTL_SECONDS)
            await pipe.execute()
    except RedisError:
        logger.debug("ingestion_progress_write_failed", exc_info=True)


async def clear(document_id: uuid.UUID) -> None:
    try:
        await redis_client.get_redis().delete(_key(document_id))
    except RedisError:
        logger.debug("ingestion_progress_clear_failed", exc_info=True)


async def read_many(document_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, IngestionProgress]:
    if not document_ids:
        return {}
    try:
        async with redis_client.get_redis().pipeline(transaction=False) as pipe:
            for document_id in document_ids:
                pipe.hgetall(_key(document_id))
            rows = await pipe.execute()
    except RedisError:
        logger.debug("ingestion_progress_read_failed", exc_info=True)
        return {}
    result = {}
    for document_id, row in zip(document_ids, rows, strict=True):
        if row:
            fields = {k.decode(): v.decode() for k, v in row.items()}
            result[document_id] = IngestionProgress.model_validate(fields)
    return result
