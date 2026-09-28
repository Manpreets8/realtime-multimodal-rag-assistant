"""Periodic housekeeping, run by ingestion workers.

With several workers running, a Redis lock (SET NX with an expiry) makes each round
run on exactly one of them. Every task is idempotent, so a round that dies halfway is
simply repeated next time.

- Queue documents that were never queued (Redis was down at upload time) or whose job
  was lost (Redis data lost while a document was processing).
- Delete images uploaded for a message that was never sent.
- Delete expired entries from the logout blocklist.
"""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.redis import redis_key
from app.db.session import SessionLocal
from app.models import Document, DocumentStatus, ImageUpload, RevokedToken
from app.services import image_service
from app.services.storage import LocalFileStorage, get_storage
from app.workers.job_queue import JobQueue, processing_lock, queued_marker

logger = logging.getLogger(__name__)

MAINTENANCE_LOCK = redis_key("maintenance", "lock")
# A document uploaded this long ago and still waiting without a queued job is re-queued.
UNQUEUED_GRACE = timedelta(seconds=30)
BATCH = 500


@dataclass(slots=True)
class MaintenanceReport:
    requeued_documents: int = 0
    deleted_images: int = 0
    purged_tokens: int = 0
    duration_ms: float = 0.0


async def requeue_stranded_documents(
    db: AsyncSession, redis: Redis, queue: JobQueue, *, job_timeout: timedelta
) -> int:
    now = datetime.now(UTC)
    candidates = list(
        await db.scalars(
            select(Document.id)
            .where(
                or_(
                    (Document.status == DocumentStatus.UPLOADED)
                    & (Document.created_at < now - UNQUEUED_GRACE),
                    (Document.status == DocumentStatus.PROCESSING)
                    & (Document.processing_started_at < now - job_timeout),
                )
            )
            .order_by(Document.created_at)
            .limit(BATCH)
        )
    )
    requeued = 0
    for document_id in candidates:
        # Still queued, or being processed right now: leave it alone.
        if await redis.exists(queued_marker(document_id), processing_lock(document_id)):
            continue
        if await queue.enqueue(document_id):
            requeued += 1
    if requeued:
        logger.warning("ingestion_requeued_stranded_documents", extra={"count": requeued})
    return requeued


async def delete_orphaned_images(
    db: AsyncSession, storage: LocalFileStorage, *, older_than: timedelta
) -> int:
    cutoff = datetime.now(UTC) - older_than
    rows = (
        await db.execute(
            select(ImageUpload.id, ImageUpload.storage_key)
            .where(ImageUpload.message_id.is_(None), ImageUpload.created_at < cutoff)
            .limit(BATCH)
        )
    ).all()
    if not rows:
        return 0
    # Re-check message_id in the DELETE: an image attached since the SELECT is kept.
    deleted_keys = list(
        await db.scalars(
            delete(ImageUpload)
            .where(ImageUpload.id.in_([row.id for row in rows]), ImageUpload.message_id.is_(None))
            .returning(ImageUpload.storage_key)
        )
    )
    await db.commit()
    await image_service.delete_files(storage, deleted_keys)  # after commit: at worst an orphaned file
    logger.info("orphaned_images_deleted", extra={"count": len(deleted_keys)})
    return len(deleted_keys)


async def purge_expired_revocations(db: AsyncSession) -> int:
    result = await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC)))
    await db.commit()
    return result.rowcount or 0


async def run_maintenance(
    redis: Redis,
    *,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    storage: LocalFileStorage | None = None,
    force: bool = False,
) -> MaintenanceReport | None:
    """One round, unless another worker ran one within the interval (returns None then)."""
    settings = get_settings()
    interval = settings.maintenance_interval_seconds
    # The lock expires shortly before the next round is due, so it also acts as the schedule.
    if not force and not await redis.set(MAINTENANCE_LOCK, "1", nx=True, ex=max(interval - 5, 5)):
        return None
    started = time.perf_counter()
    report = MaintenanceReport()
    async with session_factory() as db:
        report.requeued_documents = await requeue_stranded_documents(
            db,
            redis,
            JobQueue(redis),
            job_timeout=timedelta(seconds=settings.ingestion_job_timeout_seconds * 2),
        )
        report.deleted_images = await delete_orphaned_images(
            db, storage or get_storage(), older_than=timedelta(hours=settings.orphan_image_ttl_hours)
        )
        report.purged_tokens = await purge_expired_revocations(db)
    report.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "maintenance_completed",
        extra={
            "requeued_documents": report.requeued_documents,
            "deleted_images": report.deleted_images,
            "purged_tokens": report.purged_tokens,
            "duration_ms": report.duration_ms,
        },
    )
    return report
