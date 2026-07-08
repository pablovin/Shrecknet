from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import logging
import time
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession
from neo4j.exceptions import Neo4jError

from app.core.config_store import get_settings

logger = logging.getLogger(__name__)

_driver: AsyncDriver | None = None
_driver_loop: asyncio.AbstractEventLoop | None = None


def get_driver() -> AsyncDriver:
    """
    Get the Neo4j async driver, ensuring it's bound to the current event loop.
    
    This function is event-loop-aware: if called from different event loops,
    it will close the old driver and create a new one for the current loop.
    This prevents "attached to a different loop" errors in Celery tasks.
    
    Returns:
        AsyncDriver instance bound to the current event loop
    """
    global _driver, _driver_loop
    
    # Try to get the current event loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop is running, we'll create the driver without loop tracking
        current_loop = None
    
    # Check if we need to recreate the driver
    # This happens when:
    # 1. Driver doesn't exist
    # 2. We're in a different event loop than when driver was created
    if _driver is None or (_driver_loop is not None and current_loop is not _driver_loop):
        # Reset driver if switching loops
        if _driver is not None and current_loop is not _driver_loop:
            # We can't await the close here since this is a sync function
            # The driver will be garbage collected
            _driver = None
            _driver_loop = None
        
        settings = get_settings()
        _driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_lifetime=3600,
        )
        _driver_loop = current_loop
    
    return _driver


async def close_driver() -> None:
    """Close the Neo4j driver and reset loop tracking."""
    global _driver, _driver_loop
    if _driver is not None:
        await _driver.close()
        _driver = None
        _driver_loop = None


async def get_neo4j_session() -> AsyncGenerator[AsyncSession, None]:
    driver = get_driver()
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        yield session


async def get_optional_neo4j_session() -> AsyncGenerator[AsyncSession | None, None]:
    try:
        driver = get_driver()
        settings = get_settings()
        async with driver.session(database=settings.neo4j_database) as session:
            yield session
    except Neo4jError:
        yield None
    except Exception:
        yield None


async def ensure_temporal_graph_constraints(session: AsyncSession) -> None:
    """Create idempotent constraints and indexes for Scene/Milestone temporal model."""
    statements = [
        """
        CREATE CONSTRAINT scene_id_unique IF NOT EXISTS
        FOR (scene:Scene)
        REQUIRE scene.id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT milestone_id_unique IF NOT EXISTS
        FOR (milestone:Milestone)
        REQUIRE milestone.id IS UNIQUE
        """,
        """
        CREATE INDEX scene_instance_ontology_idx IF NOT EXISTS
        FOR (scene:Scene)
        ON (scene.instance_id, scene.ontology_id)
        """,
        """
        CREATE INDEX milestone_scene_temporal_idx IF NOT EXISTS
        FOR (milestone:Milestone)
        ON (milestone.scene_id, milestone.temporal_type, milestone.boundary_type)
        """,
    ]
    for statement in statements:
        await session.run(statement)


def _index_matches(
    index: dict[str, Any] | None,
    *,
    expected_type: str,
    expected_labels: set[str],
    expected_properties: set[str],
    expected_vector_dimension: int | None = None,
) -> bool:
    if not index:
        return False
    index_type = str(index.get("type") or "").upper()
    if expected_type.upper() not in index_type:
        return False
    labels = {
        str(value)
        for value in (
            index.get("labelsOrTypes")
            or index.get("labels")
            or index.get("entityType")
            or []
        )
        if value is not None
    }
    properties = {
        str(value)
        for value in (index.get("properties") or index.get("propertyKeys") or [])
        if value is not None
    }
    if not (expected_labels <= labels and expected_properties <= properties):
        return False
    if expected_vector_dimension is not None:
        options = index.get("options") or {}
        index_config = options.get("indexConfig") if isinstance(options, dict) else {}
        if isinstance(index_config, dict):
            actual_dimension = index_config.get("vector.dimensions") or index_config.get(
                "`vector.dimensions`"
            )
            if actual_dimension is not None and int(actual_dimension) != int(expected_vector_dimension):
                return False
    return True


