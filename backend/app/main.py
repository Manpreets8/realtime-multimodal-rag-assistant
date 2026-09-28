import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.body_limit import MaxBodySizeMiddleware
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.core.redis import close_redis, get_redis
from app.db.session import engine
from app.models import EMBEDDING_COLUMN_DIMENSIONS
from app.multimodal.speech import LocalWhisperProvider, get_speech_provider
from app.multimodal.tts import LocalPiperProvider, get_tts_provider
from app.rag.embeddings import LocalEmbeddingProvider, get_embedding_provider, validate_embedding_settings
from app.rag.reranking import LocalCrossEncoderReranker, get_reranker
from app.workers.job_queue import JobQueue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Fail fast on a misconfigured embedding provider instead of failing every upload later.
    validate_embedding_settings(settings, EMBEDDING_COLUMN_DIMENSIONS)
    logger.info(
        "application_startup",
        extra={
            "environment": settings.environment.value,
            "version": settings.app_version,
            "embedding_provider": settings.embedding_provider.value,
            "embedding_model": settings.embedding_model,
        },
    )

    # Ingestion runs in worker processes (python -m app.workers.ingestion_worker); the API only queues.
    app.state.ingestion_queue = JobQueue(get_redis())

    # Load local models in the background so the first request doesn't pay for it.
    warm_ups = [
        asyncio.create_task(asyncio.to_thread(model.warm_up))
        for model in (get_embedding_provider(), get_reranker(), get_speech_provider(), get_tts_provider())
        if isinstance(
            model,
            LocalEmbeddingProvider | LocalCrossEncoderReranker | LocalWhisperProvider | LocalPiperProvider,
        )
    ]
    if settings.llm_api_key is None:
        logger.warning("llm_not_configured", extra={"hint": "Set LLM_API_KEY to enable answers"})

    yield

    for task in warm_ups:
        task.cancel()
    await close_redis()
    await engine.dispose()
    logger.info("application_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    )

    # Middleware added later wraps earlier ones. Order (outer -> inner):
    # RequestContext -> SecurityHeaders -> CORS -> MaxBodySize, so even 413 responses carry
    # CORS and security headers.
    app.add_middleware(MaxBodySizeMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Bearer tokens, no cookies: credentialed cross-origin requests are never needed.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms", "X-Speech-Truncated", "X-Speech-Voice"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    # Added last so it is outermost: every request (incl. CORS preflight) gets an ID.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
