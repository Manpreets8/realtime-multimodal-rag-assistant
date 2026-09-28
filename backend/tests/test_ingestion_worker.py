"""The Redis-backed ingestion queue and worker, against a real Redis and PostgreSQL."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_client
from app.models import Document, DocumentStatus
from app.services.ingestion_service import process_document
from app.workers.ingestion_worker import IngestionWorker
from app.workers.job_queue import (
    DEAD_LETTER_STREAM,
    GROUP,
    STREAM,
    JobQueue,
    processing_lock,
    queued_marker,
)
from tests.conftest import RegisterFn, bearer

pytestmark = pytest.mark.integration

Processor = Callable[[uuid.UUID], Awaitable[object]]


@contextlib.asynccontextmanager
async def running(processor: Processor, **options) -> AsyncIterator[IngestionWorker]:
    options = {"block_ms": 50, "run_maintenance": False, "job_timeout_seconds": 5, **options}
    worker = IngestionWorker(redis_client.get_redis(), processor, **options)
    await worker.ensure_group()
    task = asyncio.create_task(worker.run())
    try:
        yield worker
    finally:
        worker.stop()
        await asyncio.wait_for(task, 10)


async def eventually(condition: Callable[[], Awaitable[bool]], limit: float = 5.0) -> None:
    """Poll `condition` (state in Redis/PostgreSQL, which has no event to wait on)."""
    async with asyncio.timeout(limit):
        while not await condition():  # noqa: ASYNC110 - polling external state
            await asyncio.sleep(0.02)


async def stream_empty(redis: Redis) -> bool:
    return await redis.xlen(STREAM) == 0


# --- the queue ----------------------------------------------------------------------------------


async def test_enqueue_adds_a_job_and_a_queued_marker(redis_db: Redis) -> None:
    document_id = uuid.uuid4()

    assert await JobQueue(redis_db).enqueue(document_id) is True

    entries = await redis_db.xrange(STREAM)
    assert [fields[b"document_id"].decode() for _, fields in entries] == [str(document_id)]
    assert await redis_db.ttl(queued_marker(document_id)) > 0


async def test_enqueue_reports_failure_when_redis_is_down() -> None:
    unreachable = Redis.from_url("redis://localhost:1/15", socket_connect_timeout=0.2)
    try:
        assert await JobQueue(unreachable).enqueue(uuid.uuid4()) is False
    finally:
        await unreachable.aclose()


async def test_upload_still_succeeds_when_redis_is_down(
    app: FastAPI, client: AsyncClient, register_user: RegisterFn
) -> None:
    unreachable = Redis.from_url("redis://localhost:1/15", socket_connect_timeout=0.2)
    app.state.ingestion_queue = JobQueue(unreachable)
    headers = bearer((await register_user())["access_token"])
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "KB"}, headers=headers)).json()["id"]
    try:
        response = await client.post(
            "/api/v1/documents/upload",
            data={"knowledge_base_id": kb},
            files={"file": ("a.txt", b"Some text.")},
            headers=headers,
        )
    finally:
        await unreachable.aclose()

    # Saved and waiting; the maintenance sweep queues it once Redis is back.
    assert response.status_code == 201
    assert response.json()["status"] == "uploaded"


# --- the worker ---------------------------------------------------------------------------------


async def test_end_to_end_upload_is_processed_by_a_worker(
    app: FastAPI, client: AsyncClient, register_user: RegisterFn, redis_db: Redis
) -> None:
    app.state.ingestion_queue = JobQueue(redis_db)
    headers = bearer((await register_user())["access_token"])
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "KB"}, headers=headers)).json()["id"]

    async with running(process_document):
        document = (
            await client.post(
                "/api/v1/documents/upload",
                data={"knowledge_base_id": kb},
                files={"file": ("guide.md", b"# Guide\n\nStep one. Step two.")},
                headers=headers,
            )
        ).json()

        async def completed() -> bool:
            detail = (await client.get(f"/api/v1/documents/{document['id']}", headers=headers)).json()
            return detail["status"] == "completed"

        await eventually(completed, limit=15)
        await eventually(lambda: stream_empty(redis_db))

    document_id = uuid.UUID(document["id"])
    assert not await redis_db.exists(queued_marker(document_id), processing_lock(document_id))
    assert await redis_db.xpending(STREAM, GROUP) == {"pending": 0, "min": None, "max": None, "consumers": []}


async def test_each_job_once_with_bounded_concurrency(redis_db: Redis) -> None:
    processed: list[uuid.UUID] = []
    running_now = peak = 0

    async def processor(document_id: uuid.UUID) -> None:
        nonlocal running_now, peak
        running_now += 1
        peak = max(peak, running_now)
        await asyncio.sleep(0.05)
        processed.append(document_id)
        running_now -= 1

    ids = [uuid.uuid4() for _ in range(6)]
    async with running(processor, concurrency=2):
        for document_id in ids:
            await JobQueue(redis_db).enqueue(document_id)
        await eventually(lambda: asyncio.sleep(0, len(processed) == len(ids)))

    assert sorted(processed) == sorted(ids)
    assert peak == 2


async def test_two_worker_processes_share_the_queue(redis_db: Redis) -> None:
    seen: dict[str, list[uuid.UUID]] = {"a": [], "b": []}

    def processor_for(name: str) -> Processor:
        async def processor(document_id: uuid.UUID) -> None:
            await asyncio.sleep(0.05)
            seen[name].append(document_id)

        return processor

    ids = [uuid.uuid4() for _ in range(8)]
    async with running(processor_for("a"), consumer="a"), running(processor_for("b"), consumer="b"):
        for document_id in ids:
            await JobQueue(redis_db).enqueue(document_id)
        await eventually(lambda: asyncio.sleep(0, len(seen["a"]) + len(seen["b"]) == len(ids)))

    assert sorted(seen["a"] + seen["b"]) == sorted(ids)  # every job exactly once
    assert seen["a"] and seen["b"]  # both did work


async def test_a_crashed_workers_job_is_taken_over(redis_db: Redis) -> None:
    """A worker read the job and died without acknowledging it."""
    document_id = uuid.uuid4()
    worker = IngestionWorker(redis_db, lambda _: asyncio.sleep(0))
    await worker.ensure_group()
    await JobQueue(redis_db).enqueue(document_id)
    await redis_db.xreadgroup(GROUP, "crashed-worker", {STREAM: ">"}, count=1)  # never acked
    processed: list[uuid.UUID] = []

    async def processor(document_id: uuid.UUID) -> None:
        processed.append(document_id)

    async with running(processor, job_timeout_seconds=0.3):
        await eventually(lambda: asyncio.sleep(0, processed == [document_id]))
        await eventually(lambda: stream_empty(redis_db))


async def test_heartbeats_stop_a_long_job_from_being_taken_over(redis_db: Redis) -> None:
    calls: list[str] = []

    def slow(name: str) -> Processor:
        async def processor(document_id: uuid.UUID) -> None:
            calls.append(name)
            await asyncio.sleep(1.2)  # four job timeouts

        return processor

    async with running(slow("first"), consumer="first", job_timeout_seconds=0.3):
        await JobQueue(redis_db).enqueue(uuid.uuid4())
        await eventually(lambda: asyncio.sleep(0, calls == ["first"]))
        async with running(slow("second"), consumer="second", job_timeout_seconds=0.3):
            await asyncio.sleep(1.5)
        await eventually(lambda: stream_empty(redis_db))

    assert calls == ["first"]


async def test_a_failing_job_is_retried_then_dead_lettered(
    client: AsyncClient, register_user: RegisterFn, redis_db: Redis, db: AsyncSession
) -> None:
    """Infrastructure errors (the processor raises) leave the job pending; it is retried
    after the job timeout, and after INGESTION_MAX_DELIVERIES the document is failed."""
    headers = bearer((await register_user())["access_token"])
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "KB"}, headers=headers)).json()["id"]
    uploaded = await client.post(
        "/api/v1/documents/upload",
        data={"knowledge_base_id": kb},
        files={"file": ("a.txt", b"Some text.")},
        headers=headers,
    )
    document_id = uuid.UUID(uploaded.json()["id"])
    attempts: list[uuid.UUID] = []

    async def failing(document_id: uuid.UUID) -> None:
        attempts.append(document_id)
        raise ConnectionError("database unavailable")

    async with running(failing, job_timeout_seconds=0.2, max_deliveries=2):
        await JobQueue(redis_db).enqueue(document_id)
        await eventually(lambda: stream_empty(redis_db), limit=10)

    assert len(attempts) == 2
    dead = await redis_db.xrange(DEAD_LETTER_STREAM)
    assert [fields[b"document_id"].decode() for _, fields in dead] == [str(document_id)]
    status, message = (
        await db.execute(select(Document.status, Document.error_message).where(Document.id == document_id))
    ).one()
    assert status is DocumentStatus.FAILED
    assert message.startswith("Processing was stopped after 2 attempts")


async def test_a_transient_failure_succeeds_on_retry(redis_db: Redis) -> None:
    attempts = 0

    async def flaky(document_id: uuid.UUID) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("database restarting")

    async with running(flaky, job_timeout_seconds=0.2):
        await JobQueue(redis_db).enqueue(uuid.uuid4())
        await eventually(lambda: stream_empty(redis_db))

    assert attempts == 2
    assert await redis_db.xlen(DEAD_LETTER_STREAM) == 0


async def test_a_duplicate_job_is_skipped_while_the_document_is_being_processed(redis_db: Redis) -> None:
    document_id = uuid.uuid4()
    calls = 0

    async def processor(document_id: uuid.UUID) -> None:
        nonlocal calls
        calls += 1

    await redis_db.set(processing_lock(document_id), "another-worker", px=5000)
    async with running(processor):
        await JobQueue(redis_db).enqueue(document_id)
        await eventually(lambda: stream_empty(redis_db))

    assert calls == 0
    assert await redis_db.get(processing_lock(document_id)) == b"another-worker"  # not ours to release


async def test_stopping_finishes_the_current_job(redis_db: Redis) -> None:
    started, finished = asyncio.Event(), asyncio.Event()

    async def processor(document_id: uuid.UUID) -> None:
        started.set()
        await asyncio.sleep(0.3)
        finished.set()

    worker = IngestionWorker(redis_db, processor, block_ms=50, run_maintenance=False)
    task = asyncio.create_task(worker.run())
    await JobQueue(redis_db).enqueue(uuid.uuid4())
    await asyncio.wait_for(started.wait(), 5)

    worker.stop()
    await asyncio.wait_for(task, 5)

    assert finished.is_set()
    assert await stream_empty(redis_db)
    assert worker.jobs_done == 1


async def test_workers_advertise_themselves_while_running(redis_db: Redis) -> None:
    async with running(lambda _: asyncio.sleep(0), consumer="w1"):
        await eventually(lambda: JobQueue(redis_db).stats())  # heartbeat written at start
        stats = await JobQueue(redis_db).stats()
        assert stats.workers == 1

    assert (await JobQueue(redis_db).stats()).workers == 0  # removed on shutdown


async def test_the_worker_waits_for_redis_instead_of_exiting(monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    worker = IngestionWorker(
        redis_client.get_redis(), lambda _: asyncio.sleep(0), block_ms=50, run_maintenance=False
    )
    attempts = 0
    real_ensure_group = worker.ensure_group

    async def flaky_ensure_group() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RedisConnectionError("Redis is starting")
        await real_ensure_group()

    monkeypatch.setattr(worker, "ensure_group", flaky_ensure_group)
    monkeypatch.setattr(worker, "_sleep", lambda seconds: asyncio.sleep(0))
    task = asyncio.create_task(worker.run())
    await eventually(lambda: asyncio.sleep(0, attempts >= 3))
    worker.stop()
    await asyncio.wait_for(task, 10)

    assert attempts == 3  # kept trying, then started normally
