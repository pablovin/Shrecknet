"""Offline one-shot migration to event-centric graph model."""

from __future__ import annotations

from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession


async def _exec(session: AsyncNeo4jSession, query: str, **params: Any) -> list[dict[str, Any]]:
    result = await session.run(query, **params)
    return await result.data()


async def migrate_event_centric_schema(
    graph_session: AsyncNeo4jSession,
    *,
    fail_on_temporal_cycle: bool = True,
) -> dict[str, Any]:
    """Transform TimelineEvent graph data into canonical Event graph data.

    Expected to run in an offline maintenance window.
    """

    # Constraints / indexes
    await _exec(
        graph_session,
        "CREATE CONSTRAINT event_event_id_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.event_id IS UNIQUE",
    )
    await _exec(
        graph_session,
        "CREATE CONSTRAINT entity_entity_instance_id_unique IF NOT EXISTS FOR (e:EntityInstance) REQUIRE e.entity_instance_id IS UNIQUE",
    )
    await _exec(
        graph_session,
        "CREATE CONSTRAINT ontology_instance_id_unique IF NOT EXISTS FOR (i:OntologyInstance) REQUIRE i.instance_id IS UNIQUE",
    )
    await _exec(
        graph_session,
        "CREATE INDEX event_ontology_instance_idx IF NOT EXISTS FOR (e:Event) ON (e.ontology_id, e.instance_id)",
    )
    await _exec(
        graph_session,
        "CREATE INDEX event_updated_at_idx IF NOT EXISTS FOR (e:Event) ON (e.updated_at)",
    )

    # Label + id migration (stable IDs)
    promoted = await _exec(
        graph_session,
        """
        MATCH (e:Event)
        SET e.event_id = coalesce(e.event_id, e.timeline_event_id, e.entity_instance_id)
        RETURN count(e) AS promoted
        """,
    )

    # Temporal property backfill -> explicit edges
    await _exec(
        graph_session,
        """
        MATCH (src:Event)
        WHERE src.before_event_id IS NOT NULL
        MATCH (dst:Event {event_id: src.before_event_id})
        MERGE (src)-[:AFTER]->(dst)
        MERGE (dst)-[:BEFORE]->(src)
        """,
    )
    await _exec(
        graph_session,
        """
        MATCH (src:Event)
        WHERE src.after_event_id IS NOT NULL
        MATCH (dst:Event {event_id: src.after_event_id})
        MERGE (src)-[:BEFORE]->(dst)
        MERGE (dst)-[:AFTER]->(src)
        """,
    )

    # Legacy edge conversion
    await _exec(
        graph_session,
        """
        MATCH (a:Event)-[r:AFTER]->(b:Event)
        DELETE r
        MERGE (a)-[:AFTER]->(b)
        MERGE (b)-[:BEFORE]->(a)
        """,
    )
    await _exec(
        graph_session,
        """
        MATCH (a:Event)-[r:BEFORE]->(b:Event)
        DELETE r
        MERGE (a)-[:BEFORE]->(b)
        MERGE (b)-[:AFTER]->(a)
        """,
    )

    # Convert event pointers / legacy arrays
    await _exec(
        graph_session,
        """
        MATCH (e:Event)
        SET e.involves_entity_ids = coalesce(e.involves_entity_ids, e.related_entity_ids, [])
        SET e.source_entity_id = coalesce(e.source_entity_id, e.created_from_entity_id, e.source_entity_id)
        """,
    )

    # Ensure relation list property for API projection
    await _exec(
        graph_session,
        """
        MATCH (e:Event)
        OPTIONAL MATCH (e)-[rel:BEFORE|AFTER|DERIVED_FROM|RELATED_TO]->(target:Event)
        WITH e, collect({relation_type: type(rel), target_event_id: target.event_id}) AS rels
        SET e.relations = [r IN rels WHERE r.target_event_id IS NOT NULL]
        """,
    )

    # Drop legacy properties
    await _exec(
        graph_session,
        """
        MATCH (e:Event)
        REMOVE e.timeline_event_id,
               e.before_event_id,
               e.after_event_id,
               e.created_from_instance_id,
               e.created_from_entity_id,
               e.related_instance_ids,
               e.related_entity_ids,
               e.source_instance_id
        """,
    )

    # Delete legacy structural edges if they still exist
    await _exec(
        graph_session,
        """
        MATCH (:Event)-[r:REFERENCES_SOURCE_INSTANCE]->()
        DELETE r
        """,
    )

    # Validation gates
    cycle_rows = await _exec(
        graph_session,
        """
        MATCH p=(e:Event)-[:BEFORE*1..]->(e)
        RETURN count(p) AS cycle_count
        """,
    )
    cycle_count = int((cycle_rows[0] if cycle_rows else {"cycle_count": 0})["cycle_count"])
    if fail_on_temporal_cycle and cycle_count > 0:
        raise ValueError(f"Temporal cycle check failed: {cycle_count} cycles found")

    disconnected_rows = await _exec(
        graph_session,
        """
        MATCH (e:Event)
        WHERE NOT ( (:OntologyInstance)-[:HAS_EVENT]->(e) )
        RETURN count(e) AS disconnected_events
        """,
    )
    disconnected_events = int(
        (disconnected_rows[0] if disconnected_rows else {"disconnected_events": 0})[
            "disconnected_events"
        ]
    )

    duplicate_inverse_rows = await _exec(
        graph_session,
        """
        MATCH (a:Event)-[:BEFORE]->(b:Event)
        WHERE NOT (b)-[:AFTER]->(a)
        RETURN count(*) AS missing_inverse
        """,
    )
    missing_inverse = int(
        (duplicate_inverse_rows[0] if duplicate_inverse_rows else {"missing_inverse": 0})[
            "missing_inverse"
        ]
    )

    return {
        "promoted_events": int((promoted[0] if promoted else {"promoted": 0})["promoted"]),
        "temporal_cycles": cycle_count,
        "disconnected_events": disconnected_events,
        "missing_temporal_inverse_edges": missing_inverse,
        "status": "ok",
    }
