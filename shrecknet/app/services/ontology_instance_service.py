from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from neo4j import AsyncSession as AsyncNeo4jSession, AsyncTransaction
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ontology_instance import OntologyInstance as SqlOntologyInstance
from app.models.ontology import OntologyEntity
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.ontology_instance import (
    MilestoneCreate,
    MilestoneRead,
    MilestoneUpdate,
    OntologyInstanceCreate,
    OntologyInstanceEntityCreate,
    OntologyInstanceRead,
    OntologyInstanceUpdate,
    OntologyInstanceSearchHit,
    OntologyInstanceSearchResponse,
    OntologyInstanceSummary,
    OntologyInstanceSummaryPage,
    SceneCreate,
    SceneRead,
    SceneUpdate,
    TimelineEventCreate,
    TimelineEventRead,
    TimelineEventUpdate,
)

from neo4j.time import DateTime as Neo4jDateTime

logger = logging.getLogger(__name__)

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
INSTANCE_FILTER_CLAUSE = """
WHERE
    ($ontology_id IS NULL OR toInteger(i.ontology_id) = toInteger($ontology_id))
    AND (
        $entity_definition_id IS NULL OR EXISTS {
            MATCH (i)-[:HAS_ENTITY]->(definition_entity:EntityInstance)
            WHERE toInteger(definition_entity.entity_definition_id) = toInteger($entity_definition_id)
        }
    )
    AND (
        $search_lower IS NULL
        OR toLower(coalesce(i.name, '')) CONTAINS $search_lower
        OR EXISTS {
            MATCH (i)-[:HAS_ENTITY]->(search_entity:EntityInstance)
            WHERE search_entity.alias IS NOT NULL
              AND toLower(search_entity.alias) CONTAINS $search_lower
        }
    )
"""


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


def _normalize_slug_alias(raw: str | None) -> str:
    if raw is None:
        return ""
    cleaned = raw.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned)
    return slug.strip("-")


def _slug_alias_pattern(slug: str) -> str:
    tokens = [re.escape(part) for part in re.split(r"[-_]+", slug) if part]
    if not tokens:
        return r"(?!)"
    pattern = "^" + tokens[0]
    for token in tokens[1:]:
        pattern += r"[^A-Za-z0-9]+" + token
    pattern += "$"
    return "(?i)" + pattern


