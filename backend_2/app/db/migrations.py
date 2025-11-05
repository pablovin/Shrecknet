"""Database migration utilities for schema updates."""

from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession
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


async def migrate_architect_proposals(engine: AsyncEngine) -> None:
    """
    Apply migrations to the architect_proposals table for step 2 support.

    Adds columns needed for tracking validated proposals and generated entities.
    """
    async with engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        tables = await conn.run_sync(lambda sync_conn: inspector.get_table_names())

        # Migrate architect_analysis_runs table
        if "architect_analysis_runs" in tables:
            run_columns = await conn.run_sync(
                lambda sync_conn: inspector.get_columns("architect_analysis_runs")
            )
            run_column_names = [col["name"] for col in run_columns]

            # Add generation_job_id column
            if "generation_job_id" not in run_column_names:
                logger.info(
                    "Adding generation_job_id column to architect_analysis_runs table"
                )
                await conn.execute(
                    text(
                        "ALTER TABLE architect_analysis_runs ADD COLUMN generation_job_id INTEGER DEFAULT NULL"
                    )
                )
                logger.info("Successfully added generation_job_id column")

        if "architect_proposals" not in tables:
            logger.info(
                "architect_proposals table does not exist yet, skipping migration"
            )
            return

        # Check existing columns
        columns = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("architect_proposals")
        )
        column_names = [col["name"] for col in columns]

        # Add merged_into_proposal_id column
        if "merged_into_proposal_id" not in column_names:
            logger.info(
                "Adding merged_into_proposal_id column to architect_proposals table"
            )
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN merged_into_proposal_id VARCHAR(36) DEFAULT NULL"
                )
            )
            logger.info("Successfully added merged_into_proposal_id column")

        # Add corrected_alias column
        if "corrected_alias" not in column_names:
            logger.info("Adding corrected_alias column to architect_proposals table")
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN corrected_alias VARCHAR(255) DEFAULT NULL"
                )
            )
            logger.info("Successfully added corrected_alias column")

        # Add corrected_entity_definition_id column
        if "corrected_entity_definition_id" not in column_names:
            logger.info(
                "Adding corrected_entity_definition_id column to architect_proposals table"
            )
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN corrected_entity_definition_id INTEGER DEFAULT NULL"
                )
            )
            logger.info("Successfully added corrected_entity_definition_id column")

        # Add chunks column
        if "chunks" not in column_names:
            logger.info("Adding chunks column to architect_proposals table")
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN chunks TEXT DEFAULT NULL"
                )
            )
            logger.info("Successfully added chunks column")

        # Add generated_entity_instance_id column
        if "generated_entity_instance_id" not in column_names:
            logger.info(
                "Adding generated_entity_instance_id column to architect_proposals table"
            )
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN generated_entity_instance_id VARCHAR(64) DEFAULT NULL"
                )
            )
            logger.info("Successfully added generated_entity_instance_id column")

        # Add corrected_proposal_type column
        if "corrected_proposal_type" not in column_names:
            logger.info(
                "Adding corrected_proposal_type column to architect_proposals table"
            )
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN corrected_proposal_type VARCHAR(20) DEFAULT NULL"
                )
            )
            logger.info("Successfully added corrected_proposal_type column")

        # Add corrected_entity_instance_id column
        if "corrected_entity_instance_id" not in column_names:
            logger.info(
                "Adding corrected_entity_instance_id column to architect_proposals table"
            )
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN corrected_entity_instance_id VARCHAR(64) DEFAULT NULL"
                )
            )
            logger.info("Successfully added corrected_entity_instance_id column")


async def migrate_neo4j_embedding_properties(
    graph_session: AsyncNeo4jSession,
) -> dict[str, Any]:
    """
    Add embedding properties to existing EntityInstance nodes in Neo4j.

    This migration ensures all EntityInstance nodes have the is_embedded and
    last_embedded_date properties that are required for the embedding system.

    Nodes created before this migration won't have these properties set,
    which causes them to not appear in embedding stats.

    Args:
        graph_session: Neo4j async session

    Returns:
        Dictionary with migration statistics
    """
    logger.info("Starting Neo4j embedding properties migration")

    # Check if there are any nodes missing the embedding properties
    check_query = """
    MATCH (n:EntityInstance)
    WHERE n.is_embedded IS NULL
    RETURN count(n) AS count
    """

    result = await graph_session.run(check_query)
    record = await result.single()
    nodes_to_migrate = record["count"] if record else 0

    if nodes_to_migrate == 0:
        logger.info("No nodes need migration for embedding properties")
        return {
            "nodes_migrated": 0,
            "nodes_already_migrated": 0,
            "status": "success",
        }

    logger.info(f"Found {nodes_to_migrate} nodes to migrate")

    # Update nodes that don't have the embedding properties
    # Set is_embedded to false and last_embedded_date to null
    update_query = """
    MATCH (n:EntityInstance)
    WHERE n.is_embedded IS NULL
    SET n.is_embedded = false,
        n.last_embedded_date = null
    RETURN count(n) AS updated
    """

    result = await graph_session.run(update_query)
    record = await result.single()
    nodes_migrated = record["updated"] if record else 0

    logger.info(
        f"Successfully migrated {nodes_migrated} nodes with embedding properties"
    )

    return {
        "nodes_migrated": nodes_migrated,
        "status": "success",
    }


