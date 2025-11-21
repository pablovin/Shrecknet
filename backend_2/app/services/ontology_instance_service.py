from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from neo4j import AsyncSession as AsyncNeo4jSession, AsyncTransaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ontology import OntologyEntity
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.ontology_instance import (
    OntologyInstanceCreate,
    OntologyInstanceEntityCreate,
    OntologyInstanceRead,
    OntologyInstanceUpdate,
    TimelineEventCreate,
    TimelineEventRead,
    TimelineEventUpdate,
)

from neo4j.time import DateTime as Neo4jDateTime

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _format_dt(dt: datetime) -> str:
    return dt.strftime(ISO_FORMAT)


def _parse_dt(raw: str | datetime | Neo4jDateTime | None) -> datetime:
    """
    Parse various datetime formats safely:
    - None → now (UTC)
    - str (with or without Z) → fromisoformat
    - neo4j.time.DateTime → convert to Python datetime
    - datetime → returned as is
    """
    if raw is None:
        return datetime.utcnow()

    if isinstance(raw, datetime):
        return raw

    # Handle Neo4j's custom datetime object
    if isinstance(raw, Neo4jDateTime):
        # convert to Python datetime
        return raw.to_native()  # this gives a normal Python datetime

    if isinstance(raw, str):
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            # Neo4j may return nanosecond precision (9 digits). Trim to microseconds.
            match = re.match(r"(.*\\.\\d{6})\\d+(.*)", candidate)
            if match:
                trimmed = f"{match.group(1)}{match.group(2)}"
                return datetime.fromisoformat(trimmed)
            # Fallback to current time if the format is still unexpected
            return datetime.utcnow()

    # Fallback — if something unexpected
    return datetime.utcnow()


def _ensure_datetime(value: datetime | None) -> datetime:
    return value if value is not None else datetime.utcnow()


