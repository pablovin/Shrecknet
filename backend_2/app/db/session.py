from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

default_kwargs = {}

if settings.database_url.startswith("sqlite+"):
    # Prevent SQLite from complaining about already opened connections.
    default_kwargs["poolclass"] = NullPool

engine = create_async_engine(
    settings.database_url, echo=settings.debug, future=True, **default_kwargs
)

AsyncSessionMaker = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionMaker() as session:
        yield session