def _normalize_id_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    if not values:
        return normalized
    for value in values:
        cleaned = _normalize_optional_str(value)
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _extract_legacy_event_source(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("source:"):
                source = stripped.split(":", 1)[1].strip()
                return source or None
    return None


def _strip_links_to_instances(
    text: str | None, instance_ids: Sequence[str]
) -> str | None:
    """Remove anchor tags that point to any of the provided instance ids."""
    if not text or not instance_ids:
        return text
    pattern = re.compile(
        r'<a\b[^>]*data-ontology-instance="('
        + "|".join(re.escape(inst_id) for inst_id in instance_ids)
        + r')"[^>]*>(.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    return pattern.sub(r"\2", text)


def _build_timeline_text(
    title: str,
    description: str,
    source: str | None,
    created_from_instance_id: str | None,
    related_entity_ids: list[str],
    before_event_id: str | None,
    after_event_id: str | None,
) -> str:
    lines = [f"Timeline Event: {title}"]
    if description:
        lines.append(description.strip())
    if source:
        lines.append(f"Source: {source.strip()}")
    if created_from_instance_id:
        lines.append(f"Created From Instance: {created_from_instance_id}")
    if related_entity_ids:
        lines.append(f"Involved Entities: {', '.join(related_entity_ids)}")
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

    async def _load_instances_map(
        self, instance_ids: set[str]
    ) -> dict[str, OntologyInstanceRead]:
        if not instance_ids:
            return {}
        ordered_ids = list(instance_ids)
        tasks = [self.get_instance(instance_id) for instance_id in ordered_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        resolved: dict[str, OntologyInstanceRead] = {}
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                continue
            resolved[ordered_ids[idx]] = result
        return resolved

    async def _event_ids(self, instance_id: str) -> set[str]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(event)
            WHERE type(rel) = 'HAS_EVENT' AND 'Event' IN labels(event)
            RETURN event.event_id AS event_id
            """,
            instance_id=instance_id,
        )
        rows = await result.data()
        return {row["event_id"] for row in rows if row.get("event_id")}

    async def _clear_timeline_event_edges(
        self, tx: AsyncTransaction, *, event_id: str
    ) -> None:
        """Remove derived relationships for an event before reapplying."""
        await tx.run(
            """
            MATCH (event:Event {event_id: $event_id})-[rel:SOURCE_ENTITY]->()
            DELETE rel
            """,
            event_id=event_id,
        )
        await tx.run(
            """
            MATCH (event:Event {event_id: $event_id})-[rel:INVOLVES_ENTITY]->()
            DELETE rel
            """,
            event_id=event_id,
        )
        await tx.run(
            """
            MATCH (event:Event {event_id: $event_id})-[rel:BEFORE|AFTER|DERIVED_FROM|RELATED_TO]->()
            DELETE rel
            """,
            event_id=event_id,
        )

    async def _apply_timeline_event_edges(
        self,
        tx: AsyncTransaction,
        *,
        event_id: str,
        instance_id: str,
        source_entity_id: str | None,
        involves_entity_ids: list[str] | None,
        relations: list[dict[str, Any]] | None,
    ) -> None:
        """Create explicit event relations for traversal/retrieval."""
        if source_entity_id:
            await tx.run(
                """
                MATCH (event:Event {event_id: $event_id})
                MATCH (entity:EntityInstance {entity_instance_id: $source_entity_id})
                MERGE (event)-[:SOURCE_ENTITY]->(entity)
                """,
                event_id=event_id,
                source_entity_id=source_entity_id,
            )
        related_ids = [rid for rid in (involves_entity_ids or []) if rid]
        if related_ids:
            await tx.run(
                """
                MATCH (event:Event {event_id: $event_id})
                WITH event
                UNWIND $involves_entity_ids AS related_id
                MATCH (entity:EntityInstance {entity_instance_id: related_id})
                MERGE (event)-[:INVOLVES_ENTITY]->(entity)
                """,
                event_id=event_id,
                involves_entity_ids=related_ids,
            )

        for rel in relations or []:
            relation_type = rel.get("relation_type")
            target_id = rel.get("target_event_id")
            if relation_type not in {"BEFORE", "AFTER", "DERIVED_FROM", "RELATED_TO"}:
                continue
            if not target_id:
                continue
            if relation_type == "BEFORE":
                await tx.run(
                    """
                    MATCH (event:Event {event_id: $event_id})
                    MATCH (target:Event {event_id: $target_event_id})
                    WHERE target.instance_id = $instance_id
                    MERGE (event)-[:BEFORE]->(target)
                    MERGE (target)-[:AFTER]->(event)
                    """,
                    event_id=event_id,
                    target_event_id=target_id,
                    instance_id=instance_id,
                )
            elif relation_type == "AFTER":
                await tx.run(
                    """
                    MATCH (event:Event {event_id: $event_id})
                    MATCH (target:Event {event_id: $target_event_id})
                    WHERE target.instance_id = $instance_id
                    MERGE (event)-[:AFTER]->(target)
                    MERGE (target)-[:BEFORE]->(event)
                    """,
                    event_id=event_id,
                    target_event_id=target_id,
                    instance_id=instance_id,
                )
            elif relation_type == "DERIVED_FROM":
                await tx.run(
                    """
                    MATCH (event:Event {event_id: $event_id})
                    MATCH (target:Event {event_id: $target_event_id})
                    WHERE target.instance_id = $instance_id
                    MERGE (event)-[:DERIVED_FROM]->(target)
                    """,
                    event_id=event_id,
                    target_event_id=target_id,
                    instance_id=instance_id,
                )
            else:
                await tx.run(
                    """
                    MATCH (event:Event {event_id: $event_id})
                    MATCH (target:Event {event_id: $target_event_id})
                    WHERE target.instance_id = $instance_id
                    MERGE (event)-[:RELATED_TO]->(target)
                    """,
                    event_id=event_id,
                    target_event_id=target_id,
                    instance_id=instance_id,
                )

    def _timeline_node_to_read(self, node: Any) -> TimelineEventRead:
        props = dict(node)
        source_entity_id = (
            props.get("_source_entity_edge_id")
            or props.get("source_entity_id")
            or props.get("created_from_entity_id")
        )
        source = _normalize_optional_str(props.get("source")) or _extract_legacy_event_source(
            props.get("text"),
            props.get("text_linked"),
            props.get("autogenerated_text"),
            props.get("autogenerated_text_linked"),
        )
        involves_entity_ids = (
            props.get("_involves_entity_edge_ids")
            or props.get("involves_entity_ids")
            or props.get("related_entity_ids")
            or []
        )
        if not isinstance(involves_entity_ids, list):
            involves_entity_ids = [involves_entity_ids]
        normalized_involves: list[str] = []
        for value in involves_entity_ids:
            cleaned = _normalize_optional_str(value)
            if cleaned:
                normalized_involves.append(cleaned)
        relations_raw = props.get("relations_json")
        relations: list[dict[str, Any]] = []
        if isinstance(relations_raw, str) and relations_raw.strip():
            try:
                parsed = json.loads(relations_raw)
                if isinstance(parsed, list):
                    relations = [item for item in parsed if isinstance(item, dict)]
            except json.JSONDecodeError:
                relations = []
        return TimelineEventRead(
            event_id=props.get("event_id") or props.get("event_id"),
            instance_id=props["instance_id"],
            ontology_id=props["ontology_id"],
            title=props.get("title") or "",
            description=props.get("description") or "",
            source=source,
            source_entity_id=_normalize_optional_str(source_entity_id),
            involves_entity_ids=normalized_involves,
            relations=relations,
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
            event_id = _normalize_optional_str(event.event_id or event.event_id) or str(uuid4())
            if event_id in ids:
                raise ValueError(f"Duplicate timeline event id '{event_id}' in payload")
            title = event.title.strip()
            description = event.description.strip()
            relations = [rel.model_dump() for rel in event.relations]
            source = _normalize_optional_str(event.source)
            source_entity = _normalize_optional_str(event.source_entity_id)
            involves_entity_ids = _normalize_id_list(event.involves_entity_ids)
            text_payload = _build_timeline_text(
                title,
                description,
                source,
                None,
                involves_entity_ids,
                None,
                None,
            )
            row = {
                "event_id": event_id,
                "entity_instance_id": event_id,
                "instance_id": instance_id,
                "ontology_id": ontology_id,
                "name": title,
                "alias": title,
                "title": title,
                "description": description,
                "source": source,
                "source_entity_id": source_entity,
                "involves_entity_ids": involves_entity_ids,
                "relations_json": json.dumps(relations, ensure_ascii=False),
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
            parsed_relations: list[dict[str, Any]] = []
            try:
                parsed_relations = json.loads(row.get("relations_json") or "[]")
            except json.JSONDecodeError:
                parsed_relations = []
            for relation in parsed_relations:
                target_id = relation.get("target_event_id")
                if target_id and target_id not in ids:
                    raise ValueError(f"Unknown event id '{target_id}' referenced in relations")

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
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_EVENT]->(event:Event)-[:HAS_CHUNK]->(chunk:EntityChunk)
            DETACH DELETE chunk
            """,
            instance_id=instance_id,
        )
        await tx.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_EVENT]->(event:Event)
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
                CREATE (i)-[:HAS_EVENT]->(event:Event {
                    event_id: $event_id,
                    entity_instance_id: $entity_instance_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $name,
                    alias: $alias,
                    title: $title,
                    description: $description,
                    source: $source,
                    created_from_instance_id: $created_from_instance_id,
                    created_from_entity_id: $created_from_entity_id,
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
                event_id=row["event_id"],
                instance_id=instance_id,
                source_entity_id=row.get("source_entity_id"),
                involves_entity_ids=row.get("involves_entity_ids"),
                relations=json.loads(row.get("relations_json") or "[]"),
            )

    async def _timeline_events_for_instance(
        self, instance_id: str
    ) -> list[TimelineEventRead]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(event)
            WHERE type(rel) = 'HAS_EVENT' AND 'Event' IN labels(event)
            OPTIONAL MATCH (event)-[:SOURCE_ENTITY]->(source_entity:EntityInstance)
            OPTIONAL MATCH (event)-[:INVOLVES_ENTITY]->(involved_entity:EntityInstance)
            RETURN event,
                   source_entity.entity_instance_id AS source_entity_edge_id,
                   collect(DISTINCT involved_entity.entity_instance_id) AS involves_entity_edge_ids
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
            event_payload = dict(node)
            event_payload["_source_entity_edge_id"] = record.get("source_entity_edge_id")
            event_payload["_involves_entity_edge_ids"] = record.get("involves_entity_edge_ids") or []
            events.append(self._timeline_node_to_read(event_payload))
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
        impacted_entity_ids: set[str] = set()
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
            impacted_entity_ids.add(entity_node_id)

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
                        impacted_entity_ids.add(target_id)
                        impacted_entity_ids.add(entity_node_id)
            await self._replace_timeline_events_in_tx(
                tx,
                instance_id=instance_id,
                ontology_id=payload.ontology_id,
                events=payload.events,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
            if payload.scenes:
                await self._replace_scenes_for_instance(instance_id, payload.scenes)
            instance = await self.get_instance(instance_id)
            from app.tasks.ontology_links import link_instance as link_instance_task
            from app.tasks.neo4j_embedding import embed_nodes as embed_nodes_task

            link_instance_task.delay(instance.instance_id)
            if impacted_entity_ids:
                embed_nodes_task.delay(payload.ontology_id, sorted(impacted_entity_ids))
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
            filters.append("toInteger(i.ontology_id) = toInteger($ontology_id)")
            params["ontology_id"] = ontology_id
        if search:
            filters.append("toLower(i.name) CONTAINS toLower($search)")
            params["search"] = search
        if filters:
            clauses.append("WHERE " + " AND ".join(filters))
        clauses.append("RETURN i ORDER BY i.updated_at DESC SKIP $skip LIMIT $limit")
        query = "\n".join(clauses)
        result = await self.graph_session.run(query, params)
        records = await result.data()
        instance_ids = [record["i"]["instance_id"] for record in records]
        return [await self.get_instance(instance_id) for instance_id in instance_ids]

    async def count_instances(
        self,
        *,
        ontology_id: int | None = None,
        entity_definition_id: int | None = None,
        search: str | None = None,
    ) -> int:
        normalized_search = (search or "").strip()
        params = {
            "ontology_id": ontology_id,
            "entity_definition_id": entity_definition_id,
            "search_lower": normalized_search.lower() or None,
        }
        result = await self.graph_session.run(
            "\n".join(
                [
                    "MATCH (i:OntologyInstance)",
                    INSTANCE_FILTER_CLAUSE,
                    "RETURN count(DISTINCT i) AS total",
                ]
            ),
            params,
        )
        record = await result.single()
        return int(record["total"]) if record and record.get("total") is not None else 0

    async def list_instance_summaries(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        ontology_id: int | None = None,
        entity_definition_id: int | None = None,
        search: str | None = None,
    ) -> OntologyInstanceSummaryPage:
        total = await self.count_instances(
            ontology_id=ontology_id,
            entity_definition_id=entity_definition_id,
            search=search,
        )
        if total == 0:
            return OntologyInstanceSummaryPage(
                total=0,
                skip=skip,
                limit=limit,
                results=[],
            )

        normalized_search = (search or "").strip()
        params = {
            "ontology_id": ontology_id,
            "entity_definition_id": entity_definition_id,
            "search_lower": normalized_search.lower() or None,
            "skip": skip,
            "limit": limit,
        }
        query = "\n".join(
            [
                "MATCH (i:OntologyInstance)",
                INSTANCE_FILTER_CLAUSE,
                "OPTIONAL MATCH (i)-[:HAS_ENTITY]->(e:EntityInstance)",
                "WITH i, collect(e.alias) AS alias_values, "
                "collect(properties(e)['node_avatar_url']) AS avatar_values, "
                "count(e) AS entity_count",
                "RETURN i, alias_values, avatar_values, entity_count",
                "ORDER BY i.updated_at DESC",
                "SKIP $skip",
                "LIMIT $limit",
            ]
        )
        result = await self.graph_session.run(query, params)
        rows = await result.data()
        summaries: list[OntologyInstanceSummary] = []
        for row in rows:
            node = row.get("i")
            if not node:
                continue
            alias_values = [
                alias.strip()
                for alias in (row.get("alias_values") or [])
                if isinstance(alias, str) and alias.strip()
            ]
            avatar_url = next(
                (avatar for avatar in (row.get("avatar_values") or []) if avatar),
                None,
            )
            summaries.append(
                OntologyInstanceSummary(
                    instance_id=node["instance_id"],
                    ontology_id=int(node["ontology_id"]),
                    name=node.get("name"),
                    created_at=_parse_dt(node.get("created_at")),
                    updated_at=_parse_dt(node.get("updated_at")),
                    primary_alias=alias_values[0] if alias_values else None,
                    aliases=alias_values,
                    avatar_url=avatar_url,
                    entity_count=int(row.get("entity_count") or 0),
                )
            )

        return OntologyInstanceSummaryPage(
            total=total,
            skip=skip,
            limit=limit,
            results=summaries,
        )

    async def search_instances(
        self,
        query: str,
        *,
        ontology_id: int | None,
        per_section_limit: int = 20,
    ) -> OntologyInstanceSearchResponse:
        cleaned_query = (query or "").strip()
        if not cleaned_query:
            raise ValueError("Search query cannot be empty")
        if per_section_limit <= 0:
            raise ValueError("Result limit must be positive")

        if ontology_id is None:
            raise ValueError("ontology_id is required")

        ontology = await self.repository.get(ontology_id)
        if ontology is None:
            raise ValueError(f"Ontology '{ontology_id}' not found")

        ontology_filter_id = ontology.id
        applied_ontology_name = ontology.name

        direct_raw = await self._perform_direct_search(
            cleaned_query, ontology_id=ontology_filter_id, limit=per_section_limit
        )
        deep_raw = await self._perform_embedding_search(
            cleaned_query, ontology_id=ontology_filter_id, limit=per_section_limit
        )

        instance_ids: set[str] = {
            entry["instance_id"] for entry in direct_raw if entry.get("instance_id")
        }
        instance_ids.update(
            entry["instance_id"] for entry in deep_raw if entry.get("instance_id")
        )

        instance_map = await self._load_instances_map(instance_ids)

        direct_hits: list[OntologyInstanceSearchHit] = []
        for entry in direct_raw:
            instance_id = entry.get("instance_id")
            instance = instance_map.get(instance_id)
            if not instance:
                continue
            matched_aliases = [
                alias for alias in (entry.get("matched_aliases") or []) if alias
            ]
            reason = "Matched name" if entry.get("name_match") else "Matched alias"
            if not entry.get("name_match") and len(matched_aliases) > 1:
                reason = "Matched aliases"
            direct_hits.append(
                OntologyInstanceSearchHit(
                    instance=instance,
                    ontology_name=applied_ontology_name,
                    world_name=applied_ontology_name,
                    match_reason=reason,
                    matched_aliases=matched_aliases,
                )
            )

        deep_hits: list[OntologyInstanceSearchHit] = []
        for entry in deep_raw:
            instance_id = entry.get("instance_id")
            instance = instance_map.get(instance_id)
            if not instance:
                continue
            reason = entry.get("match_reason") or "Embedding match"
            deep_hits.append(
                OntologyInstanceSearchHit(
                    instance=instance,
                    ontology_name=applied_ontology_name,
                    world_name=applied_ontology_name,
                    match_reason=reason,
                    snippet=entry.get("snippet"),
                    score=entry.get("score"),
                    source_node_id=entry.get("source_node_id"),
                    source_labels=entry.get("source_labels") or [],
                )
            )

        return OntologyInstanceSearchResponse(
            query=cleaned_query,
            ontology_id=ontology_filter_id,
            ontology_name=applied_ontology_name,
            world_name=applied_ontology_name,
            direct_results=direct_hits,
            deep_results=deep_hits,
        )

    async def _perform_direct_search(
        self,
        query: str,
        *,
        ontology_id: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        query_lower = query.lower()
        query_compact = re.sub(r"[^a-z0-9]+", "", query_lower)
        # Create a shorter prefix for wide fuzzy matching
        # Use 50% of the query length (integer division), minimum 2 characters
        prefix_len = max(2, len(query_compact) // 2)
        query_prefix = query_compact[:prefix_len]
        logger.debug(
            "direct_search params: query=%r, query_lower=%r, query_compact=%r, query_prefix=%r, ontology_id=%r, limit=%r",
            query,
            query_lower,
            query_compact,
            query_prefix,
            ontology_id,
            limit,
        )

        # Debug: count total instances for this ontology (only when debug logging is enabled)
        if logger.isEnabledFor(logging.DEBUG):
            count_result = await self.graph_session.run(
                """
                MATCH (i:OntologyInstance)
                WHERE ($ontology_id IS NULL OR toInteger(i.ontology_id) = toInteger($ontology_id))
                RETURN count(i) AS total
                """,
                ontology_id=ontology_id,
            )
            count_data = await count_result.single()
            total_instances = count_data["total"] if count_data else 0
            logger.debug(
                "direct_search: found %d total instances for ontology_id=%r",
                total_instances,
                ontology_id,
            )

        # Fuzzy search with wide matching:
        # 1. Alias/name CONTAINS the full query
        # 2. Alias/name STARTS WITH the full query
        # 3. Alias/name STARTS WITH a shorter prefix (for fuzzy matching like "Nevada" -> "Nevadinha")
        # The prefix is 50% of query length (integer division) to allow for variations
        # Collection limit (10) is larger than final display limit (5) to allow for deduplication
        result = await self.graph_session.run(
            """
            MATCH (i:OntologyInstance)
            WHERE ($ontology_id IS NULL OR toInteger(i.ontology_id) = toInteger($ontology_id))
            OPTIONAL MATCH (i)-[:HAS_ENTITY]->(e:EntityInstance)
            WITH i, collect(DISTINCT {alias: e.alias, name: e.name}) AS entity_data
            WITH
                i,
                [item IN entity_data
                    WHERE item.alias IS NOT NULL AND (
                        toLower(item.alias) CONTAINS $query_lower
                        OR toLower(item.alias) STARTS WITH $query_lower
                        OR replace(replace(replace(toLower(item.alias), ' ', ''), '-', ''), '_', '') CONTAINS $query_compact
                        OR replace(replace(replace(toLower(item.alias), ' ', ''), '-', ''), '_', '') STARTS WITH $query_compact
                        OR replace(replace(replace(toLower(item.alias), ' ', ''), '-', ''), '_', '') STARTS WITH $query_prefix
                    )
                    | item.alias][0..10] AS matched_aliases,
                [item IN entity_data
                    WHERE item.name IS NOT NULL AND (
                        toLower(item.name) CONTAINS $query_lower
                        OR toLower(item.name) STARTS WITH $query_lower
                        OR replace(replace(replace(toLower(item.name), ' ', ''), '-', ''), '_', '') CONTAINS $query_compact
                        OR replace(replace(replace(toLower(item.name), ' ', ''), '-', ''), '_', '') STARTS WITH $query_compact
                        OR replace(replace(replace(toLower(item.name), ' ', ''), '-', ''), '_', '') STARTS WITH $query_prefix
                    )
                    | item.name][0..10] AS matched_names,
                (
                    toLower(i.name) CONTAINS $query_lower
                    OR toLower(i.name) STARTS WITH $query_lower
                    OR replace(replace(replace(toLower(i.name), ' ', ''), '-', ''), '_', '') CONTAINS $query_compact
                    OR replace(replace(replace(toLower(i.name), ' ', ''), '-', ''), '_', '') STARTS WITH $query_compact
                    OR replace(replace(replace(toLower(i.name), ' ', ''), '-', ''), '_', '') STARTS WITH $query_prefix
                ) AS name_match
            WHERE name_match OR size(matched_aliases) > 0 OR size(matched_names) > 0
            WITH i, name_match, matched_aliases, matched_names,
                 matched_aliases + [n IN matched_names WHERE NOT n IN matched_aliases] AS all_matched
            RETURN
                i.instance_id AS instance_id,
                name_match,
                all_matched[0..5] AS matched_aliases
            ORDER BY CASE WHEN name_match THEN 0 ELSE 1 END, i.updated_at DESC
            LIMIT $limit
            """,
            ontology_id=ontology_id,
            query_lower=query_lower,
            query_compact=query_compact,
            query_prefix=query_prefix,
            limit=limit,
        )
        records = await result.data()
        logger.debug("direct_search results: %d records", len(records))
        return records

    async def _perform_embedding_search(
        self,
        query: str,
        *,
        ontology_id: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Deep search is disabled - always return empty results
        # This will be implemented in a future update
        return []

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
            OPTIONAL MATCH (source)-[r]->(target:EntityInstance)
            WHERE type(r) = 'RELATES_TO'
            RETURN source.entity_instance_id AS source_id,
                   properties(r)['relationship_instance_id'] AS relationship_instance_id,
                   properties(r)['relationship_definition_id'] AS definition_id,
                   properties(r)['destiny_entity_definition_id'] AS destiny_definition_id,
                   properties(r)['data'] AS rel_data,
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
        scenes = await self.list_scenes(instance_id)

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
            events=timeline_events,
            scenes=scenes,
        )

    async def get_instance_by_slug_alias(self, slug_alias: str) -> OntologyInstanceRead:
        normalized_slug = _normalize_slug_alias(slug_alias)
        if not normalized_slug:
            raise ValueError("Slug alias cannot be empty")
        alias_pattern = _slug_alias_pattern(normalized_slug)
        raw_alias = slug_alias.strip().lower()
        spaced_alias = normalized_slug.replace("-", " ").strip()
        result = await self.graph_session.run(
            """
            MATCH (i:OntologyInstance)-[:HAS_ENTITY]->(e:EntityInstance)
            WHERE e.alias IS NOT NULL AND (
                toLower(e.alias) = $raw_alias
                OR toLower(replace(replace(e.alias, " ", "-"), "_", "-")) = $slug_alias
                OR toLower(e.alias) = $spaced_alias
                OR e.alias =~ $alias_pattern
            )
            RETURN i.instance_id AS instance_id
            ORDER BY i.updated_at DESC
            LIMIT 1
            """,
            raw_alias=raw_alias,
            slug_alias=normalized_slug,
            spaced_alias=spaced_alias,
            alias_pattern=alias_pattern,
        )
        record = await result.single()
        if not record or not record.get("instance_id"):
            raise ValueError("Ontology instance not found for slug alias")
        return await self.get_instance(record["instance_id"])

    async def _entity_ids_for_instances(
        self, tx: AsyncTransaction, instance_ids: Sequence[str]
    ) -> set[str]:
        result = await tx.run(
            """
            MATCH (i:OntologyInstance)-[:HAS_ENTITY]->(e:EntityInstance)
            WHERE i.instance_id IN $instance_ids
            RETURN e.entity_instance_id AS entity_id
            """,
            instance_ids=instance_ids,
        )
        rows = await result.data()
        return {row["entity_id"] for row in rows if row.get("entity_id")}

    async def _event_ids_for_instances(
        self, tx: AsyncTransaction, instance_ids: Sequence[str]
    ) -> set[str]:
        result = await tx.run(
            """
            MATCH (i:OntologyInstance)-[:HAS_EVENT]->(event:Event)
            WHERE i.instance_id IN $instance_ids
            RETURN event.event_id AS event_id
            """,
            instance_ids=instance_ids,
        )
        rows = await result.data()
        return {row["event_id"] for row in rows if row.get("event_id")}

    async def _delete_entity_relationships(
        self, tx: AsyncTransaction, *, entity_ids: set[str]
    ) -> int:
        if not entity_ids:
            return 0
        result = await tx.run(
            """
            MATCH (source:EntityInstance)-[rel:RELATES_TO]->(target:EntityInstance)
            WHERE source.entity_instance_id IN $entity_ids
               OR target.entity_instance_id IN $entity_ids
            WITH count(rel) AS rel_count, collect(rel) AS rels
            FOREACH (r IN rels | DELETE r)
            RETURN rel_count AS rel_count
            """,
            entity_ids=list(entity_ids),
        )
        record = await result.single()
        return int(record["rel_count"]) if record and record.get("rel_count") else 0

    async def _prune_timeline_entity_references(
        self, tx: AsyncTransaction, *, entity_ids: set[str]
    ) -> int:
        if not entity_ids:
            return 0
        result = await tx.run(
            """
            MATCH (event:Event)
            WHERE event.source_entity_id IN $entity_ids
               OR any(entityId IN $entity_ids WHERE entityId IN coalesce(event.related_entity_ids, []))
            RETURN event.event_id AS event_id,
                   event.source_entity_id AS source_entity_id,
                   event.related_entity_ids AS related_entity_ids
            """,
            entity_ids=list(entity_ids),
        )
        rows = await result.data()
        payload: list[dict[str, Any]] = []
        for row in rows:
            event_id = row.get("event_id")
            if not event_id:
                continue
            source_entity = row.get("source_entity_id")
            related_entities = [
                value
                for value in (row.get("related_entity_ids") or [])
                if value not in entity_ids
            ]
            updated_source = None if source_entity in entity_ids else source_entity
            if (
                updated_source != source_entity
                or related_entities != (row.get("related_entity_ids") or [])
            ):
                payload.append(
                    {
                        "event_id": event_id,
                        "source_entity_id": updated_source,
                        "related_entity_ids": related_entities,
                    }
                )
        if payload:
            await tx.run(
                """
                UNWIND $payload AS item
                MATCH (event:Event {event_id: item.event_id})
                SET event.created_from_entity_id = item.source_entity_id,
                    event.source_entity_id = item.source_entity_id,
                    event.related_entity_ids = item.related_entity_ids
                """,
                payload=payload,
            )
        return len(payload)

    async def _delete_timeline_events_for_entities(
        self, tx: AsyncTransaction, *, entity_ids: set[str]
    ) -> dict[str, int]:
        if not entity_ids:
            return {"events_deleted": 0, "event_chunks_deleted": 0}

        event_result = await tx.run(
            """
            MATCH (event:Event)
            WHERE event.source_entity_id IN $entity_ids
               OR event.created_from_entity_id IN $entity_ids
               OR any(entityId IN $entity_ids WHERE entityId IN coalesce(event.related_entity_ids, []))
            RETURN event.event_id AS event_id
            """,
            entity_ids=list(entity_ids),
        )
        event_rows = await event_result.data()
        event_ids = {row["event_id"] for row in event_rows if row.get("event_id")}
        if not event_ids:
            return {"events_deleted": 0, "event_chunks_deleted": 0}

        chunk_count_result = await tx.run(
            """
            MATCH (event:Event)-[:HAS_CHUNK]->(chunk:EntityChunk)
            WHERE event.event_id IN $event_ids
            RETURN count(chunk) AS chunk_count
            """,
            event_ids=list(event_ids),
        )
        chunk_count_record = await chunk_count_result.single()
        event_chunk_count = (
            int(chunk_count_record["chunk_count"])
            if chunk_count_record and chunk_count_record.get("chunk_count")
            else 0
        )

        await self._prune_timeline_references(
            tx,
            instance_ids=[],
            entity_ids=entity_ids,
            event_ids=event_ids,
        )

        await tx.run(
            """
            MATCH (event:Event)-[:HAS_CHUNK]->(chunk:EntityChunk)
            WHERE event.event_id IN $event_ids
            DETACH DELETE chunk
            """,
            event_ids=list(event_ids),
        )
        await tx.run(
            """
            MATCH (event:Event)
            WHERE event.event_id IN $event_ids
            DETACH DELETE event
            """,
            event_ids=list(event_ids),
        )

        return {
            "events_deleted": len(event_ids),
            "event_chunks_deleted": event_chunk_count,
        }

    async def _prune_timeline_references(
        self,
        tx: AsyncTransaction,
        *,
        instance_ids: list[str],
        entity_ids: set[str],
        event_ids: set[str],
    ) -> None:
        instance_set = set(instance_ids)
        entity_set = set(entity_ids)
        timeline_set = set(event_ids)
        result = await tx.run(
            """
            MATCH (event:Event)
            WHERE NOT event.instance_id IN $instance_ids AND (
                event.source_instance_id IN $instance_ids
                OR any(instId IN $instance_ids WHERE instId IN coalesce(event.related_instance_ids, []))
                OR any(entityId IN $entity_ids WHERE entityId IN coalesce(event.related_entity_ids, []))
                OR event.before_event_id IN $event_ids
                OR event.after_event_id IN $event_ids
            )
            RETURN event.event_id AS event_id,
                   event.source_instance_id AS source_instance_id,
                   event.source_entity_id AS source_entity_id,
                   event.related_instance_ids AS related_instance_ids,
                   event.related_entity_ids AS related_entity_ids,
                   event.before_event_id AS before_event_id,
                   event.after_event_id AS after_event_id
            """,
            instance_ids=instance_ids,
            entity_ids=list(entity_ids),
            event_ids=list(event_ids),
        )
        rows = await result.data()
        payload: list[dict[str, Any]] = []
        for row in rows:
            event_id = row.get("event_id")
            if not event_id:
                continue
            source_instance = row.get("source_instance_id")
            created_from_instance = (
                None if source_instance in instance_set else source_instance
            )
            source_entity = row.get("source_entity_id")
            created_from_entity = (
                None
                if source_entity in entity_set or created_from_instance is None
                else source_entity
            )
            related_instances = [
                value
                for value in (row.get("related_instance_ids") or [])
                if value not in instance_set
            ]
            related_entities = [
                value
                for value in (row.get("related_entity_ids") or [])
                if value not in entity_set
            ]
            before_event = (
                None
                if row.get("before_event_id") in timeline_set
                else row.get("before_event_id")
            )
            after_event = (
                None
                if row.get("after_event_id") in timeline_set
                else row.get("after_event_id")
            )
            if (
                created_from_instance != source_instance
                or created_from_entity != source_entity
                or related_instances != (row.get("related_instance_ids") or [])
                or related_entities != (row.get("related_entity_ids") or [])
                or before_event != row.get("before_event_id")
                or after_event != row.get("after_event_id")
            ):
                payload.append(
                    {
                        "event_id": event_id,
                        "created_from_instance_id": created_from_instance,
                        "created_from_entity_id": created_from_entity,
                        "related_instance_ids": related_instances,
                        "related_entity_ids": related_entities,
                        "before_event_id": before_event,
                        "after_event_id": after_event,
                    }
                )
        if payload:
            await tx.run(
                """
                UNWIND $payload AS item
                MATCH (event:Event {event_id: item.event_id})
                SET event.created_from_instance_id = item.created_from_instance_id,
                    event.created_from_entity_id = item.created_from_entity_id,
                    event.source_instance_id = item.created_from_instance_id,
                    event.source_entity_id = item.created_from_entity_id,
                    event.related_instance_ids = item.related_instance_ids,
                    event.related_entity_ids = item.related_entity_ids,
                    event.before_event_id = item.before_event_id,
                    event.after_event_id = item.after_event_id
                """,
                payload=payload,
            )

    async def _remove_cross_instance_links(
        self, tx: AsyncTransaction, *, instance_ids: list[str]
    ) -> None:
        result = await tx.run(
            """
            MATCH (e:EntityInstance)
            WHERE NOT e.instance_id IN $instance_ids AND (
                any(instId IN $instance_ids WHERE coalesce(e.text, "") CONTAINS instId)
                OR any(instId IN $instance_ids WHERE coalesce(e.autogenerated_text, "") CONTAINS instId)
                OR any(instId IN $instance_ids WHERE coalesce(e.text_linked, "") CONTAINS instId)
                OR any(instId IN $instance_ids WHERE coalesce(e.autogenerated_text_linked, "") CONTAINS instId)
            )
            RETURN e.entity_instance_id AS entity_id,
                   e.text AS text,
                   e.autogenerated_text AS autogenerated_text,
                   e.text_linked AS text_linked,
                   e.autogenerated_text_linked AS autogenerated_text_linked
            """,
            instance_ids=instance_ids,
        )
        rows = await result.data()
        payload: list[dict[str, Any]] = []
        for row in rows:
            entity_id = row.get("entity_id")
            if not entity_id:
                continue
            cleaned_text = _strip_links_to_instances(row.get("text"), instance_ids)
            cleaned_auto = _strip_links_to_instances(
                row.get("autogenerated_text"), instance_ids
            )
            cleaned_text_linked = _strip_links_to_instances(
                row.get("text_linked"), instance_ids
            )
            cleaned_auto_linked = _strip_links_to_instances(
                row.get("autogenerated_text_linked"), instance_ids
            )
            if (
                cleaned_text != row.get("text")
                or cleaned_auto != row.get("autogenerated_text")
                or cleaned_text_linked != row.get("text_linked")
                or cleaned_auto_linked != row.get("autogenerated_text_linked")
            ):
                payload.append(
                    {
                        "entity_id": entity_id,
                        "text": cleaned_text,
                        "autogenerated_text": cleaned_auto,
                        "text_linked": cleaned_text_linked,
                        "autogenerated_text_linked": cleaned_auto_linked,
                    }
                )
        if payload:
            await tx.run(
                """
                UNWIND $payload AS item
                MATCH (e:EntityInstance {entity_instance_id: item.entity_id})
                SET e.text = item.text,
                    e.text_linked = item.text_linked,
                    e.autogenerated_text = item.autogenerated_text,
                    e.autogenerated_text_linked = item.autogenerated_text_linked
                """,
                payload=payload,
            )

    async def delete_instances(self, instance_ids: Sequence[str]) -> None:
        normalized_ids: list[str] = []
        for inst in instance_ids:
            cleaned = _normalize_optional_str(inst)
            if cleaned and cleaned not in normalized_ids:
                normalized_ids.append(cleaned)
        instance_list = normalized_ids
        if not instance_list:
            return
        tx = await self.graph_session.begin_transaction()
        try:
            entity_ids = await self._entity_ids_for_instances(tx, instance_list)
            event_ids = await self._event_ids_for_instances(
                tx, instance_list
            )

            await self._delete_entity_relationships(tx, entity_ids=entity_ids)
            await self._prune_timeline_references(
                tx,
                instance_ids=instance_list,
                entity_ids=entity_ids,
                event_ids=event_ids,
            )
            await self._remove_cross_instance_links(
                tx, instance_ids=list(instance_list)
            )

            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_ENTITY]->(e:EntityInstance)-[:HAS_CHUNK]->(chunk:EntityChunk)
                WHERE i.instance_id IN $instance_ids
                DETACH DELETE chunk
                """,
                instance_ids=instance_list,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_ENTITY]->(e:EntityInstance)
                WHERE i.instance_id IN $instance_ids
                DETACH DELETE e
                """,
                instance_ids=instance_list,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_EVENT]->(event:Event)-[:HAS_CHUNK]->(chunk:EntityChunk)
                WHERE i.instance_id IN $instance_ids
                DETACH DELETE chunk
                """,
                instance_ids=instance_list,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_EVENT]->(event:Event)
                WHERE i.instance_id IN $instance_ids
                DETACH DELETE event
                """,
                instance_ids=instance_list,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance)
                WHERE i.instance_id IN $instance_ids
                DETACH DELETE i
                """,
                instance_ids=instance_list,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

    async def delete_instance(self, instance_id: str) -> None:
        await self.delete_instances([instance_id])

    async def clear_instance_content_by_entity_types(
        self,
        *,
        ontology_id: int,
        entity_definition_ids: Sequence[int] | None = None,
        entity_type_names: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        ontology = await self.repository.get(ontology_id)
        if ontology is None:
            raise ValueError("Ontology not found")

        definitions = await self._load_entity_definitions(ontology_id)
        valid_definition_ids = set(definitions.keys())
        names_to_ids = {
            definition_data["entity"].name.strip().lower(): definition_id
            for definition_id, definition_data in definitions.items()
            if definition_data.get("entity") and definition_data["entity"].name
        }

        normalized_ids: list[int] = []
        for definition_id in entity_definition_ids or []:
            if definition_id <= 0:
                continue
            if definition_id not in normalized_ids:
                normalized_ids.append(definition_id)

        missing_names: list[str] = []
        for raw_name in entity_type_names or []:
            cleaned = (raw_name or "").strip()
            if not cleaned:
                continue
            matched_id = names_to_ids.get(cleaned.lower())
            if matched_id is None:
                missing_names.append(cleaned)
                continue
            if matched_id not in normalized_ids:
                normalized_ids.append(matched_id)

        if not normalized_ids:
            raise ValueError(
                "Provide at least one valid entity definition id or entity type name"
            )

        missing_ids = [
            definition_id
            for definition_id in normalized_ids
            if definition_id not in valid_definition_ids
        ]
        if missing_ids:
            raise ValueError(
                "Entity definition(s) not found in ontology: "
                + ", ".join(str(definition_id) for definition_id in sorted(missing_ids))
            )
        if missing_names:
            raise ValueError(
                "Entity type name(s) not found in ontology: "
                + ", ".join(sorted(missing_names))
            )

        chunk_count = 0
        relationships_deleted = 0
        timeline_delete_summary = {
            "events_deleted": 0,
            "event_chunks_deleted": 0,
        }
        empty_instance_ids: list[str] = []

        tx = await self.graph_session.begin_transaction()
        try:
            target_result = await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_ENTITY]->(e:EntityInstance)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                  AND toInteger(e.entity_definition_id) IN $definition_ids
                RETURN e.entity_instance_id AS entity_id,
                       i.instance_id AS instance_id
                """,
                ontology_id=ontology_id,
                definition_ids=normalized_ids,
            )
            target_rows = await target_result.data()
            target_entity_ids = {
                row["entity_id"] for row in target_rows if row.get("entity_id")
            }
            affected_instance_ids = {
                row["instance_id"] for row in target_rows if row.get("instance_id")
            }

            if target_entity_ids:
                chunk_count_result = await tx.run(
                    """
                    MATCH (e:EntityInstance)-[:HAS_CHUNK]->(chunk:EntityChunk)
                    WHERE e.entity_instance_id IN $entity_ids
                    RETURN count(chunk) AS chunk_count
                    """,
                    entity_ids=list(target_entity_ids),
                )
                chunk_record = await chunk_count_result.single()
                chunk_count = (
                    int(chunk_record["chunk_count"])
                    if chunk_record and chunk_record.get("chunk_count")
                    else 0
                )

                relationships_deleted = await self._delete_entity_relationships(
                    tx, entity_ids=target_entity_ids
                )
                timeline_delete_summary = (
                    await self._delete_timeline_events_for_entities(
                        tx, entity_ids=target_entity_ids
                    )
                )

                await tx.run(
                    """
                    MATCH (e:EntityInstance)-[:HAS_CHUNK]->(chunk:EntityChunk)
                    WHERE e.entity_instance_id IN $entity_ids
                    DETACH DELETE chunk
                    """,
                    entity_ids=list(target_entity_ids),
                )
                await tx.run(
                    """
                    MATCH (e:EntityInstance)
                    WHERE e.entity_instance_id IN $entity_ids
                    DETACH DELETE e
                    """,
                    entity_ids=list(target_entity_ids),
                )

            empty_instance_result = await tx.run(
                """
                MATCH (i:OntologyInstance)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                OPTIONAL MATCH (i)-[:HAS_ENTITY]->(entity:EntityInstance)
                WITH i, count(entity) AS remaining_entities
                OPTIONAL MATCH (i)-[:HAS_EVENT]->(event:Event)
                WITH i, remaining_entities, count(event) AS remaining_events
                WHERE remaining_entities = 0 AND remaining_events = 0
                RETURN i.instance_id AS instance_id
                """,
                ontology_id=ontology_id,
            )
            empty_rows = await empty_instance_result.data()
            empty_instance_ids = [
                row["instance_id"] for row in empty_rows if row.get("instance_id")
            ]

            if empty_instance_ids:
                await tx.run(
                    """
                    MATCH (i:OntologyInstance)
                    WHERE i.instance_id IN $instance_ids
                    DETACH DELETE i
                    """,
                    instance_ids=empty_instance_ids,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        sql_instances_deleted = 0
        if empty_instance_ids:
            delete_result = await self.sql_session.execute(
                delete(SqlOntologyInstance).where(
                    SqlOntologyInstance.instance_id.in_(empty_instance_ids)
                )
            )
            await self.sql_session.commit()
            sql_instances_deleted = int(delete_result.rowcount or 0)

        return {
            "ontology_id": ontology_id,
            "entity_definition_ids": sorted(normalized_ids),
            "entity_type_names": [
                definitions[definition_id]["entity"].name
                for definition_id in sorted(normalized_ids)
            ],
            "instances_affected": len(affected_instance_ids),
            "entities_deleted": len(target_entity_ids),
            "chunks_deleted": chunk_count,
            "relationships_deleted": relationships_deleted,
            "instances_deleted": len(empty_instance_ids),
            "sql_instances_deleted": sql_instances_deleted,
            # Kept for backward compatibility; event references are no longer pruned.
            "timeline_events_updated": 0,
            "timeline_events_deleted": timeline_delete_summary["events_deleted"],
            "timeline_event_chunks_deleted": timeline_delete_summary[
                "event_chunks_deleted"
            ],
        }

    async def clear_timeline_events_by_ontology(
        self,
        *,
        ontology_id: int,
    ) -> dict[str, Any]:
        ontology = await self.repository.get(ontology_id)
        if ontology is None:
            raise ValueError("Ontology not found")

        tx = await self.graph_session.begin_transaction()
        try:
            event_result = await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_EVENT]->(event:Event)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                RETURN i.instance_id AS instance_id,
                       event.event_id AS event_id
                """,
                ontology_id=ontology_id,
            )
            event_rows = await event_result.data()
            target_event_ids = {
                row["event_id"] for row in event_rows if row.get("event_id")
            }
            affected_instance_ids = {
                row["instance_id"] for row in event_rows if row.get("instance_id")
            }

            if not target_event_ids:
                return {
                    "ontology_id": ontology_id,
                    "instances_affected": 0,
                    "timeline_events_deleted": 0,
                    "timeline_event_chunks_deleted": 0,
                }

            chunk_count_result = await tx.run(
                """
                MATCH (event:Event)-[:HAS_CHUNK]->(chunk:EntityChunk)
                WHERE event.event_id IN $event_ids
                RETURN count(chunk) AS chunk_count
                """,
                event_ids=list(target_event_ids),
            )
            chunk_count_record = await chunk_count_result.single()
            chunk_count = (
                int(chunk_count_record["chunk_count"])
                if chunk_count_record and chunk_count_record.get("chunk_count")
                else 0
            )

            await self._prune_timeline_references(
                tx,
                instance_ids=[],
                entity_ids=set(),
                event_ids=target_event_ids,
            )

            await tx.run(
                """
                MATCH (event:Event)-[:HAS_CHUNK]->(chunk:EntityChunk)
                WHERE event.event_id IN $event_ids
                DETACH DELETE chunk
                """,
                event_ids=list(target_event_ids),
            )
            await tx.run(
                """
                MATCH (event:Event)
                WHERE event.event_id IN $event_ids
                DETACH DELETE event
                """,
                event_ids=list(target_event_ids),
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        return {
            "ontology_id": ontology_id,
            "instances_affected": len(affected_instance_ids),
            "timeline_events_deleted": len(target_event_ids),
            "timeline_event_chunks_deleted": chunk_count,
        }

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
            if payload.events is not None:
                tx = await self.graph_session.begin_transaction()
                try:
                    await self._replace_timeline_events_in_tx(
                        tx,
                        instance_id=instance_id,
                        ontology_id=current.ontology_id,
                        events=payload.events,
                    )
                except Exception:
                    await tx.rollback()
                    await tx.close()
                    raise
                else:
                    await tx.commit()
                    await tx.close()
            if payload.scenes is not None:
                await self._replace_scenes_for_instance(instance_id, payload.scenes)
            instance = await self.get_instance(instance_id)

            # Notifications are owned by ShreckRPG; Shrecknet keeps core update only.
            from app.tasks.ontology_links import link_instance as link_instance_task

            link_instance_task.delay(instance.instance_id)
            return instance

        definitions = await self._load_entity_definitions(current.ontology_id)
        entities_payload = self._sanitize_entities_payload_for_update(
            payload.entities, definitions
        )
        self._validate_entities_payload(entities_payload, definitions)

        tx = await self.graph_session.begin_transaction()
        impacted_entity_ids: set[str] = set()
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
            for entity_payload in entities_payload:
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
                impacted_entity_ids.add(entity_node_id)

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
                        impacted_entity_ids.add(target_id)
                        impacted_entity_ids.add(entity_node_id)
            if payload.events is not None:
                await self._replace_timeline_events_in_tx(
                    tx,
                    instance_id=instance_id,
                    ontology_id=current.ontology_id,
                    events=payload.events,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

            if payload.scenes is not None:
                await self._replace_scenes_for_instance(instance_id, payload.scenes)

            instance = await self.get_instance(instance_id)

            # Notifications are owned by ShreckRPG; Shrecknet keeps core update only.

            from app.tasks.ontology_links import link_instance as link_instance_task
            from app.tasks.neo4j_embedding import embed_nodes as embed_nodes_task

            link_instance_task.delay(instance.instance_id)
            if impacted_entity_ids:
                embed_nodes_task.delay(current.ontology_id, sorted(impacted_entity_ids))
            
            return instance

    def _sanitize_entities_payload_for_update(
        self,
        entities: Sequence[OntologyInstanceEntityCreate],
        definitions: dict[int, dict[str, Any]],
    ) -> list[OntologyInstanceEntityCreate]:
        """Drop stale property/relationship ids before strict validation."""
        sanitized_entities: list[OntologyInstanceEntityCreate] = []
        for entity in entities:
            definition = definitions.get(entity.definition_id)
            if definition is None:
                sanitized_entities.append(entity)
                continue

            valid_property_ids = set(definition["properties"].keys())
            valid_relationship_ids = set(definition["relationships"].keys())
            filtered_properties = [
                prop
                for prop in entity.properties
                if prop.definition_id in valid_property_ids
            ]
            filtered_relationships = [
                rel
                for rel in entity.relationships
                if rel.definition_id in valid_relationship_ids
            ]

            dropped_property_ids = sorted(
                {
                    prop.definition_id
                    for prop in entity.properties
                    if prop.definition_id not in valid_property_ids
                }
            )
            dropped_relationship_ids = sorted(
                {
                    rel.definition_id
                    for rel in entity.relationships
                    if rel.definition_id not in valid_relationship_ids
                }
            )

            if dropped_property_ids:
                logger.warning(
                    "Dropping invalid property definitions during instance update "
                    "for alias '%s': %s",
                    entity.alias,
                    dropped_property_ids,
                )
            if dropped_relationship_ids:
                logger.warning(
                    "Dropping invalid relationship definitions during instance update "
                    "for alias '%s': %s",
                    entity.alias,
                    dropped_relationship_ids,
                )

            sanitized_entities.append(
                entity.model_copy(
                    update={
                        "properties": filtered_properties,
                        "relationships": filtered_relationships,
                    }
                )
            )
        return sanitized_entities

    async def list_timeline_events(self, instance_id: str) -> list[TimelineEventRead]:
        await self._get_instance_ontology_id(instance_id)
        return await self._timeline_events_for_instance(instance_id)

    async def get_timeline_event(
        self, instance_id: str, event_id: str
    ) -> TimelineEventRead:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(event)
            WHERE type(rel) = 'HAS_EVENT'
              AND 'Event' IN labels(event)
              AND event.event_id = $event_id
            RETURN event
            """,
            instance_id=instance_id,
            event_id=event_id,
        )
        record = await result.single()
        if not record or not record.get("event"):
            raise ValueError("Timeline event not found")
        return self._timeline_node_to_read(record["event"])

    async def create_timeline_event(
        self, instance_id: str, payload: TimelineEventCreate
    ) -> TimelineEventRead:
        ontology_id = await self._get_instance_ontology_id(instance_id)
        existing_ids = await self._event_ids(instance_id)
        event_id = _normalize_optional_str(payload.event_id) or str(uuid4())
        if event_id in existing_ids:
            raise ValueError(f"Event id '{event_id}' already exists")

        relations = [rel.model_dump() for rel in payload.relations]
        for relation in relations:
            target_id = _normalize_optional_str(relation.get("target_event_id"))
            if not target_id:
                raise ValueError("Relation target_event_id cannot be empty")
            if target_id == event_id:
                raise ValueError("Event cannot reference itself")
            if target_id not in existing_ids:
                raise ValueError(f"Related event '{target_id}' does not exist for this instance")
            relation["target_event_id"] = target_id

        title = payload.title.strip()
        description = payload.description.strip()
        source_entity_id = _normalize_optional_str(payload.source_entity_id)
        source = _normalize_optional_str(payload.source)
        involves_entity_ids = _normalize_id_list(payload.involves_entity_ids)
        created_at = _format_dt(datetime.utcnow())
        text_payload = _build_timeline_text(
            title,
            description,
            source,
            None,
            involves_entity_ids,
            None,
            None,
        )

        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})
                CREATE (i)-[:HAS_EVENT]->(event:Event {
                    event_id: $event_id,
                    entity_instance_id: $event_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $title,
                    alias: $title,
                    title: $title,
                    description: $description,
                    source: $source,
                    source_entity_id: $source_entity_id,
                    involves_entity_ids: $involves_entity_ids,
                    relations_json: $relations_json,
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
                event_id=event_id,
                title=title,
                description=description,
                source=source,
                source_entity_id=source_entity_id,
                involves_entity_ids=involves_entity_ids,
                relations_json=json.dumps(relations, ensure_ascii=False),
                created_at=created_at,
                text=text_payload,
            )
            await self._apply_timeline_event_edges(
                tx,
                event_id=event_id,
                instance_id=instance_id,
                source_entity_id=source_entity_id,
                involves_entity_ids=involves_entity_ids,
                relations=relations,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
        
        # Notifications are owned by ShreckRPG; Shrecknet keeps core event update only.

        return await self.get_timeline_event(instance_id, event_id)

    async def update_timeline_event(
        self,
        instance_id: str,
        event_id: str,
        payload: TimelineEventUpdate,
    ) -> TimelineEventRead:
        current_event = await self.get_timeline_event(instance_id, event_id)
        existing_ids = await self._event_ids(instance_id)
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
        if payload.source is not None:
            updates["source"] = _normalize_optional_str(payload.source)
        if payload.source_entity_id is not None:
            updates["source_entity_id"] = _normalize_optional_str(payload.source_entity_id)
        if payload.involves_entity_ids is not None:
            updates["involves_entity_ids"] = _normalize_id_list(payload.involves_entity_ids)
        if payload.relations is not None:
            relations = [rel.model_dump() for rel in payload.relations]
            for relation in relations:
                target_id = _normalize_optional_str(relation.get("target_event_id"))
                if not target_id:
                    raise ValueError("Relation target_event_id cannot be empty")
                if target_id == event_id:
                    raise ValueError("Event cannot reference itself")
                if target_id not in existing_ids:
                    raise ValueError(f"Related event '{target_id}' does not exist")
                relation["target_event_id"] = target_id
            updates["relations_json"] = json.dumps(relations, ensure_ascii=False)

        now_str = _format_dt(datetime.utcnow())
        final_title = updates.get("title", current_event.title)
        final_description = updates.get("description", current_event.description or "")
        final_source = updates.get("source", current_event.source)
        final_source_entity = updates.get("source_entity_id", current_event.source_entity_id)
        final_involves = updates.get("involves_entity_ids", current_event.involves_entity_ids or [])
        if "relations_json" in updates:
            final_relations = json.loads(updates["relations_json"])
        else:
            final_relations = [rel.model_dump() for rel in current_event.relations]

        text_payload = _build_timeline_text(
            final_title,
            final_description,
            final_source,
            None,
            final_involves,
            None,
            None,
        )
        updates["text"] = text_payload
        updates["autogenerated_text"] = text_payload
        updates["last_updated_date"] = now_str
        updates["is_embedded"] = False

        params = {
            "instance_id": instance_id,
            "event_id": event_id,
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
                MATCH (:OntologyInstance {{instance_id: $instance_id}})-[:HAS_EVENT]->(event:Event {{event_id: $event_id}})
                SET {set_clause}
                """,
                **params,
            )
            await self._clear_timeline_event_edges(
                tx, event_id=event_id
            )
            await self._apply_timeline_event_edges(
                tx,
                event_id=event_id,
                instance_id=instance_id,
                source_entity_id=final_source_entity,
                involves_entity_ids=final_involves,
                relations=final_relations,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
        
        # Notifications are owned by ShreckRPG; Shrecknet keeps core event update only.

        return await self.get_timeline_event(instance_id, event_id)

    async def delete_timeline_event(
        self, instance_id: str, event_id: str
    ) -> None:
        await self.get_timeline_event(instance_id, event_id)
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_EVENT]->(event:Event {event_id: $event_id})-[:HAS_CHUNK]->(chunk:EntityChunk)
                DETACH DELETE chunk
                """,
                instance_id=instance_id,
                event_id=event_id,
            )
            await tx.run(
                """
                MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_EVENT]->(event:Event {event_id: $event_id})
                DETACH DELETE event
                """,
                instance_id=instance_id,
                event_id=event_id,
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
            "MATCH (inst:OntologyInstance)-[:HAS_EVENT]->(event:Event)"
        ]
        params: dict[str, Any] = {}
        if instance_id:
            clauses.append("WHERE inst.instance_id = $instance_id")
            params["instance_id"] = instance_id
        clauses.append(
            """
            RETURN event.event_id AS event_id,
                   inst.instance_id AS instance_id,
                   properties(event)['created_from_instance_id'] AS created_from_instance_id,
                   properties(event)['created_from_entity_id'] AS created_from_entity_id,
                   properties(event)['source_instance_id'] AS source_instance_id,
                   properties(event)['source_entity_id'] AS source_entity_id,
                     properties(event)['involves_entity_ids'] AS involves_entity_ids,
                   properties(event)['related_instance_ids'] AS related_instance_ids,
                   properties(event)['related_entity_ids'] AS related_entity_ids,
                   properties(event)['relations_json'] AS relations_json,
                   properties(event)['before_event_id'] AS before_event_id,
                   properties(event)['after_event_id'] AS after_event_id
            """
        )
        query = "\n".join(clauses)
        result = await self.graph_session.run(query, params)
        rows = await result.data()

        entities_result = await self.graph_session.run(
            """
            MATCH (inst:OntologyInstance)-[:HAS_ENTITY]->(entity:EntityInstance)
            RETURN inst.instance_id AS instance_id,
                   collect(DISTINCT entity.entity_instance_id) AS entity_ids
            """
        )
        entity_rows = await entities_result.data()
        entity_ids_by_instance: dict[str, list[str]] = {}
        known_entity_ids: set[str] = set()
        for entity_row in entity_rows:
            row_instance_id = _normalize_optional_str(entity_row.get("instance_id"))
            if not row_instance_id:
                continue
            ids = [
                str(value)
                for value in (entity_row.get("entity_ids") or [])
                if value is not None and str(value).strip()
            ]
            if not ids:
                continue
            deduped = sorted(set(ids))
            entity_ids_by_instance[row_instance_id] = deduped
            known_entity_ids.update(deduped)

        processed = 0
        failed = 0
        for row in rows:
            relations = self._legacy_event_row_to_relations(row)

            row_instance_id = _normalize_optional_str(row.get("instance_id")) or ""
            related_ids_raw = row.get("involves_entity_ids") or row.get("related_entity_ids") or []
            if isinstance(related_ids_raw, str):
                related_ids_raw = [related_ids_raw]
            related_ids = [
                str(rid)
                for rid in related_ids_raw
                if rid is not None and str(rid).strip() and str(rid) in known_entity_ids
            ]

            source_entity_id = _normalize_optional_str(
                row.get("created_from_entity_id") or row.get("source_entity_id")
            )
            if source_entity_id and source_entity_id not in known_entity_ids:
                source_entity_id = None
            if not source_entity_id:
                source_instance_id = _normalize_optional_str(
                    row.get("created_from_instance_id") or row.get("source_instance_id")
                )
                if source_instance_id:
                    candidates = entity_ids_by_instance.get(source_instance_id, [])
                    if len(candidates) == 1:
                        source_entity_id = candidates[0]

            if not related_ids:
                related_instance_ids = row.get("related_instance_ids") or []
                if isinstance(related_instance_ids, str):
                    related_instance_ids = [related_instance_ids]
                recovered_related: list[str] = []
                for related_instance_id in related_instance_ids:
                    rid = _normalize_optional_str(related_instance_id)
                    if not rid:
                        continue
                    candidates = entity_ids_by_instance.get(rid, [])
                    if len(candidates) == 1:
                        recovered_related.append(candidates[0])
                related_ids = recovered_related

            tx = await self.graph_session.begin_transaction()
            try:
                await self._clear_timeline_event_edges(
                    tx, event_id=row["event_id"]
                )
                await self._apply_timeline_event_edges(
                    tx,
                    event_id=row["event_id"],
                    instance_id=row["instance_id"],
                    source_entity_id=source_entity_id,
                    involves_entity_ids=[str(rid) for rid in related_ids if rid],
                    relations=relations,
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

    def _legacy_event_row_to_relations(
        self, row: dict[str, Any]
    ) -> list[dict[str, str]]:
        relations_raw = row.get("relations_json")
        relations: list[dict[str, str]] = []
        if isinstance(relations_raw, str) and relations_raw.strip():
            try:
                parsed = json.loads(relations_raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    relation_type = item.get("relation_type")
                    target_event_id = item.get("target_event_id")
                    if relation_type in {
                        "BEFORE",
                        "AFTER",
                        "DERIVED_FROM",
                        "RELATED_TO",
                    } and target_event_id:
                        relations.append(
                            {
                                "relation_type": str(relation_type),
                                "target_event_id": str(target_event_id),
                            }
                        )
        if relations:
            return relations

        fallback_pairs = (
            ("before_event_id", "BEFORE"),
            ("after_event_id", "AFTER"),
        )
        for field_name, relation_type in fallback_pairs:
            target_event_id = row.get(field_name)
            if target_event_id:
                relations.append(
                    {
                        "relation_type": relation_type,
                        "target_event_id": str(target_event_id),
                    }
                )
        return relations

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

    async def _entity_ids_for_instance(self, instance_id: str) -> set[str]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(entity:EntityInstance)
            RETURN entity.entity_instance_id AS entity_instance_id
            """,
            instance_id=instance_id,
        )
        rows = await result.data()
        return {
            str(row["entity_instance_id"])
            for row in rows
            if row.get("entity_instance_id") is not None
        }

    async def _scene_ids_for_instance(self, instance_id: str) -> set[str]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene)
            WHERE type(rel) = 'HAS_SCENE' AND 'Scene' IN labels(scene)
            RETURN scene.id AS scene_id
            """,
            instance_id=instance_id,
        )
        rows = await result.data()
        return {
            str(row["scene_id"])
            for row in rows
            if row.get("scene_id") is not None
        }

    async def _milestone_ids_for_scene(
        self, instance_id: str, scene_id: str
    ) -> set[str]:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[scene_rel]->(scene)-[contains_rel]->(milestone)
            WHERE type(scene_rel) = 'HAS_SCENE'
              AND 'Scene' IN labels(scene)
              AND scene.id = $scene_id
              AND type(contains_rel) = 'CONTAINS'
              AND 'Milestone' IN labels(milestone)
            RETURN milestone.id AS milestone_id
            """,
            instance_id=instance_id,
            scene_id=scene_id,
        )
        rows = await result.data()
        return {
            str(row["milestone_id"])
            for row in rows
            if row.get("milestone_id") is not None
        }

    async def _validate_scene_derived_from(
        self, *, instance_id: str, entity_instance_id: str
    ) -> None:
        known_ids = await self._entity_ids_for_instance(instance_id)
        if entity_instance_id not in known_ids:
            raise ValueError(
                "Scene derived_from.entity_instance_id must reference an existing entity in the same instance"
            )

    async def _validate_milestone_derived_from(
        self, *, instance_id: str, entity_instance_id: str
    ) -> None:
        known_ids = await self._entity_ids_for_instance(instance_id)
        if entity_instance_id not in known_ids:
            raise ValueError(
                "Milestone derived_from.entity_instance_id must reference an existing entity in the same instance"
            )

    def _build_local_order_pairs(
        self,
        milestone_ids: set[str],
        milestone_payloads: list[MilestoneCreate],
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for payload in milestone_payloads:
            milestone_id = _normalize_optional_str(payload.id)
            if not milestone_id:
                continue
            followed = payload.local_order.followed_by_milestone_id
            preceded_by = payload.local_order.preceded_by_milestone_id
            if followed:
                if followed not in milestone_ids:
                    raise ValueError(
                        f"Milestone local order target '{followed}' does not exist in scene payload"
                    )
                if followed == milestone_id:
                    raise ValueError("Milestone cannot follow itself")
                pairs.append((milestone_id, followed))
            if preceded_by:
                if preceded_by not in milestone_ids:
                    raise ValueError(
                        f"Milestone local order predecessor '{preceded_by}' does not exist in scene payload"
                    )
                if preceded_by == milestone_id:
                    raise ValueError("Milestone cannot be preceded by itself")
                pairs.append((preceded_by, milestone_id))

        outgoing: dict[str, str] = {}
        incoming: dict[str, str] = {}
        for source, target in pairs:
            if source in outgoing and outgoing[source] != target:
                raise ValueError(
                    f"Milestone '{source}' has multiple FOLLOWED_BY targets"
                )
            if target in incoming and incoming[target] != source:
                raise ValueError(
                    f"Milestone '{target}' has multiple PRECEDED_BY sources"
                )
            outgoing[source] = target
            incoming[target] = source
        return list({pair for pair in pairs})

    async def _validate_scene_milestones_payload(
        self, *, instance_id: str, milestones: list[MilestoneCreate]
    ) -> None:
        if len(milestones) < 2:
            raise ValueError("Scene must contain at least two milestones")
        milestone_ids: set[str] = set()
        begin_count = 0
        end_count = 0
        for milestone in milestones:
            milestone_id = _normalize_optional_str(milestone.id)
            if not milestone_id:
                raise ValueError("Every milestone in scene payload must include an id")
            if milestone_id in milestone_ids:
                raise ValueError(f"Duplicate milestone id '{milestone_id}' in scene")
            milestone_ids.add(milestone_id)

            if milestone.boundary_type == "begin":
                begin_count += 1
            if milestone.boundary_type == "end":
                end_count += 1

            await self._validate_milestone_derived_from(
                instance_id=instance_id,
                entity_instance_id=milestone.derived_from.entity_instance_id,
            )

        if begin_count != 1 or end_count != 1:
            raise ValueError(
                "Scene must include exactly one begin boundary milestone and one end boundary milestone"
            )

        self._build_local_order_pairs(milestone_ids, milestones)

    async def _milestone_node_to_read(
        self,
        *,
        node: Any,
        scene_id: str,
        derived_from_entity_id: str | None,
        relates_to: list[dict[str, Any]],
        local_order: dict[str, Any] | None,
    ) -> MilestoneRead:
        props = dict(node)
        return MilestoneRead(
            id=props.get("id") or "",
            scene_id=scene_id,
            instance_id=props.get("instance_id") or "",
            ontology_id=int(props.get("ontology_id") or 0),
            name=props.get("name") or "",
            description=props.get("description") or "",
            created_by_type=props.get("created_by_type") or "human",
            created_by_author=props.get("created_by_author") or "",
            temporal_type=props.get("temporal_type") or "other",
            boundary_type=props.get("boundary_type") or "none",
            local_order=local_order or {},
            derived_from={"entity_instance_id": derived_from_entity_id or ""},
            relates_to=relates_to,
            created_at=_parse_dt(props.get("created_at")),
            updated_at=_parse_dt(props.get("updated_at")),
        )

    async def _scene_node_to_read(self, *, node: Any) -> SceneRead:
        props = dict(node)
        scene_id = props.get("id") or ""

        derived_result = await self.graph_session.run(
            """
            MATCH (:Scene {id: $scene_id})-[:DERIVED_FROM]->(entity:EntityInstance)
            RETURN entity.entity_instance_id AS entity_instance_id
            LIMIT 1
            """,
            scene_id=scene_id,
        )
        derived_row = await derived_result.single()
        derived_from_entity_id = (
            str(derived_row["entity_instance_id"]) if derived_row else ""
        )

        local_order_result = await self.graph_session.run(
            """
            MATCH (scene:Scene {id: $scene_id})
            OPTIONAL MATCH (scene)-[:FOLLOWED_BY]->(followed:Scene)
            OPTIONAL MATCH (scene)-[:PRECEDED_BY]->(preceded:Scene)
            RETURN followed.id AS followed_by_scene_id,
                   preceded.id AS preceded_by_scene_id
            """,
            scene_id=scene_id,
        )
        local_order_row = await local_order_result.single()
        local_order = {
            "followed_by_scene_id": local_order_row.get("followed_by_scene_id")
            if local_order_row
            else None,
            "preceded_by_scene_id": local_order_row.get("preceded_by_scene_id")
            if local_order_row
            else None,
        }

        milestones = await self.list_milestones(props.get("instance_id") or "", scene_id)

        return SceneRead(
            id=scene_id,
            instance_id=props.get("instance_id") or "",
            ontology_id=int(props.get("ontology_id") or 0),
            name=props.get("name") or "",
            description=props.get("description") or "",
            created_by_type=props.get("created_by_type") or "human",
            created_by_author=props.get("created_by_author") or "",
            local_order=local_order,
            derived_from={"entity_instance_id": derived_from_entity_id},
            created_at=_parse_dt(props.get("created_at")),
            updated_at=_parse_dt(props.get("updated_at")),
            milestones=milestones,
        )

    async def list_scenes(self, instance_id: str) -> list[SceneRead]:
        await self._get_instance_ontology_id(instance_id)
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene)
            WHERE type(rel) = 'HAS_SCENE' AND 'Scene' IN labels(scene)
            RETURN scene
            ORDER BY scene.created_at ASC
            """,
            instance_id=instance_id,
        )
        rows = await result.data()
        scenes: list[SceneRead] = []
        for row in rows:
            scene_node = row.get("scene")
            if scene_node is None:
                continue
            scenes.append(await self._scene_node_to_read(node=scene_node))
        return scenes

    async def get_scene(self, instance_id: str, scene_id: str) -> SceneRead:
        await self._assert_scene_exists(instance_id=instance_id, scene_id=scene_id)
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene)
            WHERE type(rel) = 'HAS_SCENE'
              AND 'Scene' IN labels(scene)
              AND scene.id = $scene_id
            RETURN scene
            """,
            instance_id=instance_id,
            scene_id=scene_id,
        )
        record = await result.single()
        if not record or not record.get("scene"):
            raise ValueError("Scene not found")
        return await self._scene_node_to_read(node=record["scene"])

    async def _assert_scene_exists(self, *, instance_id: str, scene_id: str) -> None:
        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene)
            WHERE type(rel) = 'HAS_SCENE'
              AND 'Scene' IN labels(scene)
              AND scene.id = $scene_id
            RETURN count(scene) AS count
            """,
            instance_id=instance_id,
            scene_id=scene_id,
        )
        row = await result.single()
        if not row or int(row.get("count") or 0) == 0:
            raise ValueError("Scene not found")

    async def create_scene(self, instance_id: str, payload: SceneCreate) -> SceneRead:
        ontology_id = await self._get_instance_ontology_id(instance_id)
        scene_id = _normalize_optional_str(payload.id) or str(uuid4())
        if scene_id in await self._scene_ids_for_instance(instance_id):
            raise ValueError(f"Scene id '{scene_id}' already exists")

        await self._validate_scene_derived_from(
            instance_id=instance_id,
            entity_instance_id=payload.derived_from.entity_instance_id,
        )
        await self._validate_scene_milestones_payload(
            instance_id=instance_id,
            milestones=payload.milestones,
        )

        now_str = _format_dt(datetime.utcnow())
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (inst:OntologyInstance {instance_id: $instance_id})
                CREATE (inst)-[:HAS_SCENE]->(scene:Scene {
                    id: $scene_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $name,
                    description: $description,
                    created_by_type: $created_by_type,
                    created_by_author: $created_by_author,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
                """,
                instance_id=instance_id,
                scene_id=scene_id,
                ontology_id=ontology_id,
                name=payload.name,
                description=payload.description,
                created_by_type=payload.created_by_type,
                created_by_author=payload.created_by_author,
                created_at=now_str,
                updated_at=now_str,
            )

            await tx.run(
                """
                MATCH (scene:Scene {id: $scene_id})
                MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                MERGE (scene)-[:DERIVED_FROM]->(entity)
                """,
                scene_id=scene_id,
                entity_instance_id=payload.derived_from.entity_instance_id,
            )

            followed_scene_id = payload.local_order.followed_by_scene_id
            preceded_scene_id = payload.local_order.preceded_by_scene_id
            if followed_scene_id:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    MATCH (target:Scene {id: $target_scene_id, instance_id: $instance_id})
                    MERGE (scene)-[:FOLLOWED_BY]->(target)
                    MERGE (target)-[:PRECEDED_BY]->(scene)
                    """,
                    scene_id=scene_id,
                    target_scene_id=followed_scene_id,
                    instance_id=instance_id,
                )
            if preceded_scene_id:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    MATCH (target:Scene {id: $target_scene_id, instance_id: $instance_id})
                    MERGE (scene)-[:PRECEDED_BY]->(target)
                    MERGE (target)-[:FOLLOWED_BY]->(scene)
                    """,
                    scene_id=scene_id,
                    target_scene_id=preceded_scene_id,
                    instance_id=instance_id,
                )

            milestone_ids: set[str] = set()
            for milestone in payload.milestones:
                milestone_id = _normalize_optional_str(milestone.id) or str(uuid4())
                milestone_ids.add(milestone_id)
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    CREATE (scene)-[:CONTAINS]->(milestone:Milestone {
                        id: $milestone_id,
                        scene_id: $scene_id,
                        instance_id: $instance_id,
                        ontology_id: $ontology_id,
                        name: $name,
                        description: $description,
                        created_by_type: $created_by_type,
                        created_by_author: $created_by_author,
                        temporal_type: $temporal_type,
                        boundary_type: $boundary_type,
                        relates_to_json: $relates_to_json,
                        created_at: $created_at,
                        updated_at: $updated_at
                    })
                    """,
                    scene_id=scene_id,
                    milestone_id=milestone_id,
                    instance_id=instance_id,
                    ontology_id=ontology_id,
                    name=milestone.name,
                    description=milestone.description,
                    created_by_type=milestone.created_by_type,
                    created_by_author=milestone.created_by_author,
                    temporal_type=milestone.temporal_type,
                    boundary_type=milestone.boundary_type,
                    relates_to_json=json.dumps(
                        [item.model_dump() for item in milestone.relates_to],
                        ensure_ascii=False,
                    ),
                    created_at=now_str,
                    updated_at=now_str,
                )
                await tx.run(
                    """
                    MATCH (milestone:Milestone {id: $milestone_id})
                    MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                    MERGE (milestone)-[:DERIVED_FROM]->(entity)
                    """,
                    milestone_id=milestone_id,
                    entity_instance_id=milestone.derived_from.entity_instance_id,
                )
                for relates in milestone.relates_to:
                    await tx.run(
                        """
                        MATCH (milestone:Milestone {id: $milestone_id})
                        MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                        MERGE (milestone)-[:RELATES_TO {label: $label}]->(entity)
                        """,
                        milestone_id=milestone_id,
                        entity_instance_id=relates.entity_instance_id,
                        label=relates.label,
                    )

            ordered_payload = []
            for milestone in payload.milestones:
                milestone_id = _normalize_optional_str(milestone.id)
                if milestone_id:
                    ordered_payload.append(milestone)
            for source, target in self._build_local_order_pairs(
                milestone_ids,
                ordered_payload,
            ):
                await tx.run(
                    """
                    MATCH (source:Milestone {id: $source_id, scene_id: $scene_id})
                    MATCH (target:Milestone {id: $target_id, scene_id: $scene_id})
                    MERGE (source)-[:FOLLOWED_BY]->(target)
                    MERGE (target)-[:PRECEDED_BY]->(source)
                    """,
                    scene_id=scene_id,
                    source_id=source,
                    target_id=target,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()
        from app.tasks.neo4j_embedding import embed_ontology as embed_ontology_task

        embed_ontology_task.delay(ontology_id=ontology_id, author_type="agent", author_id="scene-create")
        return await self.get_scene(instance_id, scene_id)

    async def _replace_scenes_for_instance(
        self, instance_id: str, scenes: list[SceneCreate]
    ) -> None:
        existing = await self.list_scenes(instance_id)
        for scene in existing:
            await self.delete_scene(instance_id, scene.id)
        for scene_payload in scenes:
            await self.create_scene(instance_id, scene_payload)

    async def update_scene(
        self, instance_id: str, scene_id: str, payload: SceneUpdate
    ) -> SceneRead:
        await self.get_scene(instance_id, scene_id)
        updates: dict[str, Any] = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.created_by_type is not None:
            updates["created_by_type"] = payload.created_by_type
        if payload.created_by_author is not None:
            updates["created_by_author"] = payload.created_by_author

        if payload.derived_from is not None:
            await self._validate_scene_derived_from(
                instance_id=instance_id,
                entity_instance_id=payload.derived_from.entity_instance_id,
            )

        params = {"scene_id": scene_id, "instance_id": instance_id}
        set_parts: list[str] = []
        if updates:
            updates["updated_at"] = _format_dt(datetime.utcnow())
            for field, value in updates.items():
                set_parts.append(f"scene.{field} = ${field}")
                params[field] = value

        tx = await self.graph_session.begin_transaction()
        try:
            if set_parts:
                await tx.run(
                    f"""
                                        MATCH (:OntologyInstance {{instance_id: $instance_id}})-[rel]->(scene)
                                        WHERE type(rel) = 'HAS_SCENE'
                                            AND 'Scene' IN labels(scene)
                                            AND scene.id = $scene_id
                    SET {', '.join(set_parts)}
                    """,
                    **params,
                )

            await tx.run(
                """
                MATCH (scene:Scene {id: $scene_id})-[rel:FOLLOWED_BY|PRECEDED_BY]->()
                DELETE rel
                """,
                scene_id=scene_id,
            )

            if payload.local_order and payload.local_order.followed_by_scene_id:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    MATCH (target:Scene {id: $target_scene_id, instance_id: $instance_id})
                    MERGE (scene)-[:FOLLOWED_BY]->(target)
                    MERGE (target)-[:PRECEDED_BY]->(scene)
                    """,
                    scene_id=scene_id,
                    target_scene_id=payload.local_order.followed_by_scene_id,
                    instance_id=instance_id,
                )
            if payload.local_order and payload.local_order.preceded_by_scene_id:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    MATCH (target:Scene {id: $target_scene_id, instance_id: $instance_id})
                    MERGE (scene)-[:PRECEDED_BY]->(target)
                    MERGE (target)-[:FOLLOWED_BY]->(scene)
                    """,
                    scene_id=scene_id,
                    target_scene_id=payload.local_order.preceded_by_scene_id,
                    instance_id=instance_id,
                )

            if payload.derived_from is not None:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})-[rel:DERIVED_FROM]->()
                    DELETE rel
                    """,
                    scene_id=scene_id,
                )
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                    MERGE (scene)-[:DERIVED_FROM]->(entity)
                    """,
                    scene_id=scene_id,
                    entity_instance_id=payload.derived_from.entity_instance_id,
                )

            milestones = await self.list_milestones(instance_id, scene_id)
            begin_count = sum(1 for milestone in milestones if milestone.boundary_type == "begin")
            end_count = sum(1 for milestone in milestones if milestone.boundary_type == "end")
            if len(milestones) < 2 or begin_count != 1 or end_count != 1:
                raise ValueError(
                    "Scene remains invalid after update: it must contain at least two milestones with one begin and one end boundary"
                )

            refreshed = await tx.run(
                """
                MATCH (scene:Scene {id: $scene_id})-[:DERIVED_FROM]->(:EntityInstance)
                RETURN count(*) AS derived_count
                """,
                scene_id=scene_id,
            )
            derived_count_record = await refreshed.single()
            if not derived_count_record or int(derived_count_record["derived_count"] or 0) != 1:
                raise ValueError("Scene must have exactly one DERIVED_FROM entity")
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        ontology_id = await self._get_instance_ontology_id(instance_id)
        from app.tasks.neo4j_embedding import embed_ontology as embed_ontology_task

        embed_ontology_task.delay(ontology_id=ontology_id, author_type="agent", author_id="scene-update")

        return await self.get_scene(instance_id, scene_id)

    async def delete_scene(self, instance_id: str, scene_id: str) -> None:
        await self.get_scene(instance_id, scene_id)
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                                MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene)
                                WHERE type(rel) = 'HAS_SCENE'
                                    AND 'Scene' IN labels(scene)
                                    AND scene.id = $scene_id
                DETACH DELETE scene
                """,
                instance_id=instance_id,
                scene_id=scene_id,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        ontology_id = await self._get_instance_ontology_id(instance_id)
        from app.tasks.neo4j_embedding import embed_ontology as embed_ontology_task

        embed_ontology_task.delay(ontology_id=ontology_id, author_type="agent", author_id="scene-delete")

    async def list_milestones(
        self, instance_id: str, scene_id: str
    ) -> list[MilestoneRead]:
        await self._assert_scene_exists(instance_id=instance_id, scene_id=scene_id)
        result = await self.graph_session.run(
            """
                        MATCH (:OntologyInstance {instance_id: $instance_id})-[scene_rel]->(scene)-[contains_rel]->(milestone)
                        WHERE type(scene_rel) = 'HAS_SCENE'
                            AND 'Scene' IN labels(scene)
                            AND scene.id = $scene_id
                            AND type(contains_rel) = 'CONTAINS'
                            AND 'Milestone' IN labels(milestone)
                        OPTIONAL MATCH (milestone)-[derived_rel]->(derived)
                        WHERE type(derived_rel) = 'DERIVED_FROM' AND 'EntityInstance' IN labels(derived)
                        OPTIONAL MATCH (milestone)-[relates:RELATES_TO]->(related)
                        WHERE 'EntityInstance' IN labels(related)
                        OPTIONAL MATCH (milestone)-[followed_rel]->(followed)
                        WHERE type(followed_rel) = 'FOLLOWED_BY' AND 'Milestone' IN labels(followed)
                        OPTIONAL MATCH (milestone)-[preceded_rel]->(preceded)
                        WHERE type(preceded_rel) = 'PRECEDED_BY' AND 'Milestone' IN labels(preceded)
              WITH milestone,
                  head(collect(DISTINCT derived.entity_instance_id)) AS derived_from_entity_id,
                  collect(DISTINCT {entity_instance_id: related.entity_instance_id, label: relates.label}) AS relates,
                   head(collect(DISTINCT followed.id)) AS followed_by_milestone_id,
                   head(collect(DISTINCT preceded.id)) AS preceded_by_milestone_id
              RETURN milestone,
                    derived_from_entity_id,
                    relates,
                    followed_by_milestone_id,
                    preceded_by_milestone_id
            ORDER BY milestone.created_at ASC
            """,
            instance_id=instance_id,
            scene_id=scene_id,
        )
        rows = await result.data()
        milestones: list[MilestoneRead] = []
        for row in rows:
            node = row.get("milestone")
            if not node:
                continue
            relates = []
            for item in row.get("relates") or []:
                if not isinstance(item, dict):
                    continue
                entity_instance_id = _normalize_optional_str(item.get("entity_instance_id"))
                label = _normalize_optional_str(item.get("label"))
                if entity_instance_id and label:
                    relates.append(
                        {
                            "entity_instance_id": entity_instance_id,
                            "label": label,
                        }
                    )
            milestones.append(
                await self._milestone_node_to_read(
                    node=node,
                    scene_id=scene_id,
                    derived_from_entity_id=_normalize_optional_str(
                        row.get("derived_from_entity_id")
                    ),
                    relates_to=relates,
                    local_order={
                        "followed_by_milestone_id": row.get("followed_by_milestone_id"),
                        "preceded_by_milestone_id": row.get("preceded_by_milestone_id"),
                    },
                )
            )
        return milestones

    async def get_milestone(
        self, instance_id: str, scene_id: str, milestone_id: str
    ) -> MilestoneRead:
        milestones = await self.list_milestones(instance_id, scene_id)
        for milestone in milestones:
            if milestone.id == milestone_id:
                return milestone
        raise ValueError("Milestone not found")

    async def create_milestone(
        self, instance_id: str, scene_id: str, payload: MilestoneCreate
    ) -> MilestoneRead:
        scene = await self.get_scene(instance_id, scene_id)
        ontology_id = scene.ontology_id
        milestone_id = _normalize_optional_str(payload.id) or str(uuid4())
        existing_ids = await self._milestone_ids_for_scene(instance_id, scene_id)
        if milestone_id in existing_ids:
            raise ValueError(f"Milestone id '{milestone_id}' already exists")
        await self._validate_milestone_derived_from(
            instance_id=instance_id,
            entity_instance_id=payload.derived_from.entity_instance_id,
        )

        now_str = _format_dt(datetime.utcnow())
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (scene:Scene {id: $scene_id, instance_id: $instance_id})
                CREATE (scene)-[:CONTAINS]->(milestone:Milestone {
                    id: $milestone_id,
                    scene_id: $scene_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $name,
                    description: $description,
                    created_by_type: $created_by_type,
                    created_by_author: $created_by_author,
                    temporal_type: $temporal_type,
                    boundary_type: $boundary_type,
                    relates_to_json: $relates_to_json,
                    created_at: $created_at,
                    updated_at: $updated_at
                })
                """,
                scene_id=scene_id,
                instance_id=instance_id,
                milestone_id=milestone_id,
                ontology_id=ontology_id,
                name=payload.name,
                description=payload.description,
                created_by_type=payload.created_by_type,
                created_by_author=payload.created_by_author,
                temporal_type=payload.temporal_type,
                boundary_type=payload.boundary_type,
                relates_to_json=json.dumps(
                    [item.model_dump() for item in payload.relates_to],
                    ensure_ascii=False,
                ),
                created_at=now_str,
                updated_at=now_str,
            )
            await tx.run(
                """
                MATCH (milestone:Milestone {id: $milestone_id})
                MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                MERGE (milestone)-[:DERIVED_FROM]->(entity)
                """,
                milestone_id=milestone_id,
                entity_instance_id=payload.derived_from.entity_instance_id,
            )
            for relates in payload.relates_to:
                await tx.run(
                    """
                    MATCH (milestone:Milestone {id: $milestone_id})
                    MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                    MERGE (milestone)-[:RELATES_TO {label: $label}]->(entity)
                    """,
                    milestone_id=milestone_id,
                    entity_instance_id=relates.entity_instance_id,
                    label=relates.label,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        from app.tasks.neo4j_embedding import embed_ontology as embed_ontology_task

        embed_ontology_task.delay(ontology_id=ontology_id, author_type="agent", author_id="milestone-create")

        milestones = await self.list_milestones(instance_id, scene_id)
        begin_count = sum(1 for milestone in milestones if milestone.boundary_type == "begin")
        end_count = sum(1 for milestone in milestones if milestone.boundary_type == "end")
        if len(milestones) < 2 or begin_count != 1 or end_count != 1:
            await self.delete_milestone(instance_id, scene_id, milestone_id)
            raise ValueError(
                "Scene validity would be violated after milestone creation: need at least two milestones and exactly one begin/end boundary"
            )
        return await self.get_milestone(instance_id, scene_id, milestone_id)

    async def update_milestone(
        self,
        instance_id: str,
        scene_id: str,
        milestone_id: str,
        payload: MilestoneUpdate,
    ) -> MilestoneRead:
        current = await self.get_milestone(instance_id, scene_id, milestone_id)
        updates: dict[str, Any] = {}
        if payload.name is not None:
            updates["name"] = payload.name
        if payload.description is not None:
            updates["description"] = payload.description
        if payload.created_by_type is not None:
            updates["created_by_type"] = payload.created_by_type
        if payload.created_by_author is not None:
            updates["created_by_author"] = payload.created_by_author
        if payload.temporal_type is not None:
            updates["temporal_type"] = payload.temporal_type
        if payload.boundary_type is not None:
            updates["boundary_type"] = payload.boundary_type
        if payload.relates_to is not None:
            updates["relates_to_json"] = json.dumps(
                [item.model_dump() for item in payload.relates_to],
                ensure_ascii=False,
            )

        if payload.derived_from is not None:
            await self._validate_milestone_derived_from(
                instance_id=instance_id,
                entity_instance_id=payload.derived_from.entity_instance_id,
            )

        tx = await self.graph_session.begin_transaction()
        try:
            if updates:
                updates["updated_at"] = _format_dt(datetime.utcnow())
                set_parts = []
                params = {
                    "instance_id": instance_id,
                    "scene_id": scene_id,
                    "milestone_id": milestone_id,
                }
                for field, value in updates.items():
                    set_parts.append(f"milestone.{field} = ${field}")
                    params[field] = value
                await tx.run(
                    f"""
                                        MATCH (:OntologyInstance {{instance_id: $instance_id}})-[scene_rel]->(scene)-[contains_rel]->(milestone)
                                        WHERE type(scene_rel) = 'HAS_SCENE'
                                            AND 'Scene' IN labels(scene)
                                            AND scene.id = $scene_id
                                            AND type(contains_rel) = 'CONTAINS'
                                            AND 'Milestone' IN labels(milestone)
                                            AND milestone.id = $milestone_id
                    SET {', '.join(set_parts)}
                    """,
                    **params,
                )

            await tx.run(
                """
                MATCH (milestone:Milestone {id: $milestone_id})-[rel:FOLLOWED_BY|PRECEDED_BY|RELATES_TO|DERIVED_FROM]->()
                DELETE rel
                """,
                milestone_id=milestone_id,
            )

            local_order = payload.local_order or current.local_order
            if local_order.followed_by_milestone_id:
                await tx.run(
                    """
                    MATCH (source:Milestone {id: $milestone_id, scene_id: $scene_id})
                    MATCH (target:Milestone {id: $target_milestone_id, scene_id: $scene_id})
                    MERGE (source)-[:FOLLOWED_BY]->(target)
                    MERGE (target)-[:PRECEDED_BY]->(source)
                    """,
                    milestone_id=milestone_id,
                    scene_id=scene_id,
                    target_milestone_id=local_order.followed_by_milestone_id,
                )
            if local_order.preceded_by_milestone_id:
                await tx.run(
                    """
                    MATCH (source:Milestone {id: $milestone_id, scene_id: $scene_id})
                    MATCH (target:Milestone {id: $target_milestone_id, scene_id: $scene_id})
                    MERGE (source)-[:PRECEDED_BY]->(target)
                    MERGE (target)-[:FOLLOWED_BY]->(source)
                    """,
                    milestone_id=milestone_id,
                    scene_id=scene_id,
                    target_milestone_id=local_order.preceded_by_milestone_id,
                )

            derived_from_entity_id = (
                payload.derived_from.entity_instance_id
                if payload.derived_from is not None
                else current.derived_from.entity_instance_id
            )
            await tx.run(
                """
                MATCH (milestone:Milestone {id: $milestone_id})
                MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                MERGE (milestone)-[:DERIVED_FROM]->(entity)
                """,
                milestone_id=milestone_id,
                entity_instance_id=derived_from_entity_id,
            )

            relates_to = payload.relates_to if payload.relates_to is not None else current.relates_to
            for relates in relates_to:
                await tx.run(
                    """
                    MATCH (milestone:Milestone {id: $milestone_id})
                    MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                    MERGE (milestone)-[:RELATES_TO {label: $label}]->(entity)
                    """,
                    milestone_id=milestone_id,
                    entity_instance_id=relates.entity_instance_id,
                    label=relates.label,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        ontology_id = await self._get_instance_ontology_id(instance_id)
        from app.tasks.neo4j_embedding import embed_ontology as embed_ontology_task

        embed_ontology_task.delay(ontology_id=ontology_id, author_type="agent", author_id="milestone-update")

        milestones = await self.list_milestones(instance_id, scene_id)
        begin_count = sum(1 for milestone in milestones if milestone.boundary_type == "begin")
        end_count = sum(1 for milestone in milestones if milestone.boundary_type == "end")
        if len(milestones) < 2 or begin_count != 1 or end_count != 1:
            raise ValueError(
                "Scene validity violated by milestone update: scene must have at least two milestones with one begin and one end"
            )
        return await self.get_milestone(instance_id, scene_id, milestone_id)

    async def delete_milestone(
        self, instance_id: str, scene_id: str, milestone_id: str
    ) -> None:
        await self.get_milestone(instance_id, scene_id, milestone_id)
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                                MATCH (:OntologyInstance {instance_id: $instance_id})-[scene_rel]->(scene)-[contains_rel]->(milestone)
                                WHERE type(scene_rel) = 'HAS_SCENE'
                                    AND 'Scene' IN labels(scene)
                                    AND scene.id = $scene_id
                                    AND type(contains_rel) = 'CONTAINS'
                                    AND 'Milestone' IN labels(milestone)
                                    AND milestone.id = $milestone_id
                DETACH DELETE milestone
                """,
                instance_id=instance_id,
                scene_id=scene_id,
                milestone_id=milestone_id,
            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        ontology_id = await self._get_instance_ontology_id(instance_id)
        from app.tasks.neo4j_embedding import embed_ontology as embed_ontology_task

        embed_ontology_task.delay(ontology_id=ontology_id, author_type="agent", author_id="milestone-delete")

        milestones = await self.list_milestones(instance_id, scene_id)
        begin_count = sum(1 for milestone in milestones if milestone.boundary_type == "begin")
        end_count = sum(1 for milestone in milestones if milestone.boundary_type == "end")
        if len(milestones) < 2 or begin_count != 1 or end_count != 1:
            raise ValueError(
                "Cannot delete milestone because scene would become invalid (needs >=2 milestones with one begin and one end)"
            )
