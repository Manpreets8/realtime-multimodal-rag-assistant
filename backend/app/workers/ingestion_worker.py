"""Ingestion worker process: `python -m app.workers.ingestion_worker`.

Consumes the Redis Stream written by `job_queue.JobQueue` through a consumer group, so
any number of worker processes (on any machines) share the work, and runs periodic
maintenance. Parsing and embedding are CPU- and memory-heavy; running them here keeps
the API process responsive and lets ingestion scale separately.

Reliability:
- **At-least-once delivery.** A job is acknowledged only after `process_document` has
  recorded the result in PostgreSQL. A worker that crashes leaves its job pending.
- **Heartbeats.** While a job runs, the worker resets the job's idle time every few
  seconds. A job idle longer than INGESTION_JOB_TIMEOUT_SECONDS belongs to a dead
  worker, and the next free worker claims it (XAUTOCLAIM). Long documents are never
  taken over while their worker is alive.
- **One worker per document.** A per-document lock (SET NX, refreshed with the
  heartbeat) stops a duplicate job from processing the same document concurrently.
- **Poison jobs.** A job delivered more than INGESTION_MAX_DELIVERIES times (it keeps
  killing its worker) is moved to a dead-letter stream and the document (or, for an
  insights job, its insights) marked failed, instead of crashing workers forever.
- **Job kinds.** `ingest` jobs (the default) process documents; `insights` jobs generate a
  document's AI insights. Each kind has its own per-document lock.
- **Graceful shutdown.** On Ctrl+C / SIGTERM the worker stops taking jobs and finishes
  the ones it has (up to a grace period); anything unfinished is picked up by another
  worker or after restart.
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import update

from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.redis import create_redis
from app.db.session import SessionLocal, engine
from app.models import Document, DocumentStatus
from app.rag.embeddings import get_embedding_provider
from app.services import insights_service
from app.services.ingestion_service import process_document
from app.workers import maintenance
from app.workers.job_queue import (
    DEAD_LETTER_STREAM,
    GROUP,
    INGEST,
    INSIGHTS,
    STREAM,
    WORKER_HEARTBEAT_PREFIX,
    processing_lock,
    queued_marker,
)

logger = logging.getLogger(__name__)

Processor = Callable[[uuid.UUID], Awaitable[object]]

BLOCK_MS = 5000
SHUTDOWN_GRACE_SECONDS = 60
HEARTBEAT_TTL_SECONDS = 30
DEAD_LETTER_MAX_LENGTH = 10_000

# Delete the lock / extend it only if this worker still owns it.
_RELEASE_LOCK = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
_EXTEND_LOCK = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "return redis.call('PEXPIRE', KEYS[1], ARGV[2]) end return 0"
)
POISON_MESSAGE = (
    "Processing was stopped after {attempts} attempts because it failed unexpectedly each time. "
    "Try reprocessing it, or contact support."
)


class IngestionWorker:
    def __init__(
        self,
        redis: Redis,
        processor: Processor = process_document,
        *,
        insights_processor: Processor = insights_service.generate,
        concurrency: int | None = None,
        job_timeout_seconds: float | None = None,
        max_deliveries: int | None = None,
        maintenance_interval_seconds: float | None = None,
        block_ms: int = BLOCK_MS,
        consumer: str | None = None,
        run_maintenance: bool = True,
    ) -> None:
        settings = get_settings()
        self._redis = redis
        self._processors: dict[str, Processor] = {INGEST: processor, INSIGHTS: insights_processor}
        self._concurrency = concurrency or settings.ingestion_workers
        self._job_timeout_ms = int((job_timeout_seconds or settings.ingestion_job_timeout_seconds) * 1000)
        self._max_deliveries = max_deliveries or settings.ingestion_max_deliveries
        self._maintenance_interval = maintenance_interval_seconds or settings.maintenance_interval_seconds
        self._block_ms = block_ms
        self._run_maintenance = run_maintenance
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._stopping = asyncio.Event()
        self._active: dict[str, uuid.UUID] = {}
        self.jobs_done = 0
        self._started = time.time()

    # --- lifecycle ----------------------------------------------------------------------

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def stop(self) -> None:
        self._stopping.set()

    async def _wait_for_redis(self) -> None:
        """Start even if Redis isn't up yet (e.g. both containers starting): retry with backoff."""
        delay = 1.0
        while not self._stopping.is_set():
            try:
                await self.ensure_group()
                return
            except RedisError:
                logger.warning("ingestion_worker_waiting_for_redis", extra={"retry_in_s": delay})
                await self._sleep(delay)
                delay = min(delay * 2, 30.0)

    async def run(self) -> None:
        await self._wait_for_redis()
        if self._stopping.is_set():
            return
        logger.info(
            "ingestion_worker_started",
            extra={
                "consumer": self.consumer,
                "concurrency": self._concurrency,
                "job_timeout_ms": self._job_timeout_ms,
            },
        )
        consumers = [
            asyncio.create_task(self._consume(n), name=f"consume-{n}") for n in range(self._concurrency)
        ]
        background = [asyncio.create_task(self._heartbeat(), name="heartbeat")]
        if self._run_maintenance:
            background.append(asyncio.create_task(self._maintenance(), name="maintenance"))
        try:
            await self._stopping.wait()
        finally:
            self._stopping.set()
            # Consumers finish their current job; a blocked read returns within BLOCK_MS.
            _, still_running = await asyncio.wait(consumers, timeout=SHUTDOWN_GRACE_SECONDS)
            for task in still_running:
                task.cancel()
            _, stuck = await asyncio.wait([*consumers, *background], timeout=10)
            for task in stuck:
                task.cancel()
            with contextlib.suppress(RedisError):
                await self._redis.delete(WORKER_HEARTBEAT_PREFIX + self.consumer)
            logger.info(
                "ingestion_worker_stopped",
                extra={
                    "consumer": self.consumer,
                    "jobs_done": self.jobs_done,
                    "unfinished": len(still_running),
                },
            )

    # --- consuming ------------------------------------------------------------------------

    async def _consume(self, slot: int) -> None:
        while not self._stopping.is_set():
            try:
                job = await self._next_job()
                if job is not None:
                    await self._handle(*job)
            except asyncio.CancelledError:
                raise
            except RedisError:
                logger.warning("ingestion_worker_redis_error", extra={"slot": slot}, exc_info=True)
                await self._sleep(2)
            except Exception:
                logger.exception("ingestion_worker_error", extra={"slot": slot})
                await self._sleep(2)

    async def _next_job(self) -> tuple[str, dict[bytes, bytes]] | None:
        # First, jobs abandoned by dead workers; then new ones.
        _, claimed, _ = await self._redis.xautoclaim(
            STREAM, GROUP, self.consumer, min_idle_time=self._job_timeout_ms, start_id="0-0", count=1
        )
        if claimed:
            entry_id, fields = claimed[0]
            logger.warning("ingestion_job_reclaimed", extra={"job_id": entry_id.decode()})
            return entry_id.decode(), fields
        if self._stopping.is_set():
            return None
        response = await self._redis.xreadgroup(
            GROUP, self.consumer, {STREAM: ">"}, count=1, block=self._block_ms
        )
        if not response:
            return None
        entry_id, fields = response[0][1][0]
        return entry_id.decode(), fields

    async def _deliveries(self, entry_id: str) -> int:
        pending = await self._redis.xpending_range(STREAM, GROUP, min=entry_id, max=entry_id, count=1)
        return int(pending[0]["times_delivered"]) if pending else 1

    async def _finish(self, entry_id: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xack(STREAM, GROUP, entry_id)
            pipe.xdel(STREAM, entry_id)
            await pipe.execute()

    async def _handle(self, entry_id: str, fields: dict[bytes, bytes]) -> None:
        try:
            document_id = uuid.UUID(fields[b"document_id"].decode())
            kind = fields.get(b"kind", INGEST.encode()).decode()
            processor = self._processors[kind]
        except (KeyError, ValueError):
            logger.error("ingestion_job_invalid", extra={"job_id": entry_id})
            await self._finish(entry_id)
            return
        log = {"document_id": str(document_id), "job_id": entry_id, "kind": kind}
        request_id_ctx.set(f"job-{entry_id}")

        deliveries = await self._deliveries(entry_id)
        if deliveries > self._max_deliveries:
            await self._dead_letter(entry_id, document_id, deliveries, kind)
            return

        token = self.consumer
        lock = processing_lock(document_id, kind)
        if not await self._redis.set(lock, token, nx=True, px=self._job_timeout_ms):
            # Another worker is processing this document right now; this is a duplicate job.
            logger.info("ingestion_job_duplicate_skipped", extra=log)
            await self._finish(entry_id)
            return
        if kind == INGEST:
            await self._redis.delete(queued_marker(document_id))

        self._active[entry_id] = document_id
        job_over = asyncio.Event()
        keep_alive = asyncio.create_task(self._keep_alive(entry_id, lock, token, job_over))
        started = time.perf_counter()
        try:
            await processor(document_id)
        except Exception:
            # process_document records document failures itself; reaching here means the
            # database or storage was unavailable. Leave the job pending: it is retried after
            # the job timeout and counts towards the delivery limit.
            logger.exception("ingestion_job_error", extra={**log, "deliveries": deliveries})
            return
        finally:
            job_over.set()  # an event, not cancel(): see _keep_alive
            await asyncio.gather(keep_alive, return_exceptions=True)
            self._active.pop(entry_id, None)
            with contextlib.suppress(RedisError):
                await self._redis.eval(_RELEASE_LOCK, 1, lock, token)
        await self._finish(entry_id)
        self.jobs_done += 1
        logger.info(
            "ingestion_job_done",
            extra={
                **log,
                "deliveries": deliveries,
                "job_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    async def _keep_alive(self, entry_id: str, lock: str, token: str, job_over: asyncio.Event) -> None:
        """Reset the job's idle time and extend the document lock until `job_over` is set.

        Background loops end on an event rather than task.cancel(): on Python 3.11 a cancel
        that lands inside redis-py's internal timeout can surface as a redis TimeoutError,
        which the `except RedisError` below would swallow, leaving the loop running."""
        interval = self._job_timeout_ms / 3000
        while not await _wait(job_over, interval):
            try:
                # JUSTID: claim without incrementing the delivery count.
                await self._redis.xclaim(STREAM, GROUP, self.consumer, 0, [entry_id], justid=True)
                await self._redis.eval(_EXTEND_LOCK, 1, lock, token, self._job_timeout_ms)
            except RedisError:
                logger.warning("ingestion_heartbeat_failed", extra={"job_id": entry_id}, exc_info=True)

    async def _dead_letter(
        self, entry_id: str, document_id: uuid.UUID, deliveries: int, kind: str = INGEST
    ) -> None:
        logger.error(
            "ingestion_job_dead_lettered",
            extra={
                "document_id": str(document_id),
                "job_id": entry_id,
                "deliveries": deliveries,
                "kind": kind,
            },
        )
        await self._redis.xadd(
            DEAD_LETTER_STREAM,
            {
                "document_id": str(document_id),
                "job_id": entry_id,
                "deliveries": deliveries,
                "kind": kind,
                "at": time.time(),
            },
            maxlen=DEAD_LETTER_MAX_LENGTH,
            approximate=True,
        )
        if kind == INSIGHTS:
            await insights_service.fail_abandoned(document_id, deliveries - 1)
            await self._finish(entry_id)
            return
        async with SessionLocal() as db:
            await db.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status.in_([DocumentStatus.UPLOADED, DocumentStatus.PROCESSING]),
                )
                .values(
                    status=DocumentStatus.FAILED,
                    error_message=POISON_MESSAGE.format(attempts=deliveries - 1),
                    processed_at=datetime.now(UTC),
                )
            )
            await db.commit()
        await self._finish(entry_id)

    # --- background --------------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        """Advertise this worker (the UI warns when documents wait and no worker is alive)."""
        while not self._stopping.is_set():
            state = {"started": self._started, "jobs_done": self.jobs_done, "active": len(self._active)}
            with contextlib.suppress(RedisError):
                await self._redis.set(
                    WORKER_HEARTBEAT_PREFIX + self.consumer, json.dumps(state), ex=HEARTBEAT_TTL_SECONDS
                )
            await self._sleep(HEARTBEAT_TTL_SECONDS / 3)

    async def _maintenance(self) -> None:
        while not self._stopping.is_set():
            try:
                await maintenance.run_maintenance(self._redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("maintenance_failed")
            await self._sleep(self._maintenance_interval)

    async def _sleep(self, seconds: float) -> None:
        await _wait(self._stopping, seconds)


async def _wait(event: asyncio.Event, seconds: float) -> bool:
    """Wait up to `seconds` for `event`; True if it is set."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), seconds)
    return event.is_set()


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    # The blocking read waits up to BLOCK_MS; the socket timeout must be longer.
    redis = create_redis(socket_timeout=BLOCK_MS / 1000 + 10)
    worker = IngestionWorker(redis)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:  # Windows: fall back to signal.signal
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(worker.stop))

    provider = get_embedding_provider()
    if hasattr(provider, "warm_up"):  # a model on this machine: load it before taking jobs
        await asyncio.to_thread(provider.warm_up)
    try:
        await worker.run()
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
