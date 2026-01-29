"""Database session management for background jobs database."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config_store import get_settings

_jobs_engine: AsyncEngine | None = None
_jobs_session_maker: async_sessionmaker[AsyncSession] | None = None
_jobs_engine_key: tuple[str, bool] | None = None


def get_jobs_engine() -> AsyncEngine:
    global _jobs_engine, _jobs_session_maker, _jobs_engine_key
    settings = get_settings()
    engine_key = (settings.jobs_database_url, settings.debug)
    if _jobs_engine is None or _jobs_engine_key != engine_key:
        jobs_kwargs = {}
        if settings.jobs_database_url.startswith("sqlite+"):
            # Prevent SQLite from complaining about already opened connections.
            jobs_kwargs["poolclass"] = NullPool
        _jobs_engine = create_async_engine(
            settings.jobs_database_url, echo=settings.debug, future=True, **jobs_kwargs
        )
        _jobs_session_maker = async_sessionmaker(
            _jobs_engine, expire_on_commit=False, class_=AsyncSession
        )
        _jobs_engine_key = engine_key
    return _jobs_engine


def _get_jobs_session_maker() -> async_sessionmaker[AsyncSession]:
    if _jobs_session_maker is None:
        get_jobs_engine()
    if _jobs_session_maker is None:
        raise RuntimeError("Jobs session maker unavailable")
    return _jobs_session_maker


def JobsSessionMaker() -> AsyncSession:
    return _get_jobs_session_maker()()


async def get_jobs_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting a jobs database session."""
    async with _get_jobs_session_maker() as session:
        yield session