async def _collect_index_diagnostics(session: AsyncSession) -> dict[str, int]:
    query = """
    MATCH (chunk:EntityChunk)
    WITH count(chunk) AS entity_chunk_count,
         count { MATCH (missing_text:EntityChunk)
                 WHERE missing_text.text_chunk IS NULL OR trim(toString(missing_text.text_chunk)) = '' } AS chunks_missing_text_chunk,
         count { MATCH (missing_embedding:EntityChunk)
                 WHERE missing_embedding.text_embedding IS NULL } AS chunks_missing_text_embedding
    OPTIONAL MATCH (entity:EntityInstance)-[:HAS_CHUNK]->(:EntityChunk)
    WITH entity_chunk_count, chunks_missing_text_chunk, chunks_missing_text_embedding,
         count(DISTINCT entity) AS entity_parent_count
    OPTIONAL MATCH (scene:Scene)-[:HAS_CHUNK]->(:EntityChunk)
    WITH entity_chunk_count, chunks_missing_text_chunk, chunks_missing_text_embedding,
         entity_parent_count, count(DISTINCT scene) AS scene_parent_count
    OPTIONAL MATCH (milestone:Milestone)-[:HAS_CHUNK]->(:EntityChunk)
    RETURN entity_chunk_count,
           chunks_missing_text_chunk,
           chunks_missing_text_embedding,
           entity_parent_count,
           scene_parent_count,
           count(DISTINCT milestone) AS milestone_parent_count
    """
    result = await session.run(query)
    row = await result.single()
    if not row:
        return {
            "entity_chunk_count": 0,
            "chunks_missing_text_chunk": 0,
            "chunks_missing_text_embedding": 0,
            "entity_parent_count": 0,
            "scene_parent_count": 0,
            "milestone_parent_count": 0,
        }
    return {
        "entity_chunk_count": int(row.get("entity_chunk_count") or 0),
        "chunks_missing_text_chunk": int(row.get("chunks_missing_text_chunk") or 0),
        "chunks_missing_text_embedding": int(row.get("chunks_missing_text_embedding") or 0),
        "entity_parent_count": int(row.get("entity_parent_count") or 0),
        "scene_parent_count": int(row.get("scene_parent_count") or 0),
        "milestone_parent_count": int(row.get("milestone_parent_count") or 0),
    }


