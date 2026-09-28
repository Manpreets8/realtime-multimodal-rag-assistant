import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, ImageUpload, RevokedToken
from app.workers import maintenance
from app.workers.job_queue import STREAM, processing_lock, queued_marker
from tests.conftest import RegisterFn, bearer
from tests.images import PNG

pytestmark = pytest.mark.integration


@pytest.fixture
async def alice(register_user: RegisterFn) -> dict[str, str]:
    return bearer((await register_user(email="alice@example.com"))["access_token"])


async def upload(client: AsyncClient, headers: dict, kb: str, name: str) -> uuid.UUID:
    response = await client.post(
        "/api/v1/documents/upload",
        data={"knowledge_base_id": kb},
        files={"file": (name, f"Text of {name}.".encode())},
        headers=headers,
    )
    return uuid.UUID(response.json()["id"])


async def queued_ids(redis: Redis) -> list[uuid.UUID]:
    return [uuid.UUID(fields[b"document_id"].decode()) for _, fields in await redis.xrange(STREAM)]


async def test_stranded_documents_are_queued_again(
    client: AsyncClient, alice: dict, db: AsyncSession, redis_db: Redis
) -> None:
    kb = (await client.post("/api/v1/knowledge-bases", json={"name": "KB"}, headers=alice)).json()["id"]
    never_queued = await upload(client, alice, kb, "a.txt")  # Redis was down at upload time
    still_queued = await upload(client, alice, kb, "b.txt")  # job waiting in the stream
    lost_job = await upload(client, alice, kb, "c.txt")  # processing when Redis lost its data
    being_processed = await upload(client, alice, kb, "d.txt")  # a live worker holds the lock
    just_uploaded = await upload(client, alice, kb, "e.txt")  # the API is about to queue it
    done = await upload(client, alice, kb, "f.txt")
    old = datetime.now(UTC) - timedelta(hours=1)
    await db.execute(
        update(Document)
        .where(Document.id.in_([never_queued, still_queued, lost_job, being_processed, done]))
        .values(created_at=old)
    )
    await db.execute(
        update(Document)
        .where(Document.id.in_([lost_job, being_processed]))
        .values(status=DocumentStatus.PROCESSING, processing_started_at=old)
    )
    await db.execute(update(Document).where(Document.id == done).values(status=DocumentStatus.COMPLETED))
    await db.commit()
    await redis_db.set(queued_marker(still_queued), "1")
    await redis_db.set(processing_lock(being_processed), "worker-1")

    report = await maintenance.run_maintenance(redis_db, force=True)

    assert report is not None and report.requeued_documents == 2
    assert sorted(await queued_ids(redis_db)) == sorted([never_queued, lost_job])
    assert just_uploaded not in await queued_ids(redis_db)  # within the grace period


async def test_orphaned_images_are_deleted_with_their_files(
    client: AsyncClient, alice: dict, db: AsyncSession, redis_db: Redis, upload_dir: Path
) -> None:
    async def image() -> uuid.UUID:
        response = await client.post(
            "/api/v1/images", files={"file": ("x.png", PNG, "image/png")}, headers=alice
        )
        return uuid.UUID(response.json()["id"])

    orphan, recent, sent = await image(), await image(), await image()
    await client.post(
        "/api/v1/chat", json={"message": "What is this?", "image_ids": [str(sent)]}, headers=alice
    )
    await db.execute(
        update(ImageUpload)
        .where(ImageUpload.id.in_([orphan, sent]))
        .values(created_at=datetime.now(UTC) - timedelta(days=2))
    )
    await db.commit()
    orphan_file = upload_dir / (
        await db.scalar(select(ImageUpload.storage_key).where(ImageUpload.id == orphan))
    )
    assert orphan_file.exists()

    report = await maintenance.run_maintenance(redis_db, force=True)

    assert report is not None and report.deleted_images == 1
    remaining = set(await db.scalars(select(ImageUpload.id).execution_options(populate_existing=True)))
    assert remaining == {recent, sent}  # unsent but recent: the user may still send it
    assert not orphan_file.exists()


async def test_expired_revocations_are_purged(db: AsyncSession, redis_db: Redis) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            RevokedToken(jti="expired", expires_at=now - timedelta(minutes=1)),
            RevokedToken(jti="valid", expires_at=now + timedelta(minutes=30)),
        ]
    )
    await db.commit()

    report = await maintenance.run_maintenance(redis_db, force=True)

    assert report is not None and report.purged_tokens == 1
    assert list(await db.scalars(select(RevokedToken.jti))) == ["valid"]


async def test_one_round_per_interval_across_workers(redis_db: Redis) -> None:
    first = await maintenance.run_maintenance(redis_db)
    second = await maintenance.run_maintenance(redis_db)  # another worker, same interval

    assert first is not None
    assert second is None
    assert await redis_db.ttl(maintenance.MAINTENANCE_LOCK) > 0
