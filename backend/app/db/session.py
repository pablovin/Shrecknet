from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config_store import get_settings

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None
_engine_key: tuple[str, bool] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_maker, _engine_key
    settings = get_settings()
    engine_key = (settings.database_url, settings.debug)
    if _engine is None or _engine_key != engine_key:
        default_kwargs = {}
        if settings.database_url.startswith("sqlite+"):
            # Prevent SQLite from complaining about already opened connections.
            default_kwargs["poolclass"] = NullPool
        _engine = create_async_engine(
            settings.database_url, echo=settings.debug, future=True, **default_kwargs
        )
        _session_maker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
        _engine_key = engine_key
    return _engine


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    if _session_maker is None:
        get_engine()
    if _session_maker is None:
        raise RuntimeError("Session maker unavailable")
    return _session_maker


def AsyncSessionMaker() -> AsyncSession:
    return _get_session_maker()()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with _get_session_maker()() as session:
        yield session
