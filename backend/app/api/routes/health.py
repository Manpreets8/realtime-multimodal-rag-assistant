import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_client
from app.core.config import Settings, get_settings
from app.db.session import check_database, get_db
from app.workers.job_queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]
    app: str
    version: str
    environment: str


class ServiceStatus(BaseModel):
    """Reported, but not part of readiness: the API keeps serving without them (rate limits fail
    open, uploads wait in the database until a worker queues them)."""

    redis: bool
    ingestion_workers: int = Field(description="Worker processes with a recent heartbeat")
    ingestion_waiting: int = Field(description="Jobs queued but not started")


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]
    services: ServiceStatus | None = None


@router.get("", response_model=LivenessResponse, summary="Liveness probe")
async def liveness(settings: Annotated[Settings, Depends(get_settings)]) -> LivenessResponse:
    return LivenessResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment.value,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe (database + pgvector); also reports Redis and ingestion workers",
    responses={503: {"model": ReadinessResponse}},
)
async def readiness(db: Annotated[AsyncSession, Depends(get_db)]) -> ReadinessResponse | JSONResponse:
    try:
        checks = await check_database(db)
    except (SQLAlchemyError, OSError):
        logger.exception("readiness_database_check_failed")
        checks = {"database": False, "pgvector": False}

    services = await _services()
    if all(checks.values()):
        return ReadinessResponse(status="ready", checks=checks, services=services)
    body = ReadinessResponse(status="not_ready", checks=checks, services=services)
    return JSONResponse(body.model_dump(), status_code=503)


async def _services() -> ServiceStatus:
    try:
        redis = redis_client.get_redis()
        await redis.ping()
        stats = await JobQueue(redis).stats()
    except (RedisError, OSError):
        logger.warning("readiness_redis_check_failed", exc_info=True)
        return ServiceStatus(redis=False, ingestion_workers=0, ingestion_waiting=0)
    return ServiceStatus(redis=True, ingestion_workers=stats.workers, ingestion_waiting=stats.waiting)