async def ensure_elder_hybrid_indexes(session: AsyncSession) -> dict[str, Any]:
    """Ensure Elder hybrid retrieval indexes once during startup only."""
    started = time.monotonic()
    settings = get_settings()
    database_name = settings.neo4j_database
    vector_index_name = "entity_chunk_vec_idx"
    fulltext_index_name = "entity_chunk_fulltext_idx"
    write_performed = False
    statuses = {
        "vector_index": "skipped",
        "fulltext_index": "skipped",
    }

    start_msg = (
        "elder_hybrid_index_migration_start "
        f"database={database_name} vector_index={vector_index_name} "
        f"fulltext_index={fulltext_index_name}"
    )
    print(f"[STARTUP] {start_msg}", flush=True)
    logger.info(
        "elder_hybrid_index_migration_start database=%s vector_index=%s fulltext_index=%s",
        database_name,
        vector_index_name,
        fulltext_index_name,
    )

    result = await session.run(
        "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options RETURN name, type, labelsOrTypes, properties, options"
    )
    rows = await result.data()
    indexes = {str(row.get("name")): row for row in rows if row.get("name")}

    vector_index = indexes.get(vector_index_name)
    if _index_matches(
        vector_index,
        expected_type="VECTOR",
        expected_labels={"EntityChunk"},
        expected_properties={"text_embedding"},
        expected_vector_dimension=settings.embedding_dimension,
    ):
        statuses["vector_index"] = "present"
    else:
        try:
            print(
                f"[STARTUP] elder_hybrid_index_create_start index={vector_index_name} type=VECTOR",
                flush=True,
            )
            create_vector = f"""
            CREATE VECTOR INDEX {vector_index_name} IF NOT EXISTS
            FOR (c:EntityChunk) ON (c.text_embedding)
            OPTIONS {{
                indexConfig: {{
                    `vector.dimensions`: {settings.embedding_dimension},
                    `vector.similarity_function`: 'cosine'
                }}
            }}
            """
            await (await session.run(create_vector)).consume()
            statuses["vector_index"] = "created"
            write_performed = True
            print(
                f"[STARTUP] elder_hybrid_index_create_done index={vector_index_name} status=created",
                flush=True,
            )
        except Exception as exc:
            if "EquivalentSchemaRuleAlreadyExists" in str(exc) or "already exists" in str(exc).lower():
                statuses["vector_index"] = "present"
            else:
                statuses["vector_index"] = "failed"
                logger.exception(
                    "elder_hybrid_index_vector_failed database=%s index=%s error=%s",
                    database_name,
                    vector_index_name,
                    exc,
                )

    fulltext_index = indexes.get(fulltext_index_name)
    if _index_matches(
        fulltext_index,
        expected_type="FULLTEXT",
        expected_labels={"EntityChunk"},
        expected_properties={"text_chunk"},
    ):
        statuses["fulltext_index"] = "present"
    else:
        try:
            print(
                f"[STARTUP] elder_hybrid_index_create_start index={fulltext_index_name} type=FULLTEXT",
                flush=True,
            )
            create_fulltext = f"""
            CREATE FULLTEXT INDEX {fulltext_index_name} IF NOT EXISTS
            FOR (c:EntityChunk) ON EACH [c.text_chunk]
            """
            await (await session.run(create_fulltext)).consume()
            statuses["fulltext_index"] = "created"
            write_performed = True
            print(
                f"[STARTUP] elder_hybrid_index_create_done index={fulltext_index_name} status=created",
                flush=True,
            )
        except Exception as exc:
            if "EquivalentSchemaRuleAlreadyExists" in str(exc) or "already exists" in str(exc).lower():
                statuses["fulltext_index"] = "present"
            else:
                statuses["fulltext_index"] = "failed"
                logger.exception(
                    "elder_hybrid_index_fulltext_failed database=%s index=%s error=%s",
                    database_name,
                    fulltext_index_name,
                    exc,
                )

    diagnostics = {
        "entity_chunk_count": 0,
        "chunks_missing_text_chunk": 0,
        "chunks_missing_text_embedding": 0,
        "entity_parent_count": 0,
        "scene_parent_count": 0,
        "milestone_parent_count": 0,
    }
    diagnostics_collected = False
    if write_performed:
        diagnostics = await _collect_index_diagnostics(session)
        diagnostics_collected = True
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    payload: dict[str, Any] = {
        "database": database_name,
        "vector_index": statuses["vector_index"],
        "fulltext_index": statuses["fulltext_index"],
        "write_performed": write_performed,
        "diagnostics_collected": diagnostics_collected,
        "duration_ms": duration_ms,
        **diagnostics,
    }
    print(
        "[STARTUP] elder_hybrid_index_migration_done "
        f"database={database_name} vector_index={statuses['vector_index']} "
        f"fulltext_index={statuses['fulltext_index']} write_performed={write_performed} "
        f"diagnostics_collected={diagnostics_collected} duration_ms={duration_ms:.2f}",
        flush=True,
    )
    logger.info(
        "elder_hybrid_index_migration_done database=%s vector_index=%s fulltext_index=%s "
        "write_performed=%s diagnostics_collected=%s entity_chunks=%d missing_text_chunk=%d missing_text_embedding=%d "
        "entity_parents=%d scene_parents=%d milestone_parents=%d duration_ms=%.2f",
        database_name,
        statuses["vector_index"],
        statuses["fulltext_index"],
        write_performed,
        diagnostics_collected,
        diagnostics["entity_chunk_count"],
        diagnostics["chunks_missing_text_chunk"],
        diagnostics["chunks_missing_text_embedding"],
        diagnostics["entity_parent_count"],
        diagnostics["scene_parent_count"],
        diagnostics["milestone_parent_count"],
        duration_ms,
    )
    return payload
