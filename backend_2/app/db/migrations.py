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
        
        if "architect_proposals" not in tables:
            logger.info("architect_proposals table does not exist yet, skipping migration")
            return
        
        # Check existing columns
        columns = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("architect_proposals")
        )
        column_names = [col["name"] for col in columns]
        
        # Add merged_into_proposal_id column
        if "merged_into_proposal_id" not in column_names:
            logger.info("Adding merged_into_proposal_id column to architect_proposals table")
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
            logger.info("Adding corrected_entity_definition_id column to architect_proposals table")
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
            logger.info("Adding generated_entity_instance_id column to architect_proposals table")
            await conn.execute(
                text(
                    "ALTER TABLE architect_proposals ADD COLUMN generated_entity_instance_id VARCHAR(64) DEFAULT NULL"
                )
            )
            logger.info("Successfully added generated_entity_instance_id column")


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
