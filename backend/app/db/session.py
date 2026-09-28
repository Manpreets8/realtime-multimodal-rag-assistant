"""Async SQLAlchemy engine and session factory."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    connect_args={"server_settings": {"statement_timeout": str(_settings.db_statement_timeout_ms)}},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database(session: AsyncSession) -> dict[str, bool]:
    """Return connectivity and pgvector availability. Raises on connection failure."""
    await session.execute(text("SELECT 1"))
    has_vector = await session.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
    )
    return {"database": True, "pgvector": bool(has_vector)}
