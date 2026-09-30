import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

# Must run before any `app` import: the engine is created from DATABASE_URL at import time.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://rag:rag@localhost:5433/rag_assistant_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# A separate Redis database, flushed before every test (never the development database 0).
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "false")

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_client
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.llm import factory as llm_factory
from app.main import create_app
from app.multimodal import speech, tts
from app.rag import embeddings, reranking
from tests.fakes import HashingEmbeddingProvider, RecordingQueue, ScriptedLLM, ScriptedSpeech, ScriptedTTS

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PASSWORD = "correct-horse-42"


async def _ensure_database_exists(url: str) -> None:
    parsed = make_url(url)
    conn = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.host,
        port=parsed.port,
        database="postgres",
    )
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", parsed.database):
            await conn.execute(f'CREATE DATABASE "{parsed.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Create the test database once per run and apply all Alembic migrations to it."""
    asyncio.run(_ensure_database_exists(TEST_DATABASE_URL))
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")


@pytest.fixture
async def db(migrated_database: None) -> AsyncIterator[AsyncSession]:
    """A session on the migrated test database; every table is emptied after the test."""
    async with SessionLocal() as session:
        yield session
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
async def redis_available() -> bool:
    """Probed once, so unit tests without Redis don't each wait for a connection timeout."""
    try:
        await redis_client.get_redis().ping()
    except RedisError:
        return False
    return True


@pytest.fixture(autouse=True)
async def redis_db(request: pytest.FixtureRequest, redis_available: bool):
    """Every test starts with an empty Redis (rate-limit windows, caches, job streams).
    Unit tests (not marked `integration`) also run without Redis."""
    client = redis_client.get_redis()
    assert client.connection_pool.connection_kwargs.get("db") == 15, "tests must not use the dev Redis DB"
    if redis_available:
        await client.flushdb()
    elif request.node.get_closest_marker("integration"):
        pytest.fail("Redis is not reachable at TEST_REDIS_URL (docker compose up -d redis)")
    return client


@pytest.fixture(autouse=True)
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets an isolated storage root; nothing is written to the real documents/ folder."""
    directory = tmp_path / "uploads"
    monkeypatch.setattr(get_settings(), "upload_dir", directory)
    return directory


@pytest.fixture(autouse=True)
def embedder(monkeypatch: pytest.MonkeyPatch) -> HashingEmbeddingProvider:
    """Deterministic embeddings for every test; the real model has its own `model`-marked test."""
    provider = HashingEmbeddingProvider()
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda: provider)
    return provider


@pytest.fixture(autouse=True)
def reranker(monkeypatch: pytest.MonkeyPatch) -> reranking.PassthroughReranker:
    """Keep retrieval order by default so tests don't load the cross-encoder."""
    instance = reranking.PassthroughReranker()
    monkeypatch.setattr(reranking, "get_reranker", lambda: instance)
    return instance


@pytest.fixture(autouse=True)
def llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """No test ever calls the real Anthropic API."""
    instance = ScriptedLLM()
    monkeypatch.setattr(llm_factory, "get_llm_provider", lambda: instance)
    return instance


@pytest.fixture(autouse=True)
def stt(monkeypatch: pytest.MonkeyPatch) -> ScriptedSpeech:
    """Speech-to-text stand-in; the real Whisper model has its own `model`-marked tests."""
    instance = ScriptedSpeech()
    monkeypatch.setattr(speech, "get_speech_provider", lambda: instance)
    return instance


@pytest.fixture(autouse=True)
def tts_provider(monkeypatch: pytest.MonkeyPatch) -> ScriptedTTS:
    """Text-to-speech stand-in with a fresh audio cache; Piper has its own `model`-marked test."""
    instance = ScriptedTTS()
    monkeypatch.setattr(tts, "get_tts_provider", lambda: instance)
    tts._limiter.cache_clear()
    return instance


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test ever emails a real mail server, even when SMTP is configured in .env."""
    monkeypatch.setattr(get_settings(), "smtp_host", "")


@pytest.fixture
def ingestion_queue() -> RecordingQueue:
    return RecordingQueue()


@pytest.fixture
def app(ingestion_queue: RecordingQueue) -> FastAPI:
    application = create_app()
    # The lifespan (which starts the real queue) does not run under ASGITransport.
    application.state.ingestion_queue = ingestion_queue
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False lets us assert on the 500 response body
    # instead of having the transport re-raise the server-side exception.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


RegisterFn = Callable[..., Awaitable[dict]]


@pytest.fixture
def register_user(client: AsyncClient, db: AsyncSession) -> RegisterFn:
    """Register a user through the API and return the token response body."""

    async def _register(
        email: str = "user@example.com", password: str = DEFAULT_PASSWORD, **extra: str
    ) -> dict:
        response = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": password, **extra}
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _register


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
