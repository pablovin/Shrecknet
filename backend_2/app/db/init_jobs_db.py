"""Initialize the jobs database."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from app.db.migrations import migrate_architect_proposals, migrate_jobs_database
from app.models.background_job import (
    BackgroundJob,
)  # noqa: F401 - imported for registration


async def init_jobs_db(engine: AsyncEngine) -> None:
    """Create all tables in the jobs database."""
    # First, create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Then, run migrations to update existing tables
    await migrate_jobs_database(engine)
    await migrate_architect_proposals(engine)
