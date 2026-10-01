"""Document job queue on a Redis Stream (the producer side, used by the API).

Two kinds of job share the stream: `ingest` (extract, chunk, embed and index a document;
the default when a job has no kind) and `insights` (generate a document's AI insights).

    API ──XADD──► rag:jobs:ingestion ──XREADGROUP──► worker processes (consumer group)

Why a stream with a consumer group rather than a plain list: a job read by a worker
stays *pending* until the worker acknowledges it. If the worker dies mid-document the
job is not lost; another worker claims it once its heartbeats stop (see
`ingestion_worker.py`). Delivery counts give poison-job protection.

The document row in PostgreSQL remains the source of truth for status. A job is only
a nudge to process a document, and processing is idempotent (a document that is
already completed is skipped), so duplicate jobs are harmless. If Redis is down at
upload time the upload still succeeds; the worker's maintenance sweep finds documents
left `uploaded` and queues them.
"""

import logging
import uuid
from dataclasses import dataclass

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.redis import redis_key

logger = logging.getLogger(__name__)

STREAM = redis_key("jobs", "ingestion")
DEAD_LETTER_STREAM = redis_key("jobs", "ingestion", "dead")
GROUP = "ingestion-workers"
# Acknowledged entries are deleted, so the stream only holds waiting and running jobs.
STREAM_MAX_LENGTH = 100_000
QUEUED_MARKER_TTL_SECONDS = 6 * 3600


INGEST = "ingest"
INSIGHTS = "insights"


def queued_marker(document_id: uuid.UUID) -> str:
    """Set while a job for the document is waiting, so the sweep doesn't queue it again."""
    return redis_key("ingestion", "queued", str(document_id))


def processing_lock(document_id: uuid.UUID, kind: str = INGEST) -> str:
    """Held by the worker running a job of `kind` for the document; prevents two workers
    running the same kind of job for one document at once."""
    if kind == INGEST:
        return redis_key("ingestion", "lock", str(document_id))
    return redis_key(kind, "lock", str(document_id))


WORKER_HEARTBEAT_PREFIX = redis_key("workers", "ingestion") + ":"


@dataclass(frozen=True, slots=True)
class QueueStats:
    waiting: int
    running: int
    workers: int
    dead_letters: int


class JobQueue:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def enqueue(self, document_id: uuid.UUID) -> bool:
        """Queue a document for ingestion. Returns False (and logs) if Redis is unreachable;
        the document then stays `uploaded` until the maintenance sweep queues it."""
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(queued_marker(document_id), "1", ex=QUEUED_MARKER_TTL_SECONDS)
                pipe.xadd(
                    STREAM, {"document_id": str(document_id)}, maxlen=STREAM_MAX_LENGTH, approximate=True
                )
                _, job_id = await pipe.execute()
        except RedisError:
            logger.warning("ingestion_enqueue_failed", extra={"document_id": str(document_id)}, exc_info=True)
            return False
        logger.info("ingestion_enqueued", extra={"document_id": str(document_id), "job_id": job_id.decode()})
        return True

    async def enqueue_insights(self, document_id: uuid.UUID) -> bool:
        """Queue AI insight generation. Returns False (and logs) if Redis is unreachable."""
        try:
            job_id = await self._redis.xadd(
                STREAM,
                {"document_id": str(document_id), "kind": INSIGHTS},
                maxlen=STREAM_MAX_LENGTH,
                approximate=True,
            )
        except RedisError:
            logger.warning("insights_enqueue_failed", extra={"document_id": str(document_id)}, exc_info=True)
            return False
        logger.info("insights_enqueued", extra={"document_id": str(document_id), "job_id": job_id.decode()})
        return True

    async def stats(self) -> QueueStats:
        pending = await self._redis.xpending(STREAM, GROUP) if await self._group_exists() else {"pending": 0}
        running = int(pending["pending"])
        length = await self._redis.xlen(STREAM)
        workers = 0
        async for _ in self._redis.scan_iter(match=WORKER_HEARTBEAT_PREFIX + "*", count=100):
            workers += 1
        return QueueStats(
            waiting=max(length - running, 0),
            running=running,
            workers=workers,
            dead_letters=await self._redis.xlen(DEAD_LETTER_STREAM),
        )

    async def _group_exists(self) -> bool:
        try:
            groups = await self._redis.xinfo_groups(STREAM)
        except RedisError:  # stream doesn't exist yet
            return False
        return any(group["name"] in (GROUP, GROUP.encode()) for group in groups)


def get_ingestion_queue(request: Request) -> JobQueue:
    return request.app.state.ingestion_queue
