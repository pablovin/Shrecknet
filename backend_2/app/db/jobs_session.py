"""Database session management for background jobs database."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

# Use a separate database URL for jobs
jobs_database_url = settings.jobs_database_url

jobs_kwargs = {}

if jobs_database_url.startswith("sqlite+"):
    # Prevent SQLite from complaining about already opened connections.
    jobs_kwargs["poolclass"] = NullPool

jobs_engine = create_async_engine(
    jobs_database_url, echo=settings.debug, future=True, **jobs_kwargs
)

JobsSessionMaker = async_sessionmaker(
    jobs_engine, expire_on_commit=False, class_=AsyncSession
)


async def get_jobs_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting a jobs database session."""
    async with JobsSessionMaker() as session:
        yield session
