"""Database migration utilities for schema updates."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def migrate_jobs_database(engine: AsyncEngine) -> None:
    """
    Apply migrations to the jobs database.
    
    This handles schema updates for existing databases that were created
    before new columns were added to the models.
    """
    async with engine.begin() as conn:
        # Check if background_jobs table exists
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        tables = await conn.run_sync(lambda sync_conn: inspector.get_table_names())
        
        if "background_jobs" not in tables:
            logger.info("background_jobs table does not exist yet, skipping migration")
            return
        
        # Check if ontology_id column exists
        columns = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("background_jobs")
        )
        column_names = [col["name"] for col in columns]
        
        if "ontology_id" not in column_names:
            logger.info("Adding ontology_id column to background_jobs table")
            await conn.execute(
                text(
                    "ALTER TABLE background_jobs ADD COLUMN ontology_id INTEGER DEFAULT NULL"
                )
            )
            # Create index for the new column
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_background_jobs_ontology_id ON background_jobs (ontology_id)"
                )
            )
            logger.info("Successfully added ontology_id column")
        else:
            logger.debug("ontology_id column already exists, skipping migration")
        
        # Check if duration_seconds column exists
        if "duration_seconds" not in column_names:
            logger.info("Adding duration_seconds column to background_jobs table")
            await conn.execute(
                text(
                    "ALTER TABLE background_jobs ADD COLUMN duration_seconds FLOAT DEFAULT NULL"
                )
            )
            logger.info("Successfully added duration_seconds column")
        else:
            logger.debug("duration_seconds column already exists, skipping migration")