def _normalize_optional_str(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


def _normalize_id_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    if not values:
        return normalized
    for value in values:
        cleaned = _normalize_optional_str(value)
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _build_timeline_text(
    title: str,
    description: str,
    source_instance_id: str | None,
    related_instance_ids: list[str],
    before_event_id: str | None,
    after_event_id: str | None,
) -> str:
    lines = [f"Timeline Event: {title}"]
    if description:
        lines.append(description.strip())
    if source_instance_id:
        lines.append(f"Source Entity: {source_instance_id}")
    if related_instance_ids:
        lines.append(f"Involved Entities: {', '.join(related_instance_ids)}")
    if before_event_id:
        lines.append(f"Occurs After Event: {before_event_id}")
    if after_event_id:
        lines.append(f"Precedes Event: {after_event_id}")
    return "\n".join(lines)


class OntologyInstanceService:
    def __init__(
        self, sql_session: AsyncSession, graph_session: AsyncNeo4jSession
    ) -> None:
        self.sql_session = sql_session
        self.graph_session = graph_session
        self.repository = OntologyRepository(sql_session)

    async def _timeline_event_ids(self, instance_id: str) -> set[str]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)
            RETURN event.timeline_event_id AS event_id
            """,
            instance_id=instance_id,
        )
        rows = await result.data()
        return {row["event_id"] for row in rows if row.get("event_id")}

    async def _clear_timeline_event_edges(
        self, tx: AsyncTransaction, *, timeline_event_id: str
    ) -> None:
        """Remove derived relationships for a timeline event before reapplying."""
        await tx.run(
            """
            MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})-[rel:SOURCE_ENTITY]->()
            DELETE rel
            """,
            timeline_event_id=timeline_event_id,
        )
        await tx.run(
            """
            MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})-[rel:INVOLVES_ENTITY]->()
            DELETE rel
            """,
            timeline_event_id=timeline_event_id,
        )
        await tx.run(
            """
            MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})-[rel:FOLLOWS]->()
            DELETE rel
            """,
            timeline_event_id=timeline_event_id,
        )
        await tx.run(
            """
            MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})-[rel:PRECEDES]->()
            DELETE rel
            """,
            timeline_event_id=timeline_event_id,
        )

    async def _apply_timeline_event_edges(
        self,
        tx: AsyncTransaction,
        *,
        timeline_event_id: str,
        instance_id: str,
        source_entity_id: str | None,
        related_entity_ids: list[str] | None,
        before_event_id: str | None,
        after_event_id: str | None,
    ) -> None:
        """Create relationships that mirror timeline metadata for traversal/retrieval."""
        if source_entity_id:
            await tx.run(
                """
                MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})
                MATCH (entity:EntityInstance {entity_instance_id: $source_entity_id})
                WHERE entity.instance_id = $instance_id
                MERGE (event)-[:SOURCE_ENTITY]->(entity)
                """,
                timeline_event_id=timeline_event_id,
                source_entity_id=source_entity_id,
                instance_id=instance_id,
            )
        related_ids = [rid for rid in (related_entity_ids or []) if rid]
        if related_ids:
            await tx.run(
                """
                MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})
                WITH event
                UNWIND $related_entity_ids AS related_id
                MATCH (entity:EntityInstance {entity_instance_id: related_id})
                WHERE entity.instance_id = event.instance_id
                MERGE (event)-[:INVOLVES_ENTITY]->(entity)
                """,
                timeline_event_id=timeline_event_id,
                related_entity_ids=related_ids,
            )
        if before_event_id:
            await tx.run(
                """
                MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})
                MATCH (previous:TimelineEvent {timeline_event_id: $before_event_id})
                WHERE previous.instance_id = event.instance_id
                MERGE (event)-[:FOLLOWS]->(previous)
                """,
                timeline_event_id=timeline_event_id,
                before_event_id=before_event_id,
            )
        if after_event_id:
            await tx.run(
                """
                MATCH (event:TimelineEvent {timeline_event_id: $timeline_event_id})
                MATCH (next:TimelineEvent {timeline_event_id: $after_event_id})
                WHERE next.instance_id = event.instance_id
                MERGE (event)-[:PRECEDES]->(next)
                """,
                timeline_event_id=timeline_event_id,
                after_event_id=after_event_id,
            )

    def _timeline_node_to_read(self, node: Any) -> TimelineEventRead:
        props = dict(node)
        related_ids = props.get("related_instance_ids") or []
        if not isinstance(related_ids, list):
            related_ids = [related_ids]
        related_entity_ids = props.get("related_entity_ids") or []
        if not isinstance(related_entity_ids, list):
            related_entity_ids = [related_entity_ids]
        source_entity_id = props.get("source_entity_id")
        source_instance_id = props.get("source_instance_id")
        if not source_entity_id and source_instance_id:
            # legacy events stored entity ids directly
            source_entity_id = source_instance_id
            source_instance_id = props.get("instance_id")
        if not related_entity_ids and related_ids:
            # treat legacy related ids as entity ids, but preserve page ids fallback
            related_entity_ids = list(related_ids)
        return TimelineEventRead(
            timeline_event_id=props["timeline_event_id"],
            instance_id=props["instance_id"],
            ontology_id=props["ontology_id"],
            title=props.get("title") or "",
            description=props.get("description") or "",
            source_instance_id=source_instance_id,
            source_entity_id=source_entity_id,
            related_instance_ids=[str(value) for value in related_ids],
            related_entity_ids=[str(value) for value in related_entity_ids],
            before_event_id=props.get("before_event_id"),
            after_event_id=props.get("after_event_id"),
            created_at=_parse_dt(props.get("created_at")),
            updated_at=_parse_dt(props.get("updated_at")),
        )

    def _prepare_timeline_event_rows(
        self,
        events: list[TimelineEventCreate],
        *,
        instance_id: str,
        ontology_id: int,
    ) -> list[dict[str, Any]]:
        if not events:
            return []
        timestamp = _format_dt(datetime.utcnow())
        rows: list[dict[str, Any]] = []
        ids: set[str] = set()
        for event in events:
            event_id = _normalize_optional_str(event.timeline_event_id) or str(uuid4())
            if event_id in ids:
                raise ValueError(f"Duplicate timeline event id '{event_id}' in payload")
            title = event.title.strip()
            description = event.description.strip()
            related_ids = _normalize_id_list(event.related_instance_ids)
            before_id = _normalize_optional_str(event.before_event_id)
            after_id = _normalize_optional_str(event.after_event_id)
            source_id = _normalize_optional_str(event.source_instance_id)
            text_payload = _build_timeline_text(
                title, description, source_id, related_ids, before_id, after_id
            )
            row = {
                "timeline_event_id": event_id,
                "entity_instance_id": event_id,
                "instance_id": instance_id,
                "ontology_id": ontology_id,
                "name": title,
                "alias": title,
                "title": title,
                "description": description,
                "source_instance_id": instance_id if source_id else None,
                "source_entity_id": source_id,
                "related_instance_ids": [instance_id for _ in related_ids] if related_ids else [],
                "related_entity_ids": related_ids,
                "before_event_id": before_id,
                "after_event_id": after_id,
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_updated_date": timestamp,
                "text": text_payload,
                "autogenerated_text": text_payload,
                "is_embedded": False,
                "last_embedded_date": None,
            }
            rows.append(row)
            ids.add(event_id)

        for row in rows:
            for pointer in ("before_event_id", "after_event_id"):
                target_id = row[pointer]
                if target_id and target_id not in ids:
                    raise ValueError(
                        f"Unknown timeline event id '{target_id}' referenced in before/after"
                    )

        return rows

    async def _replace_timeline_events_in_tx(
        self,
        tx: AsyncTransaction,
        *,
        instance_id: str,
        ontology_id: int,
        events: list[TimelineEventCreate],
    ) -> None:
        await tx.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)-[:HAS_CHUNK]->(chunk:EntityChunk)
            DETACH DELETE chunk
            """,
            instance_id=instance_id,
        )
        await tx.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)
            DETACH DELETE event
            """,
            instance_id=instance_id,
        )
        rows = self._prepare_timeline_event_rows(
            events, instance_id=instance_id, ontology_id=ontology_id
        )
        if not rows:
            return
        for row in rows:
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})
                CREATE (i)-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {
                    timeline_event_id: $timeline_event_id,
                    entity_instance_id: $entity_instance_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $name,
                    alias: $alias,
                    title: $title,
                    description: $description,
                    source_instance_id: $source_instance_id,
                    source_entity_id: $source_entity_id,
                    related_instance_ids: $related_instance_ids,
                    related_entity_ids: $related_entity_ids,
                    before_event_id: $before_event_id,
                    after_event_id: $after_event_id,
                    created_at: $created_at,
                    updated_at: $updated_at,
                    last_updated_date: $last_updated_date,
                    text: $text,
                    autogenerated_text: $autogenerated_text,
                    is_embedded: $is_embedded,
                    last_embedded_date: $last_embedded_date
                })
                """,
                **row,
            )
            await self._apply_timeline_event_edges(
                tx,
                timeline_event_id=row["timeline_event_id"],
                instance_id=instance_id,
                source_entity_id=row.get("source_entity_id") or row.get("source_instance_id"),
                related_entity_ids=row.get("related_entity_ids") or row.get("related_instance_ids"),
                before_event_id=row.get("before_event_id"),
                after_event_id=row.get("after_event_id"),
            )

    async def _timeline_events_for_instance(
        self, instance_id: str
    ) -> list[TimelineEventRead]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)
            RETURN event
            ORDER BY event.created_at ASC
            """,
            instance_id=instance_id,
        )
        rows = await result.data()
        events: list[TimelineEventRead] = []
        for record in rows:
            node = record.get("event")
            if not node:
                continue
            events.append(self._timeline_node_to_read(node))
        return events

    async def _get_instance_ontology_id(self, instance_id: str) -> int:
        result = await self.graph_session.run(
            """
            MATCH (i:OntologyInstance {instance_id: $instance_id})
            RETURN i.ontology_id AS ontology_id
            """,
            instance_id=instance_id,
        )
        row = await result.single()
        if not row or row.get("ontology_id") is None:
            raise ValueError("Ontology instance not found")
        return row["ontology_id"]

    # ------------------------------------------------------------------
    async def create_instance(
        self, payload: OntologyInstanceCreate
    ) -> OntologyInstanceRead:
        ontology = await self.repository.get(payload.ontology_id)
        if not ontology:
            raise ValueError("Ontology not found")

        definitions = await self._load_entity_definitions(payload.ontology_id)
        self._validate_entities_payload(payload.entities, definitions)

        instance_id = str(uuid4())
        timestamp = _format_dt(datetime.utcnow())

        alias_to_ids: dict[str, str] = {}
        nodes_payload: list[dict[str, Any]] = []
        for entity_payload in payload.entities:
            entity_node_id = str(uuid4())
            alias_to_ids[entity_payload.alias] = entity_node_id
            normalized_alias = re.sub(
                r"[^a-z0-9_]+", "_", entity_payload.alias.strip().lower()
            )
            alias_to_ids[normalized_alias] = entity_node_id
            prop_map = {
                str(prop.definition_id): prop.value
                for prop in entity_payload.properties
            }
            created_dt = _ensure_datetime(entity_payload.created_date)
            updated_dt = _ensure_datetime(entity_payload.last_updated_date)
            nodes_payload.append(
                {
                    "entity_instance_id": entity_node_id,
                    "payload": entity_payload,
                    "properties": json.dumps(prop_map),
                    "created_date": _format_dt(created_dt),
                    "last_updated_date": _format_dt(updated_dt),
                }
            )

        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                CREATE (i:OntologyInstance {
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $name,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
                """,
                instance_id=instance_id,
                ontology_id=payload.ontology_id,
                name=payload.name,
                created_at=timestamp,
                updated_at=timestamp,
            )

            for node in nodes_payload:
                entity_node_id = node["entity_instance_id"]
                entity_payload = node["payload"]
                prop_json = node["properties"]
                await tx.run(
                    """
                    MATCH (i:OntologyInstance {instance_id: $instance_id})
                    CREATE (i)-[:HAS_ENTITY]->(e:EntityInstance {
                        entity_instance_id: $entity_instance_id,
                        instance_id: $instance_id,
                        ontology_id: $ontology_id,
                        entity_definition_id: $entity_definition_id,
                        properties: $properties,
                        text: $text,
                        node_avatar_url: $node_avatar_url,
                        autogenerated_text: $autogenerated_text,
                        text_linked: $text_linked,
                        autogenerated_text_linked: $autogenerated_text_linked,
                        created_date: $created_date,
                        last_updated_date: $last_updated_date,
                        author_type: $author_type,
                        author_id: $author_id,
                        created_at: $created_at,
                        updated_at: $updated_at,
                        alias: $alias,
                        is_embedded: false,
                        last_embedded_date: null
                    })
                    """,
                    instance_id=instance_id,
                    ontology_id=payload.ontology_id,
                    entity_instance_id=entity_node_id,
                    entity_definition_id=entity_payload.definition_id,
                    properties=prop_json,
                    text=entity_payload.text,
                    node_avatar_url=entity_payload.node_avatar_url,
                    autogenerated_text=entity_payload.autogenerated_text,
                    text_linked=entity_payload.text,
                    autogenerated_text_linked=entity_payload.autogenerated_text,
                    created_date=node["created_date"],
                    last_updated_date=node["last_updated_date"],
                    author_type=entity_payload.author_type.value,
                    author_id=entity_payload.author_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                    alias=entity_payload.alias,
                )

            for node in nodes_payload:
                entity_node_id = node["entity_instance_id"]
                entity_payload = node["payload"]
                relationship_definitions = definitions[entity_payload.definition_id][
                    "relationships"
                ]
                for relationship_payload in entity_payload.relationships:
                    target_alias = relationship_payload.target_alias
                    target_id: str | None
                    if target_alias:
                        target_id = alias_to_ids.get(target_alias)
                        if target_id is None:
                            normalized_alias = re.sub(
                                r"[^a-z0-9_]+",
                                "_",
                                target_alias.strip().lower(),
                            )
                            target_id = alias_to_ids.get(normalized_alias)
                        if target_id is None:
                            raise ValueError(
                                f"Unknown target alias '{target_alias}' for relationship"
                            )
                    else:
                        target_id = relationship_payload.target_entity_instance_id
                        if target_id is None:
                            raise ValueError(
                                "Relationship must specify target alias or entity instance id"
                            )
                        await self._validate_existing_target_entity(
                            target_id,
                            payload.ontology_id,
                            relationship_definitions[
                                relationship_payload.definition_id
                            ].destiny_entity_id,
                            tx,
                        )
                    rel_definition = relationship_definitions[
                        relationship_payload.definition_id
                    ]
                    relationship_id = str(uuid4())
                    relationship_data = json.dumps(relationship_payload.data or {})
                    await tx.run(
                        """
                        MATCH (source:EntityInstance {entity_instance_id: $source_id})
                        MATCH (target:EntityInstance {entity_instance_id: $target_id})
                        CREATE (source)-[r:RELATES_TO {
                            relationship_instance_id: $relationship_id,
                            relationship_definition_id: $relationship_definition_id,
                            destiny_entity_definition_id: $destiny_definition_id,
                            data: $data,
                            created_at: $created_at,
                            updated_at: $updated_at
                        }]->(target)
                        """,
                        source_id=entity_node_id,
                        target_id=target_id,
                        relationship_id=relationship_id,
                        relationship_definition_id=relationship_payload.definition_id,
                        destiny_definition_id=rel_definition.destiny_entity_id,
                        data=relationship_data,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                    if rel_definition.bi_directional:
                        reverse_id = str(uuid4())
                        await tx.run(
                            """
                            MATCH (source:EntityInstance {entity_instance_id: $source_id})
                            MATCH (target:EntityInstance {entity_instance_id: $target_id})
                            CREATE (target)-[r:RELATES_TO {
                                relationship_instance_id: $relationship_id,
                                relationship_definition_id: $relationship_definition_id,
                                destiny_entity_definition_id: $destiny_definition_id,
                                data: $data,
                                created_at: $created_at,
                                updated_at: $updated_at
                            }]->(source)
                            """,
                            source_id=entity_node_id,
                            target_id=target_id,
                            relationship_id=reverse_id,
                            relationship_definition_id=relationship_payload.definition_id,
                            destiny_definition_id=entity_payload.definition_id,
                            data=relationship_data,
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
            await self._replace_timeline_events_in_tx(
                tx,
                instance_id=instance_id,
                ontology_id=payload.ontology_id,
                events=payload.timeline_events,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
            instance = await self.get_instance(instance_id)
            from app.tasks.ontology_links import link_instance as link_instance_task

            link_instance_task.delay(instance.instance_id)
            return instance

    async def list_instances(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        search: str | None = None,
        ontology_id: int | None = None,
    ) -> Sequence[OntologyInstanceRead]:
        clauses = ["MATCH (i:OntologyInstance)"]
        filters: list[str] = []
        params: dict[str, Any] = {"skip": skip, "limit": limit}
        if ontology_id is not None:
            filters.append("i.ontology_id = $ontology_id")
            params["ontology_id"] = ontology_id
        if search:
            filters.append(
                "toLower(i.name) CONTAINS toLower($search)"
            )
            params["search"] = search
        if filters:
            clauses.append("WHERE " + " AND ".join(filters))
        clauses.append("RETURN i ORDER BY i.updated_at DESC SKIP $skip LIMIT $limit")
        query = "\n".join(clauses)
        result = await self.graph_session.run(query, params)
        records = await result.data()
        instance_ids = [record["i"]["instance_id"] for record in records]
        return [await self.get_instance(instance_id) for instance_id in instance_ids]

    async def get_instance(self, instance_id: str) -> OntologyInstanceRead:
        record = await self.graph_session.run(
            """
            MATCH (i:OntologyInstance {instance_id: $instance_id})
            RETURN i
            """,
            instance_id=instance_id,
        )
        instance_data = await record.single()
        if not instance_data:
            raise ValueError("Ontology instance not found")
        instance_node = instance_data["i"]

        entities_result = await self.graph_session.run(
            """
            MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance)
            RETURN e
            """,
            instance_id=instance_id,
        )
        entity_records = await entities_result.data()
        entities_map: dict[str, dict[str, Any]] = {}
        for record in entity_records:
            node = record["e"]
            entities_map[node["entity_instance_id"]] = {
                "entity_instance_id": node["entity_instance_id"],
                "definition_id": node.get("entity_definition_id"),
                "alias": node.get("alias"),
                "text": node.get("text", ""),
                "node_avatar_url": node.get("node_avatar_url"),
                "autogenerated_text": node.get("autogenerated_text"),
                "text_linked": node.get("text_linked") or node.get("text", ""),
                "autogenerated_text_linked": node.get("autogenerated_text_linked")
                or node.get("autogenerated_text"),
                "created_date": _parse_dt(node.get("created_date")),
                "last_updated_date": _parse_dt(node.get("last_updated_date")),
                "author_type": node.get("author_type"),
                "author_id": node.get("author_id"),
                "properties": json.loads(node.get("properties") or "{}"),
                "relationships": [],
            }

        relationships_result = await self.graph_session.run(
            """
            MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(source:EntityInstance)
            OPTIONAL MATCH (source)-[r:RELATES_TO]->(target:EntityInstance)
            RETURN source.entity_instance_id AS source_id,
                   r.relationship_instance_id AS relationship_instance_id,
                   r.relationship_definition_id AS definition_id,
                   r.destiny_entity_definition_id AS destiny_definition_id,
                   r.data AS rel_data,
                   target.entity_instance_id AS target_id
            """,
            instance_id=instance_id,
        )
        rel_records = await relationships_result.data()
        for record in rel_records:
            relationship_instance_id = record.get("relationship_instance_id")
            if relationship_instance_id is None:
                continue
            source_id = record["source_id"]
            target_id = record.get("target_id")
            if source_id not in entities_map or target_id is None:
                continue
            entities_map[source_id]["relationships"].append(
                {
                    "relationship_instance_id": relationship_instance_id,
                    "definition_id": record.get("definition_id"),
                    "target_entity_id": target_id,
                    "destiny_entity_definition_id": record.get("destiny_definition_id"),
                    "data": json.loads(record.get("rel_data") or "{}"),
                }
            )

        timeline_events = await self._timeline_events_for_instance(instance_id)

        return OntologyInstanceRead(
            instance_id=instance_node["instance_id"],
            ontology_id=instance_node["ontology_id"],
            name=instance_node.get("name"),
            created_at=_parse_dt(instance_node.get("created_at")),
            updated_at=_parse_dt(instance_node.get("updated_at")),
            entities=[
                {
                    "entity_instance_id": entity_data["entity_instance_id"],
                    "definition_id": entity_data["definition_id"],
                    "alias": entity_data.get("alias"),
                    "text": entity_data["text"],
                    "text_linked": entity_data.get("text_linked"),
                    "node_avatar_url": entity_data.get("node_avatar_url"),
                    "autogenerated_text": entity_data.get("autogenerated_text"),
                    "autogenerated_text_linked": entity_data.get(
                        "autogenerated_text_linked"
                    ),
                    "created_date": entity_data["created_date"],
                    "last_updated_date": entity_data["last_updated_date"],
                    "author_type": entity_data["author_type"],
                    "author_id": entity_data["author_id"],
                    "properties": [
                        {
                            "definition_id": int(prop_id),
                            "value": value,
                        }
                        for prop_id, value in entity_data["properties"].items()
                    ],
                    "relationships": entity_data["relationships"],
                }
                for entity_data in entities_map.values()
            ],
            timeline_events=timeline_events,
        )

    async def delete_instance(self, instance_id: str) -> None:
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance)-[:HAS_CHUNK]->(chunk:EntityChunk)
                DETACH DELETE chunk
                """,
                instance_id=instance_id,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance)
                DETACH DELETE e
                """,
                instance_id=instance_id,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)-[:HAS_CHUNK]->(chunk:EntityChunk)
                DETACH DELETE chunk
                """,
                instance_id=instance_id,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)
                DETACH DELETE event
                """,
                instance_id=instance_id,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})
                DETACH DELETE i
                """,
                instance_id=instance_id,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

    async def update_instance(
        self, instance_id: str, payload: OntologyInstanceUpdate
    ) -> OntologyInstanceRead:
        current = await self.get_instance(instance_id)
        timestamp = _format_dt(datetime.utcnow())

        await self.graph_session.run(
            """
            MATCH (i:OntologyInstance {instance_id: $instance_id})
            SET i.name = coalesce($name, i.name),
                i.updated_at = $updated_at
            """,
            instance_id=instance_id,
            name=payload.name,
            updated_at=timestamp,
        )

        if payload.entities is None:
            if payload.timeline_events is not None:
                tx = await self.graph_session.begin_transaction()
                try:
                    await self._replace_timeline_events_in_tx(
                        tx,
                        instance_id=instance_id,
                        ontology_id=current.ontology_id,
                        events=payload.timeline_events,
                    )
                except Exception:
                    await tx.rollback()
                    await tx.close()
                    raise
                else:
                    await tx.commit()
                    await tx.close()
            instance = await self.get_instance(instance_id)
            from app.tasks.ontology_links import link_instance as link_instance_task

            link_instance_task.delay(instance.instance_id)
            return instance

        definitions = await self._load_entity_definitions(current.ontology_id)
        self._validate_entities_payload(payload.entities, definitions)

        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e)
                DETACH DELETE e
                """,
                instance_id=instance_id,
            )

            alias_to_ids: dict[str, str] = {}
            nodes_payload: list[
                tuple[str, OntologyInstanceEntityCreate, dict[str, Any], str, str]
            ] = []
            for entity_payload in payload.entities:
                entity_node_id = str(uuid4())
                alias_to_ids[entity_payload.alias] = entity_node_id
                normalized_alias = re.sub(
                    r"[^a-z0-9_]+", "_", entity_payload.alias.strip().lower()
                )
                alias_to_ids[normalized_alias] = entity_node_id
                prop_map = {
                    str(prop.definition_id): prop.value
                    for prop in entity_payload.properties
                }
                created_dt = _ensure_datetime(entity_payload.created_date)
                updated_dt = _ensure_datetime(entity_payload.last_updated_date)
                nodes_payload.append(
                    (
                        entity_node_id,
                        entity_payload,
                        prop_map,
                        _format_dt(created_dt),
                        _format_dt(updated_dt),
                    )
                )

            for (
                entity_node_id,
                entity_payload,
                prop_map,
                created_iso,
                updated_iso,
            ) in nodes_payload:
                await tx.run(
                    """
                    MATCH (i:OntologyInstance {instance_id: $instance_id})
                    CREATE (i)-[:HAS_ENTITY]->(e:EntityInstance {
                        entity_instance_id: $entity_instance_id,
                        instance_id: $instance_id,
                        ontology_id: $ontology_id,
                        entity_definition_id: $entity_definition_id,
                        properties: $properties,
                        text: $text,
                        node_avatar_url: $node_avatar_url,
                        autogenerated_text: $autogenerated_text,
                        text_linked: $text_linked,
                        autogenerated_text_linked: $autogenerated_text_linked,
                        created_date: $created_date,
                        last_updated_date: $last_updated_date,
                        author_type: $author_type,
                        author_id: $author_id,
                        created_at: $created_at,
                        updated_at: $updated_at,
                        alias: $alias,
                        is_embedded: false,
                        last_embedded_date: null
                    })
                    """,
                    instance_id=instance_id,
                    ontology_id=current.ontology_id,
                    entity_instance_id=entity_node_id,
                    entity_definition_id=entity_payload.definition_id,
                    properties=json.dumps(prop_map),
                    text=entity_payload.text,
                    node_avatar_url=entity_payload.node_avatar_url,
                    autogenerated_text=entity_payload.autogenerated_text,
                    text_linked=entity_payload.text,
                    autogenerated_text_linked=entity_payload.autogenerated_text,
                    created_date=created_iso,
                    last_updated_date=updated_iso,
                    author_type=entity_payload.author_type.value,
                    author_id=entity_payload.author_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                    alias=entity_payload.alias,
                )

            for entity_node_id, entity_payload, _, _, _ in nodes_payload:
                relationship_definitions = definitions[entity_payload.definition_id][
                    "relationships"
                ]
                for relationship_payload in entity_payload.relationships:
                    target_alias = relationship_payload.target_alias
                    target_id: str | None
                    if target_alias:
                        target_id = alias_to_ids.get(target_alias)
                        if target_id is None:
                            normalized_alias = re.sub(
                                r"[^a-z0-9_]+",
                                "_",
                                target_alias.strip().lower(),
                            )
                            target_id = alias_to_ids.get(normalized_alias)
                        if target_id is None:
                            raise ValueError(
                                f"Unknown target alias '{target_alias}' for relationship"
                            )
                    else:
                        target_id = relationship_payload.target_entity_instance_id
                        if target_id is None:
                            raise ValueError(
                                "Relationship must specify target alias or entity instance id"
                            )
                        await self._validate_existing_target_entity(
                            target_id,
                            current.ontology_id,
                            relationship_definitions[
                                relationship_payload.definition_id
                            ].destiny_entity_id,
                            tx,
                        )
                    rel_definition = relationship_definitions[
                        relationship_payload.definition_id
                    ]
                    relationship_id = str(uuid4())
                    relationship_data = json.dumps(relationship_payload.data or {})
                    await tx.run(
                        """
                        MATCH (source:EntityInstance {entity_instance_id: $source_id})
                        MATCH (target:EntityInstance {entity_instance_id: $target_id})
                        CREATE (source)-[r:RELATES_TO {
                            relationship_instance_id: $relationship_id,
                            relationship_definition_id: $relationship_definition_id,
                            destiny_entity_definition_id: $destiny_definition_id,
                            data: $data,
                            created_at: $created_at,
                            updated_at: $updated_at
                        }]->(target)
                        """,
                        source_id=entity_node_id,
                        target_id=target_id,
                        relationship_id=relationship_id,
                        relationship_definition_id=relationship_payload.definition_id,
                        destiny_definition_id=rel_definition.destiny_entity_id,
                        data=relationship_data,
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                    if rel_definition.bi_directional:
                        reverse_id = str(uuid4())
                        await tx.run(
                            """
                            MATCH (source:EntityInstance {entity_instance_id: $source_id})
                            MATCH (target:EntityInstance {entity_instance_id: $target_id})
                            CREATE (target)-[r:RELATES_TO {
                                relationship_instance_id: $relationship_id,
                                relationship_definition_id: $relationship_definition_id,
                                destiny_entity_definition_id: $destiny_definition_id,
                                data: $data,
                                created_at: $created_at,
                                updated_at: $updated_at
                            }]->(source)
                            """,
                            source_id=entity_node_id,
                            target_id=target_id,
                            relationship_id=reverse_id,
                            relationship_definition_id=relationship_payload.definition_id,
                            destiny_definition_id=entity_payload.definition_id,
                            data=relationship_data,
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
            if payload.timeline_events is not None:
                await self._replace_timeline_events_in_tx(
                    tx,
                    instance_id=instance_id,
                    ontology_id=current.ontology_id,
                    events=payload.timeline_events,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
            instance = await self.get_instance(instance_id)
            from app.tasks.ontology_links import link_instance as link_instance_task

            link_instance_task.delay(instance.instance_id)
            return instance

    async def list_timeline_events(self, instance_id: str) -> list[TimelineEventRead]:
        await self._get_instance_ontology_id(instance_id)
        return await self._timeline_events_for_instance(instance_id)

    async def get_timeline_event(
        self, instance_id: str, timeline_event_id: str
    ) -> TimelineEventRead:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {timeline_event_id: $timeline_event_id})
            RETURN event
            """,
            instance_id=instance_id,
            timeline_event_id=timeline_event_id,
        )
        record = await result.single()
        if not record or not record.get("event"):
            raise ValueError("Timeline event not found")
        return self._timeline_node_to_read(record["event"])

    async def create_timeline_event(
        self, instance_id: str, payload: TimelineEventCreate
    ) -> TimelineEventRead:
        ontology_id = await self._get_instance_ontology_id(instance_id)
        existing_ids = await self._timeline_event_ids(instance_id)
        before_id = _normalize_optional_str(payload.before_event_id)
        after_id = _normalize_optional_str(payload.after_event_id)
        event_id = _normalize_optional_str(payload.timeline_event_id) or str(uuid4())
        if event_id in existing_ids:
            raise ValueError(f"Timeline event id '{event_id}' already exists")
        for pointer, ref in (("before_event_id", before_id), ("after_event_id", after_id)):
            if ref == event_id:
                raise ValueError(f"{pointer.replace('_', ' ').title()} cannot reference the same timeline event")
            if ref and ref not in existing_ids:
                raise ValueError(f"{pointer.replace('_', ' ').title()} '{ref}' does not exist for this instance")
        title = payload.title.strip()
        description = payload.description.strip()
        related_ids = _normalize_id_list(payload.related_instance_ids)
        source_id = _normalize_optional_str(payload.source_instance_id)
        created_at = _format_dt(datetime.utcnow())
        text_payload = _build_timeline_text(
            title, description, source_id, related_ids, before_id, after_id
        )
        source_instance_ref = instance_id if source_id else None
        related_instance_refs = [instance_id for _ in related_ids] if related_ids else []

        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})
                CREATE (i)-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {
                    timeline_event_id: $timeline_event_id,
                    entity_instance_id: $timeline_event_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $title,
                    alias: $title,
                    title: $title,
                    description: $description,
                    source_instance_id: $source_instance_id,
                    source_entity_id: $source_entity_id,
                    related_instance_ids: $related_instance_ids,
                    related_entity_ids: $related_entity_ids,
                    before_event_id: $before_event_id,
                    after_event_id: $after_event_id,
                    created_at: $created_at,
                    updated_at: $created_at,
                    last_updated_date: $created_at,
                    text: $text,
                    autogenerated_text: $text,
                    is_embedded: false,
                    last_embedded_date: null
                })
                """,
                instance_id=instance_id,
                ontology_id=ontology_id,
                timeline_event_id=event_id,
                title=title,
                description=description,
                source_instance_id=source_instance_ref,
                source_entity_id=source_id,
                related_instance_ids=related_instance_refs,
                related_entity_ids=related_ids,
                before_event_id=before_id,
                after_event_id=after_id,
                created_at=created_at,
                text=text_payload,
            )
            await self._apply_timeline_event_edges(
                tx,
                timeline_event_id=event_id,
                instance_id=instance_id,
                source_entity_id=source_id,
                related_entity_ids=related_ids,
                before_event_id=before_id,
                after_event_id=after_id,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
        return await self.get_timeline_event(instance_id, event_id)

    async def update_timeline_event(
        self,
        instance_id: str,
        timeline_event_id: str,
        payload: TimelineEventUpdate,
    ) -> TimelineEventRead:
        current_event = await self.get_timeline_event(instance_id, timeline_event_id)
        existing_ids = await self._timeline_event_ids(instance_id)
        updates: dict[str, Any] = {}

        if payload.title is not None:
            title = payload.title.strip()
            if not title:
                raise ValueError("Timeline event title cannot be empty")
            updates["title"] = title
            updates["name"] = title
            updates["alias"] = title
        if payload.description is not None:
            description = payload.description.strip()
            if not description:
                raise ValueError("Timeline event description cannot be empty")
            updates["description"] = description
        if payload.source_instance_id is not None:
            source_id = _normalize_optional_str(payload.source_instance_id)
            updates["source_entity_id"] = source_id
            updates["source_instance_id"] = instance_id if source_id else None
        if payload.related_instance_ids is not None:
            related_entities = _normalize_id_list(payload.related_instance_ids)
            updates["related_entity_ids"] = related_entities
            updates["related_instance_ids"] = (
                [instance_id for _ in related_entities] if related_entities else []
            )
        if payload.before_event_id is not None:
            before_id = _normalize_optional_str(payload.before_event_id)
            if before_id == timeline_event_id:
                raise ValueError("Timeline event cannot reference itself in 'before'")
            if before_id and before_id not in existing_ids:
                raise ValueError(f"before_event_id '{before_id}' does not exist")
            updates["before_event_id"] = before_id
        if payload.after_event_id is not None:
            after_id = _normalize_optional_str(payload.after_event_id)
            if after_id == timeline_event_id:
                raise ValueError("Timeline event cannot reference itself in 'after'")
            if after_id and after_id not in existing_ids:
                raise ValueError(f"after_event_id '{after_id}' does not exist")
            updates["after_event_id"] = after_id

        now_str = _format_dt(datetime.utcnow())
        final_title = updates.get("title", current_event.title)
        final_description = updates.get("description", current_event.description or "")
        final_source_entity = updates.get(
            "source_entity_id",
            current_event.source_entity_id or current_event.source_instance_id,
        )
        final_related_entities = updates.get(
            "related_entity_ids",
            current_event.related_entity_ids
            or current_event.related_instance_ids
            or [],
        )
        final_before = updates.get("before_event_id", current_event.before_event_id)
        final_after = updates.get("after_event_id", current_event.after_event_id)

        text_payload = _build_timeline_text(
            final_title,
            final_description,
            final_source_entity,
            final_related_entities,
            final_before,
            final_after,
        )
        updates["text"] = text_payload
        updates["autogenerated_text"] = text_payload
        updates["last_updated_date"] = now_str
        updates["is_embedded"] = False

        params = {
            "instance_id": instance_id,
            "timeline_event_id": timeline_event_id,
            "updated_at": now_str,
        }
        set_parts = ["event.updated_at = $updated_at"]
        for field, value in updates.items():
            set_parts.append(f"event.{field} = ${field}")
            params[field] = value
        set_clause = ", ".join(set_parts)

        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                f"""
                MATCH (:OntologyInstance {{instance_id: $instance_id}})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {{timeline_event_id: $timeline_event_id}})
                SET {set_clause}
                """,
                **params,
            )
            await self._clear_timeline_event_edges(
                tx, timeline_event_id=timeline_event_id
            )
            await self._apply_timeline_event_edges(
                tx,
                timeline_event_id=timeline_event_id,
                instance_id=instance_id,
                source_entity_id=final_source_entity,
                related_entity_ids=final_related_entities,
                before_event_id=final_before,
                after_event_id=final_after,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
        return await self.get_timeline_event(instance_id, timeline_event_id)

    async def delete_timeline_event(
        self, instance_id: str, timeline_event_id: str
    ) -> None:
        await self.get_timeline_event(instance_id, timeline_event_id)
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {timeline_event_id: $timeline_event_id})-[:HAS_CHUNK]->(chunk:EntityChunk)
                DETACH DELETE chunk
                """,
                instance_id=instance_id,
                timeline_event_id=timeline_event_id,
            )
            await tx.run(
                """
                MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent {timeline_event_id: $timeline_event_id})
                DETACH DELETE event
                """,
                instance_id=instance_id,
                timeline_event_id=timeline_event_id,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

    async def rebuild_timeline_relationships(
        self, *, instance_id: str | None = None
    ) -> dict[str, int]:
        """
        Recreate graph edges for timeline events so legacy data gains explicit relationships.

        Args:
            instance_id: Optional ontology instance to limit the rebuild scope.

        Returns:
            Dictionary reporting how many events were processed and how many failed.
        """
        clauses = [
            "MATCH (inst:OntologyInstance)-[:HAS_TIMELINE_EVENT]->(event:TimelineEvent)"
        ]
        params: dict[str, Any] = {}
        if instance_id:
            clauses.append("WHERE inst.instance_id = $instance_id")
            params["instance_id"] = instance_id
        clauses.append(
            """
            RETURN event.timeline_event_id AS timeline_event_id,
                   inst.instance_id AS instance_id,
                   event.source_instance_id AS source_instance_id,
                   event.source_entity_id AS source_entity_id,
                   event.related_instance_ids AS related_instance_ids,
                   event.related_entity_ids AS related_entity_ids,
                   event.before_event_id AS before_event_id,
                   event.after_event_id AS after_event_id
            """
        )
        query = "\n".join(clauses)
        result = await self.graph_session.run(query, params)
        rows = await result.data()
        processed = 0
        failed = 0
        for row in rows:
            related_ids = row.get("related_entity_ids") or row.get("related_instance_ids") or []
            if isinstance(related_ids, str):
                related_ids = [related_ids]
            tx = await self.graph_session.begin_transaction()
            try:
                await self._clear_timeline_event_edges(
                    tx, timeline_event_id=row["timeline_event_id"]
                )
                await self._apply_timeline_event_edges(
                    tx,
                    timeline_event_id=row["timeline_event_id"],
                    instance_id=row["instance_id"],
                    source_entity_id=row.get("source_entity_id") or row.get("source_instance_id"),
                    related_entity_ids=[str(rid) for rid in related_ids if rid],
                    before_event_id=row.get("before_event_id"),
                    after_event_id=row.get("after_event_id"),
                )
            except Exception:
                failed += 1
                await tx.rollback()
                await tx.close()
                continue
            else:
                processed += 1
                await tx.commit()
                await tx.close()
        return {"processed_events": processed, "failed_events": failed}

    # ------------------------------------------------------------------
    async def _load_entity_definitions(
        self, ontology_id: int
    ) -> dict[int, dict[str, Any]]:
        result = await self.sql_session.execute(
            select(OntologyEntity)
            .options(
                selectinload(OntologyEntity.properties),
                selectinload(OntologyEntity.relationships),
            )
            .where(OntologyEntity.ontology_id == ontology_id)
        )
        entities = result.scalars().unique().all()
        definitions: dict[int, dict[str, Any]] = {}
        for entity in entities:
            definitions[entity.id] = {
                "entity": entity,
                "properties": {prop.id: prop for prop in entity.properties},
                "relationships": {rel.id: rel for rel in entity.relationships},
            }
        return definitions

    def _validate_entities_payload(
        self,
        entities: Sequence[OntologyInstanceEntityCreate],
        definitions: dict[int, dict[str, Any]],
    ) -> None:
        alias_to_definition: dict[str, int] = {}

        def register_alias(alias: str, definition_id: int) -> None:
            cleaned = alias.strip()
            alias_to_definition[cleaned] = definition_id
            normalized = re.sub(r"[^a-z0-9_]+", "_", cleaned.lower())
            alias_to_definition[normalized] = definition_id

        for entity_payload in entities:
            if entity_payload.alias in alias_to_definition:
                raise ValueError(
                    f"Duplicate entity alias '{entity_payload.alias}' in payload"
                )
            if entity_payload.definition_id not in definitions:
                raise ValueError(
                    f"Entity definition {entity_payload.definition_id} does not belong to ontology"
                )
            register_alias(entity_payload.alias, entity_payload.definition_id)
            definition = definitions[entity_payload.definition_id]
            property_map = definition["properties"]
            for prop in entity_payload.properties:
                if prop.definition_id not in property_map:
                    raise ValueError(
                        f"Property definition {prop.definition_id} does not belong to entity"
                    )

        for entity_payload in entities:
            definition = definitions[entity_payload.definition_id]
            relationship_map = definition["relationships"]
            for rel in entity_payload.relationships:
                if rel.definition_id not in relationship_map:
                    raise ValueError(
                        f"Relationship definition {rel.definition_id} does not belong to entity"
                    )
                target_alias = rel.target_alias
                if target_alias:
                    match_definition = alias_to_definition.get(target_alias)
                    if match_definition is None:
                        normalized = re.sub(
                            r"[^a-z0-9_]+", "_", target_alias.strip().lower()
                        )
                        match_definition = alias_to_definition.get(normalized)
                    if match_definition is None:
                        raise ValueError(
                            f"Relationship refers to unknown target alias '{target_alias}'"
                        )
                    destiny_entity_id = relationship_map[
                        rel.definition_id
                    ].destiny_entity_id
                    if (
                        destiny_entity_id is not None
                        and match_definition != destiny_entity_id
                    ):
                        raise ValueError(
                            "Relationship target alias does not match destiny entity definition"
                        )
                elif not rel.target_entity_instance_id:
                    raise ValueError(
                        "Relationship must provide a target alias or entity instance id"
                    )

    async def _validate_existing_target_entity(
        self,
        entity_instance_id: str,
        ontology_id: int,
        expected_definition_id: int | None,
        tx,
    ) -> None:
        result = await tx.run(
            """
            MATCH (e:EntityInstance {entity_instance_id: $entity_instance_id})<-[:HAS_ENTITY]-(inst:OntologyInstance)
            RETURN e.entity_definition_id AS definition_id, inst.ontology_id AS ontology_id
            """,
            entity_instance_id=entity_instance_id,
        )
        record = await result.single()
        if not record:
            raise ValueError(
                f"Target entity instance '{entity_instance_id}' does not exist"
            )
        if record["ontology_id"] != ontology_id:
            raise ValueError("Target entity belongs to a different ontology instance")
        if (
            expected_definition_id is not None
            and record["definition_id"] != expected_definition_id
        ):
            raise ValueError(
                "Target entity instance does not match relationship destiny definition"
            )
