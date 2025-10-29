"""Initialize the jobs database."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from app.models.background_job import BackgroundJob  # noqa: F401 - imported for registration


async def init_jobs_db(engine: AsyncEngine) -> None:
    """Create all tables in the jobs database."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