async def migrate_game_datetimes_to_brussels_timezone(engine: AsyncEngine) -> None:
    """
    Migrate existing game, session, and poll datetime fields to Brussels timezone.

    This migration ensures all datetime fields in games, game_sessions,
    game_session_polls, game_session_poll_options, game_session_poll_votes,
    and game_session_attendance tables have proper timezone information.

    For SQLite, datetime values are stored as strings. This migration converts
    naive datetime strings to timezone-aware strings in Brussels timezone (Europe/Brussels).

    Note: This migration uses +01:00 (CET - Central European Time) as a fixed offset
    for all existing records. This is a simplification that doesn't account for DST
    (Daylight Saving Time). Historical records created during CEST (summer time) will
    be marked with +01:00 instead of +02:00. This is acceptable for most use cases
    as it provides unambiguous timezone information. If precise DST handling is required,
    a more complex migration using pytz/zoneinfo would be needed.
    """
    # Define tables and their datetime columns that need migration
    # Using a dictionary for validation - only these tables/columns will be migrated
    VALID_TABLE_COLUMNS = {
        "games": ["created_at", "updated_at"],
        "game_sessions": ["scheduled_date", "created_at", "updated_at"],
        "game_session_polls": ["created_at"],
        "game_session_poll_options": ["proposed_start", "created_at"],
        "game_session_poll_votes": ["created_at"],
        "game_session_attendance": ["responded_at"],
    }

    async with engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        tables = await conn.run_sync(lambda sync_conn: inspector.get_table_names())

        logger.info(
            "Starting game datetime timezone migration to Brussels (Europe/Brussels)"
        )

        for table_name, columns in VALID_TABLE_COLUMNS.items():
            # Validate table exists
            if table_name not in tables:
                logger.debug(f"Table {table_name} does not exist, skipping")
                continue

            for column in columns:
                # Both table_name and column are from VALID_TABLE_COLUMNS hardcoded dict,
                # making them safe to use in SQL queries (not user input).
                # We cannot use SQLAlchemy's table()/column() constructs here because
                # we need string concatenation (|| '+01:00') on column values, which
                # requires raw SQL with the text() function.
                logger.info(f"Migrating {table_name}.{column} to Brussels timezone")

                # For SQLite, we need to update datetime strings to include timezone
                # Check if any rows exist without timezone info (no '+' or 'Z' in the datetime string)
                # Note: f-strings are safe here because table_name and column come from VALID_TABLE_COLUMNS
                check_query = text(
                    f"""
                    SELECT COUNT(*) as count 
                    FROM {table_name} 
                    WHERE {column} IS NOT NULL 
                    AND {column} NOT LIKE '%+%' 
                    AND {column} NOT LIKE '%Z'
                """
                )

                result = await conn.execute(check_query)
                row = result.fetchone()
                rows_to_update = row[0] if row else 0

                if rows_to_update == 0:
                    logger.debug(f"No rows to migrate in {table_name}.{column}")
                    continue

                logger.info(
                    f"Found {rows_to_update} rows to migrate in {table_name}.{column}"
                )

                # Update naive datetime strings to include Brussels timezone offset
                # Using +01:00 (CET) as a fixed offset for all records
                # Note: f-strings are safe here because table_name and column come from VALID_TABLE_COLUMNS
                update_query = text(
                    f"""
                    UPDATE {table_name}
                    SET {column} = {column} || '+01:00'
                    WHERE {column} IS NOT NULL 
                    AND {column} NOT LIKE '%+%' 
                    AND {column} NOT LIKE '%Z'
                """
                )

                await conn.execute(update_query)
                logger.info(
                    f"Successfully migrated {rows_to_update} rows in {table_name}.{column}"
                )

        logger.info("Game datetime timezone migration completed")
