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

from app.core.config_store import get_settings
from app.models.ontology import OntologyEntity
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.ontology_instance import (
    MilestoneCreate,
    MilestoneRead,
    MilestoneUpdate,
    OntologyEntityResolveItem,
    OntologyEntityResolveResponse,
    OntologyInstanceCreate,
    OntologyInstanceEntityCreate,
    OntologyInstanceRead,
    OntologyInstanceSceneCountItem,
    OntologyInstanceSceneCountsResponse,
    OntologyInstanceUpdate,
    OntologyInstanceSearchHit,
    OntologyInstanceSearchResponse,
    OntologyInstanceSummary,
    OntologyInstanceSummaryPage,
    SceneCreate,
    SceneRead,
    SceneUpdate,
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


def _enqueue_embed_reconciliation(
    *,
    ontology_id: int,
    instance_id: str | None,
    node_ids: list[str],
    author_id: str,
) -> None:
    from app.tasks.neo4j_embedding import (
        embed_reconciliation as embed_reconciliation_task,
    )

    settings = get_settings()
    embed_reconciliation_task.apply_async(
        kwargs={
            "ontology_id": ontology_id,
            "instance_id": instance_id,
            "node_ids": node_ids,
            "author_type": "agent",
            "author_id": author_id,
        },
        expires=max(60, int(settings.celery_expires_reconciliation_seconds)),
    )


def _enqueue_link_instance(instance_id: str) -> None:
    from app.tasks.ontology_links import link_instance as link_instance_task

    settings = get_settings()
    link_instance_task.apply_async(
        args=[instance_id],
        kwargs={"author_type": "agent", "author_id": "system"},
        expires=max(60, int(settings.celery_expires_reconciliation_seconds)),
    )


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
    seen: set[str] = set()
    if not values:
        return normalized
    for value in values:
        cleaned = _normalize_optional_str(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
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
        self, payload: OntologyInstanceCreate, *, trigger_background_jobs: bool = True
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
            if trigger_background_jobs:
                from app.tasks.neo4j_embedding import embed_nodes as embed_nodes_task

                _enqueue_link_instance(instance.instance_id)
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

    async def resolve_entities(
        self,
        *,
        ontology_id: int,
        entity_instance_ids: list[str],
    ) -> OntologyEntityResolveResponse:
        requested_ids = _normalize_id_list(entity_instance_ids)
        if not requested_ids:
            raise ValueError("entity_instance_ids cannot be empty")
        if len(requested_ids) > 200:
            raise ValueError("entity_instance_ids cannot contain more than 200 ids")

        result = await self.graph_session.run(
            """
            UNWIND $entity_ids AS entity_id
            OPTIONAL MATCH (entity:EntityInstance {entity_instance_id: entity_id})
            WHERE entity IS NOT NULL
              AND toInteger(entity.ontology_id) = toInteger($ontology_id)
            OPTIONAL MATCH (inst:OntologyInstance {instance_id: entity.instance_id})
            RETURN entity.entity_instance_id AS entity_instance_id,
                   entity.instance_id AS instance_id,
                   toInteger(entity.ontology_id) AS ontology_id,
                   toInteger(entity.entity_definition_id) AS entity_definition_id,
                   entity.alias AS entity_alias,
                   inst.name AS instance_name
            """,
            entity_ids=requested_ids,
            ontology_id=ontology_id,
        )
        rows = await result.data()
        resolved_by_id: dict[str, OntologyEntityResolveItem] = {}
        for row in rows:
            entity_id = _normalize_optional_str(row.get("entity_instance_id"))
            if not entity_id or entity_id in resolved_by_id:
                continue
            resolved_by_id[entity_id] = OntologyEntityResolveItem(
                entity_instance_id=entity_id,
                instance_id=str(row.get("instance_id") or ""),
                ontology_id=int(row.get("ontology_id") or ontology_id),
                entity_definition_id=int(row.get("entity_definition_id") or 0),
                entity_alias=_normalize_optional_str(row.get("entity_alias")),
                instance_name=_normalize_optional_str(row.get("instance_name")),
            )

        ordered_results: list[OntologyEntityResolveItem] = []
        missing_ids: list[str] = []
        for entity_id in requested_ids:
            item = resolved_by_id.get(entity_id)
            if item is None:
                missing_ids.append(entity_id)
                continue
            ordered_results.append(item)

        return OntologyEntityResolveResponse(
            results=ordered_results,
            missing_entity_instance_ids=missing_ids,
        )

    async def count_scenes_by_instances(
        self,
        *,
        instance_ids: list[str],
    ) -> OntologyInstanceSceneCountsResponse:
        requested_ids = _normalize_id_list(instance_ids)
        if not requested_ids:
            raise ValueError("instance_ids cannot be empty")
        if len(requested_ids) > 200:
            raise ValueError("instance_ids cannot contain more than 200 ids")

        result = await self.graph_session.run(
            """
            UNWIND $instance_ids AS instance_id
            OPTIONAL MATCH (:OntologyInstance {instance_id: instance_id})-[rel]->(scene)
            WHERE type(rel) = 'HAS_SCENE' AND 'Scene' IN labels(scene)
            RETURN instance_id AS instance_id, count(DISTINCT scene) AS scene_count
            """,
            instance_ids=requested_ids,
        )
        rows = await result.data()
        counts_by_instance: dict[str, int] = {}
        for row in rows:
            instance_id = _normalize_optional_str(str(row.get("instance_id") or ""))
            if not instance_id:
                continue
            counts_by_instance[instance_id] = int(row.get("scene_count") or 0)

        ordered_results = [
            OntologyInstanceSceneCountItem(
                instance_id=instance_id,
                scene_count=counts_by_instance.get(instance_id, 0),
            )
            for instance_id in requested_ids
        ]
        return OntologyInstanceSceneCountsResponse(results=ordered_results)

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

        # Enrich entities with CharacterAgent info
        agent_result = await self.graph_session.run(
            """
            MATCH (agent:CharacterAgent)-[:EMBODIES]->(entity:EntityInstance)
            WHERE entity.instance_id = $instance_id
            RETURN entity.entity_instance_id AS entity_id,
                   agent.id AS agent_id,
                   agent.name AS agent_name
            """,
            instance_id=instance_id,
        )
        agent_rows = await agent_result.data()
        agent_map: dict[str, dict[str, Any]] = {}
        for row in agent_rows:
            eid = row["entity_id"]
            agent_map[eid] = {
                "has_agent": True,
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
            }
        for entity_data in entities_map.values():
            eid = entity_data["entity_instance_id"]
            info = agent_map.get(eid)
            if info:
                entity_data["has_agent"] = True
                entity_data["agent_id"] = info["agent_id"]
                entity_data["agent_name"] = info["agent_name"]
            else:
                entity_data["has_agent"] = False
                entity_data["agent_id"] = None
                entity_data["agent_name"] = None

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
                    "has_agent": entity_data.get("has_agent", False),
                    "agent_id": entity_data.get("agent_id"),
                    "agent_name": entity_data.get("agent_name"),
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

            await self._delete_entity_relationships(tx, entity_ids=entity_ids)
            await self._remove_cross_instance_links(
                tx, instance_ids=list(instance_list)
            )

            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_ENTITY]->(e:EntityInstance)-[:HAS_SEMANTIC_DOCUMENT]->(chunk:SemanticDocument)
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
                    MATCH (e:EntityInstance)-[chunk_rel]->(chunk)
                    WHERE e.entity_instance_id IN $entity_ids
                      AND type(chunk_rel) = 'HAS_SEMANTIC_DOCUMENT'
                      AND 'SemanticDocument' IN labels(chunk)
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
                await tx.run(
                    """
                    MATCH (e:EntityInstance)-[chunk_rel]->(chunk)
                    WHERE e.entity_instance_id IN $entity_ids
                      AND type(chunk_rel) = 'HAS_SEMANTIC_DOCUMENT'
                      AND 'SemanticDocument' IN labels(chunk)
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
                OPTIONAL MATCH (i)-[event_rel]->(event)
                WHERE type(event_rel) = 'HAS_EVENT'
                  AND 'Event' IN labels(event)
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
        }

    async def clear_timeline_events_and_orphans(
        self,
        *,
        ontology_id: int,
    ) -> dict[str, Any]:
        ontology = await self.repository.get(ontology_id)
        if ontology is None:
            raise ValueError("Ontology not found")

        tx = await self.graph_session.begin_transaction()
        try:
            # Response keys remain additive-compatible; removed v1 stores are no
            # longer inspected or maintained.
            legacy_event_count = 0
            legacy_chunk_count = 0

            milestones_count_result = await tx.run(
                """
                                MATCH (i:OntologyInstance)-[scene_rel]->(scene)-[contains_rel]->(milestone)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                                    AND type(scene_rel) = 'HAS_SCENE'
                                    AND 'Scene' IN labels(scene)
                                    AND type(contains_rel) = 'CONTAINS'
                                    AND 'Milestone' IN labels(milestone)
                RETURN count(DISTINCT milestone) AS milestones_count
                """,
                ontology_id=ontology_id,
            )
            milestones_count_row = await milestones_count_result.single()
            milestones_count = int(
                milestones_count_row.get("milestones_count") if milestones_count_row else 0
            )

            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_SCENE]->(scene:Scene)
                      <-[:PROJECTS_ON]-(perspective:ScenePerspective)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                OPTIONAL MATCH (perspective)-[:EVOKES|FORMS_BELIEF|HAS_IMPACT]->(child)
                DETACH DELETE child
                """,
                ontology_id=ontology_id,
            )
            await tx.run(
                """
                MATCH (i:OntologyInstance)-[:HAS_SCENE]->(scene:Scene)
                      <-[:PROJECTS_ON]-(perspective:ScenePerspective)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                DETACH DELETE perspective
                """,
                ontology_id=ontology_id,
            )

            await tx.run(
                """
                                MATCH (i:OntologyInstance)-[scene_rel]->(scene)-[contains_rel]->(milestone)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                                    AND type(scene_rel) = 'HAS_SCENE'
                                    AND 'Scene' IN labels(scene)
                                    AND type(contains_rel) = 'CONTAINS'
                                    AND 'Milestone' IN labels(milestone)
                DETACH DELETE milestone
                """,
                ontology_id=ontology_id,
            )

            scenes_count_result = await tx.run(
                """
                                MATCH (i:OntologyInstance)-[scene_rel]->(scene)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                                    AND type(scene_rel) = 'HAS_SCENE'
                                    AND 'Scene' IN labels(scene)
                RETURN count(DISTINCT scene) AS scenes_count
                """,
                ontology_id=ontology_id,
            )
            scenes_count_row = await scenes_count_result.single()
            scenes_count = int(
                scenes_count_row.get("scenes_count") if scenes_count_row else 0
            )

            await tx.run(
                """
                                MATCH (i:OntologyInstance)-[scene_rel]->(scene)
                WHERE toInteger(i.ontology_id) = toInteger($ontology_id)
                                    AND type(scene_rel) = 'HAS_SCENE'
                                    AND 'Scene' IN labels(scene)
                DETACH DELETE scene
                """,
                ontology_id=ontology_id,
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
            "legacy_events_deleted": legacy_event_count,
            "legacy_event_chunks_deleted": legacy_chunk_count,
            "milestones_deleted": milestones_count,
            "scenes_deleted": scenes_count,
        }

    async def update_instance(
        self, instance_id: str, payload: OntologyInstanceUpdate
    ) -> OntologyInstanceRead:
        if payload.scenes is not None:
            raise ValueError(
                "Scenes and milestones must be updated via scene-specific endpoints"
            )

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
            instance = await self.get_instance(instance_id)

            # Notifications are owned by ShreckRPG; Shrecknet keeps core update only.
            _enqueue_link_instance(instance.instance_id)
            return instance

        definitions = await self._load_entity_definitions(current.ontology_id)
        entities_payload = self._sanitize_entities_payload_for_update(
            payload.entities, definitions
        )
        current_entity_ids = [
            entity.entity_instance_id
            for entity in current.entities
            if _normalize_optional_str(entity.entity_instance_id)
        ]
        if len(current_entity_ids) != 1:
            raise ValueError(
                "Instance update requires exactly one existing entity in single-entity mode"
            )
        if len(entities_payload) != 1:
            raise ValueError(
                "Instance update requires exactly one entity payload in single-entity mode"
            )
        entities_payload = [
            entities_payload[0].model_copy(
                update={"entity_instance_id": current_entity_ids[0]}
            )
        ]

        tx = await self.graph_session.begin_transaction()
        impacted_entity_ids: set[str] = set()
        try:
            existing_entities_result = await tx.run(
                """
                MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance)
                RETURN e.entity_instance_id AS entity_instance_id,
                       e.alias AS alias,
                       e.entity_definition_id AS definition_id
                """,
                instance_id=instance_id,
            )
            existing_entities = await existing_entities_result.data()
            alias_to_ids: dict[str, str] = {}
            alias_to_definition: dict[str, int] = {}
            existing_definition_by_id: dict[str, int] = {}
            existing_alias_by_id: dict[str, str] = {}
            for row in existing_entities:
                entity_id = row.get("entity_instance_id")
                alias_raw = row.get("alias")
                definition_id = row.get("definition_id")
                if not entity_id or not alias_raw or definition_id is None:
                    continue
                alias = str(alias_raw).strip()
                if not alias:
                    continue
                normalized_alias = re.sub(r"[^a-z0-9_]+", "_", alias.lower())
                entity_id_str = str(entity_id)
                definition_id_int = int(definition_id)
                alias_to_ids[alias] = entity_id_str
                alias_to_ids[normalized_alias] = entity_id_str
                alias_to_definition[alias] = definition_id_int
                alias_to_definition[normalized_alias] = definition_id_int
                existing_definition_by_id[entity_id_str] = definition_id_int
                existing_alias_by_id[entity_id_str] = alias

            seen_payload_aliases: set[str] = set()
            updates_payload: list[
                tuple[str, OntologyInstanceEntityCreate, dict[str, Any], str, str]
            ] = []
            planned_alias_by_id: dict[str, str] = dict(existing_alias_by_id)
            for entity_payload in entities_payload:
                canonical_alias = entity_payload.alias.strip()
                if canonical_alias in seen_payload_aliases:
                    raise ValueError(
                        f"Duplicate entity alias '{entity_payload.alias}' in payload"
                    )
                seen_payload_aliases.add(canonical_alias)

                if entity_payload.definition_id not in definitions:
                    raise ValueError(
                        f"Entity definition {entity_payload.definition_id} does not belong to ontology"
                    )

                normalized_alias = re.sub(
                    r"[^a-z0-9_]+", "_", entity_payload.alias.strip().lower()
                )
                provided_entity_id = _normalize_optional_str(
                    entity_payload.entity_instance_id
                )
                entity_id: str | None = None
                if provided_entity_id is not None:
                    if provided_entity_id not in existing_definition_by_id:
                        raise ValueError(
                            f"Entity id '{provided_entity_id}' does not exist in this ontology instance"
                        )
                    entity_id = provided_entity_id
                else:
                    entity_id = alias_to_ids.get(canonical_alias) or alias_to_ids.get(
                        normalized_alias
                    )
                if entity_id is None:
                    raise ValueError(
                        f"Entity alias '{entity_payload.alias}' does not exist in this ontology instance; provide entity_instance_id to rename aliases"
                    )
                existing_definition_id = existing_definition_by_id.get(entity_id)
                if (
                    existing_definition_id is not None
                    and existing_definition_id != entity_payload.definition_id
                ):
                    raise ValueError(
                        f"Entity alias '{entity_payload.alias}' cannot change definition_id"
                    )

                definition = definitions[entity_payload.definition_id]
                valid_property_ids = set(definition["properties"].keys())
                valid_relationship_ids = set(definition["relationships"].keys())
                for prop in entity_payload.properties:
                    if prop.definition_id not in valid_property_ids:
                        raise ValueError(
                            f"Property definition {prop.definition_id} does not belong to entity"
                        )
                for rel in entity_payload.relationships:
                    if rel.definition_id not in valid_relationship_ids:
                        raise ValueError(
                            f"Relationship definition {rel.definition_id} does not belong to entity"
                        )

                prop_map = {
                    str(prop.definition_id): prop.value
                    for prop in entity_payload.properties
                }
                created_dt = _ensure_datetime(entity_payload.created_date)
                updated_dt = _ensure_datetime(entity_payload.last_updated_date)
                updates_payload.append(
                    (
                        entity_id,
                        entity_payload,
                        prop_map,
                        _format_dt(created_dt),
                        _format_dt(updated_dt),
                    )
                )
                impacted_entity_ids.add(entity_id)
                planned_alias_by_id[entity_id] = canonical_alias
                alias_to_ids[canonical_alias] = entity_id
                alias_to_ids[normalized_alias] = entity_id
                alias_to_definition[canonical_alias] = entity_payload.definition_id
                alias_to_definition[normalized_alias] = entity_payload.definition_id

            seen_aliases: set[str] = set()
            seen_normalized_aliases: set[str] = set()
            for alias in planned_alias_by_id.values():
                normalized = re.sub(r"[^a-z0-9_]+", "_", alias.strip().lower())
                if alias in seen_aliases or normalized in seen_normalized_aliases:
                    raise ValueError(
                        f"Duplicate entity alias '{alias}' in resulting ontology instance"
                    )
                seen_aliases.add(alias)
                seen_normalized_aliases.add(normalized)

            for (
                entity_node_id,
                entity_payload,
                prop_map,
                created_iso,
                updated_iso,
            ) in updates_payload:
                await tx.run(
                    """
                    MATCH (i:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance {entity_instance_id: $entity_instance_id})
                    SET e.entity_definition_id = $entity_definition_id,
                        e.properties = $properties,
                        e.text = $text,
                        e.node_avatar_url = $node_avatar_url,
                        e.autogenerated_text = $autogenerated_text,
                        e.text_linked = $text_linked,
                        e.autogenerated_text_linked = $autogenerated_text_linked,
                        e.created_date = $created_date,
                        e.last_updated_date = $last_updated_date,
                        e.author_type = $author_type,
                        e.author_id = $author_id,
                        e.updated_at = $updated_at,
                        e.alias = $alias
                    """,
                    instance_id=instance_id,
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
                    updated_at=timestamp,
                    alias=entity_payload.alias,
                )

            for entity_node_id, entity_payload, _, _, _ in updates_payload:
                relationship_definitions = definitions[entity_payload.definition_id][
                    "relationships"
                ]
                existing_rels_result = await tx.run(
                    """
                    MATCH (source:EntityInstance {entity_instance_id: $source_id})-[r:RELATES_TO]->(target:EntityInstance)
                    RETURN target.entity_instance_id AS target_id,
                           properties(r)['relationship_definition_id'] AS relationship_definition_id,
                           properties(r)['relationship_instance_id'] AS relationship_instance_id,
                           properties(r)['data'] AS rel_data
                    """,
                    source_id=entity_node_id,
                )
                existing_rels = await existing_rels_result.data()
                desired_relationships: list[tuple[str, int, str]] = []
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
                        match_definition = alias_to_definition.get(target_alias)
                        if match_definition is None:
                            normalized_target = re.sub(
                                r"[^a-z0-9_]+", "_", target_alias.strip().lower()
                            )
                            match_definition = alias_to_definition.get(normalized_target)
                        if match_definition is None:
                            raise ValueError(
                                f"Relationship refers to unknown target alias '{target_alias}'"
                            )
                        destiny_entity_id = relationship_definitions[
                            relationship_payload.definition_id
                        ].destiny_entity_id
                        if (
                            destiny_entity_id is not None
                            and match_definition != destiny_entity_id
                        ):
                            raise ValueError(
                                "Relationship target alias does not match destiny entity definition"
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
                    relationship_data_obj = relationship_payload.data or {}
                    relationship_data = json.dumps(
                        relationship_data_obj, sort_keys=True, separators=(",", ":")
                    )
                    desired_relationships.append(
                        (
                            str(target_id),
                            int(relationship_payload.definition_id),
                            relationship_data,
                        )
                    )

                def _canonical_data(raw_data: Any) -> tuple[str, Any]:
                    data_obj: Any
                    if isinstance(raw_data, str):
                        try:
                            parsed = json.loads(raw_data)
                            data_obj = parsed if isinstance(parsed, dict) else {}
                        except (TypeError, ValueError):
                            data_obj = {}
                    elif isinstance(raw_data, dict):
                        data_obj = raw_data
                    else:
                        data_obj = {}
                    return (
                        json.dumps(data_obj, sort_keys=True, separators=(",", ":")),
                        data_obj,
                    )

                existing_by_key: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
                for existing_rel in existing_rels:
                    definition_id_raw = existing_rel.get("relationship_definition_id")
                    target_id = existing_rel.get("target_id")
                    if definition_id_raw is None or not target_id:
                        continue
                    canonical_data, data_obj = _canonical_data(existing_rel.get("rel_data"))
                    key = (int(definition_id_raw), str(target_id), canonical_data)
                    existing_by_key.setdefault(key, []).append(
                        {
                            "relationship_instance_id": existing_rel.get(
                                "relationship_instance_id"
                            ),
                            "data_obj": data_obj,
                        }
                    )

                desired_counts: dict[tuple[int, str, str], int] = {}
                for target_id, definition_id, relationship_data in desired_relationships:
                    key = (definition_id, target_id, relationship_data)
                    desired_counts[key] = desired_counts.get(key, 0) + 1

                for key, existing_items in existing_by_key.items():
                    desired_count = desired_counts.get(key, 0)
                    if len(existing_items) <= desired_count:
                        continue
                    definition_id, target_id, relationship_data = key
                    rel_definition = relationship_definitions.get(definition_id)
                    remove_count = len(existing_items) - desired_count
                    for _ in range(remove_count):
                        rel_item = existing_items.pop()
                        rel_instance_id = _normalize_optional_str(
                            rel_item.get("relationship_instance_id")
                        )
                        if rel_instance_id:
                            await tx.run(
                                """
                                MATCH (:EntityInstance {entity_instance_id: $source_id})-[r:RELATES_TO]->(:EntityInstance {entity_instance_id: $target_id})
                                WHERE properties(r)['relationship_instance_id'] = $relationship_instance_id
                                DELETE r
                                """,
                                source_id=entity_node_id,
                                target_id=target_id,
                                relationship_instance_id=rel_instance_id,
                            )
                        else:
                            await tx.run(
                                """
                                MATCH (:EntityInstance {entity_instance_id: $source_id})-[r:RELATES_TO]->(:EntityInstance {entity_instance_id: $target_id})
                                WHERE properties(r)['relationship_definition_id'] = $relationship_definition_id
                                  AND coalesce(properties(r)['data'], '{}') = $data
                                WITH r LIMIT 1
                                DELETE r
                                """,
                                source_id=entity_node_id,
                                target_id=target_id,
                                relationship_definition_id=definition_id,
                                data=relationship_data,
                            )
                        impacted_entity_ids.add(entity_node_id)
                        impacted_entity_ids.add(target_id)
                        if rel_definition and rel_definition.bi_directional:
                            await tx.run(
                                """
                                MATCH (:EntityInstance {entity_instance_id: $target_id})-[r:RELATES_TO]->(:EntityInstance {entity_instance_id: $source_id})
                                WHERE properties(r)['relationship_definition_id'] = $relationship_definition_id
                                  AND coalesce(properties(r)['data'], '{}') = $data
                                WITH r LIMIT 1
                                DELETE r
                                """,
                                target_id=target_id,
                                source_id=entity_node_id,
                                relationship_definition_id=definition_id,
                                data=relationship_data,
                            )

                for key, desired_count in desired_counts.items():
                    existing_count = len(existing_by_key.get(key, []))
                    if existing_count >= desired_count:
                        continue
                    definition_id, target_id, relationship_data = key
                    rel_definition = relationship_definitions[definition_id]
                    add_count = desired_count - existing_count
                    for _ in range(add_count):
                        relationship_id = str(uuid4())
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
                            relationship_definition_id=definition_id,
                            destiny_definition_id=rel_definition.destiny_entity_id,
                            data=relationship_data,
                            created_at=timestamp,
                            updated_at=timestamp,
                        )
                        impacted_entity_ids.add(entity_node_id)
                        impacted_entity_ids.add(target_id)
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
                                relationship_definition_id=definition_id,
                                destiny_definition_id=entity_payload.definition_id,
                                data=relationship_data,
                                created_at=timestamp,
                                updated_at=timestamp,
                            )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

            instance = await self.get_instance(instance_id)

            # Notifications are owned by ShreckRPG; Shrecknet keeps core update only.

            from app.tasks.neo4j_embedding import embed_nodes as embed_nodes_task

            _enqueue_link_instance(instance.instance_id)
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
            RETURN scene['id'] AS scene_id
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

    async def _validate_scene_relates_to_entities(
        self, *, instance_id: str, entity_instance_ids: list[str]
    ) -> None:
        if not entity_instance_ids:
            return
        result = await self.graph_session.run(
            """
            UNWIND $entity_ids AS entity_id
            OPTIONAL MATCH (entity:EntityInstance {entity_instance_id: entity_id})
            RETURN entity_id, entity IS NOT NULL AS exists
            """,
            entity_ids=entity_instance_ids,
        )
        rows = await result.data()
        invalid = [
            str(row.get("entity_id") or "")
            for row in rows
            if not bool(row.get("exists"))
        ]
        if invalid:
            raise ValueError(
                "Scene relates_to.entity_instance_id must reference existing entities"
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
    ) -> list[MilestoneCreate]:
        milestone_ids: set[str] = set()
        normalized_milestones: list[MilestoneCreate] = []
        for milestone in milestones:
            milestone_id = _normalize_optional_str(milestone.id)
            if not milestone_id:
                if (
                    milestone.local_order.followed_by_milestone_id
                    or milestone.local_order.preceded_by_milestone_id
                ):
                    raise ValueError(
                        "Milestone local_order references require explicit milestone ids"
                    )
                milestone_id = str(uuid4())
            if milestone_id in milestone_ids:
                raise ValueError(f"Duplicate milestone id '{milestone_id}' in scene")
            milestone_ids.add(milestone_id)
            normalized_milestones.append(
                milestone.model_copy(update={"id": milestone_id})
            )

            await self._validate_milestone_derived_from(
                instance_id=instance_id,
                entity_instance_id=milestone.derived_from.entity_instance_id,
            )

        self._build_local_order_pairs(milestone_ids, normalized_milestones)
        return normalized_milestones

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

        ontology_id_raw = props.get("ontology_id")
        if ontology_id_raw is None:
            raise ValueError(f"Scene '{scene_id}' is missing ontology_id")
        try:
            ontology_id = int(ontology_id_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Scene '{scene_id}' has invalid ontology_id") from exc
        if ontology_id <= 0:
            raise ValueError(f"Scene '{scene_id}' has invalid ontology_id")

        derived_result = await self.graph_session.run(
            """
            MATCH (:Scene {id: $scene_id})-[:DERIVED_FROM]->(entity:EntityInstance)
            RETURN entity.entity_instance_id AS entity_instance_id
            LIMIT 1
            """,
            scene_id=scene_id,
        )
        derived_row = await derived_result.single()
        derived_from_entity_id = None
        if derived_row:
            derived_from_entity_id = _normalize_optional_str(
                str(derived_row.get("entity_instance_id") or "")
            )
        if not derived_from_entity_id:
            raise ValueError(
                f"Scene '{scene_id}' must have a DERIVED_FROM entity_instance_id"
            )

        local_order_result = await self.graph_session.run(
            """
            MATCH (scene)
            WHERE 'Scene' IN labels(scene)
              AND scene['id'] = $scene_id
            OPTIONAL MATCH (scene)-[followed_rel]->(followed)
            WHERE type(followed_rel) = 'FOLLOWED_BY'
              AND 'Scene' IN labels(followed)
            OPTIONAL MATCH (scene)-[preceded_rel]->(preceded)
            WHERE type(preceded_rel) = 'PRECEDED_BY'
              AND 'Scene' IN labels(preceded)
            RETURN followed['id'] AS followed_by_scene_id,
                   preceded['id'] AS preceded_by_scene_id
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

        scene_relates_result = await self.graph_session.run(
            """
            MATCH (scene:Scene {id: $scene_id})-[rel:RELATES_TO]->(entity:EntityInstance)
            RETURN collect(DISTINCT {
                entity_instance_id: entity.entity_instance_id,
                label: rel.label
            }) AS relates
            """,
            scene_id=scene_id,
        )
        scene_relates_row = await scene_relates_result.single()
        scene_relates = [
            item
            for item in (scene_relates_row or {}).get("relates") or []
            if item and item.get("entity_instance_id")
        ]

        milestones = await self.list_milestones(props.get("instance_id") or "", scene_id)

        return SceneRead(
            id=scene_id,
            instance_id=props.get("instance_id") or "",
            ontology_id=ontology_id,
            name=props.get("name") or "",
            description=props.get("description") or "",
            created_by_type=props.get("created_by_type") or "human",
            created_by_author=props.get("created_by_author") or "",
            local_order=local_order,
            derived_from={"entity_instance_id": derived_from_entity_id},
            relates_to=scene_relates,
            created_at=_parse_dt(props.get("created_at")),
            updated_at=_parse_dt(props.get("updated_at")),
            milestones=milestones,
        )

    async def list_scenes_by_derived_from(
        self, instance_id: str, entity_instance_id: str
    ) -> list[SceneRead]:
        await self._get_instance_ontology_id(instance_id)
        entity_id = _normalize_optional_str(entity_instance_id)
        if not entity_id:
            raise ValueError("derived_from entity_instance_id cannot be empty")

        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene:Scene)
            WHERE type(rel) = 'HAS_SCENE'
              AND (
                EXISTS {
                    MATCH (scene)-[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: $entity_instance_id})
                }
                OR EXISTS {
                    MATCH (scene)-[:CONTAINS]->(:Milestone)-[:DERIVED_FROM]->(:EntityInstance {entity_instance_id: $entity_instance_id})
                }
              )
            RETURN DISTINCT scene
            ORDER BY scene.created_at ASC
            """,
            instance_id=instance_id,
            entity_instance_id=entity_id,
        )
        rows = await result.data()
        scenes: list[SceneRead] = []
        for row in rows:
            scene_node = row.get("scene")
            if scene_node is None:
                continue
            scenes.append(await self._scene_node_to_read(node=scene_node))
        return scenes

    async def list_scenes_by_related_to(
        self, instance_id: str, entity_instance_id: str
    ) -> list[SceneRead]:
        await self._get_instance_ontology_id(instance_id)
        entity_id = _normalize_optional_str(entity_instance_id)
        if not entity_id:
            raise ValueError("related_to entity_instance_id cannot be empty")

        result = await self.graph_session.run(
            """
            MATCH (:OntologyInstance {instance_id: $instance_id})-[rel]->(scene:Scene)
            WHERE type(rel) = 'HAS_SCENE'
              AND (
                EXISTS {
                    MATCH (scene)-[:RELATES_TO]->(:EntityInstance {entity_instance_id: $entity_instance_id})
                }
                OR EXISTS {
                    MATCH (scene)-[:CONTAINS]->(:Milestone)-[:RELATES_TO]->(:EntityInstance {entity_instance_id: $entity_instance_id})
                }
              )
            RETURN DISTINCT scene
            ORDER BY scene.created_at ASC
            """,
            instance_id=instance_id,
            entity_instance_id=entity_id,
        )
        rows = await result.data()
        scenes: list[SceneRead] = []
        for row in rows:
            scene_node = row.get("scene")
            if scene_node is None:
                continue
            scenes.append(await self._scene_node_to_read(node=scene_node))
        return scenes

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

    async def create_scene(
        self, instance_id: str, payload: SceneCreate, *, trigger_background_jobs: bool = True
    ) -> SceneRead:
        ontology_id = await self._get_instance_ontology_id(instance_id)
        scene_id = _normalize_optional_str(payload.id) or str(uuid4())
        if scene_id in await self._scene_ids_for_instance(instance_id):
            raise ValueError(f"Scene id '{scene_id}' already exists")

        await self._validate_scene_derived_from(
            instance_id=instance_id,
            entity_instance_id=payload.derived_from.entity_instance_id,
        )
        await self._validate_scene_relates_to_entities(
            instance_id=instance_id,
            entity_instance_ids=[item.entity_instance_id for item in payload.relates_to],
        )
        normalized_milestones = await self._validate_scene_milestones_payload(
            instance_id=instance_id,
            milestones=payload.milestones,
        )

        now_str = _format_dt(datetime.utcnow())
        tx = await self.graph_session.begin_transaction()
        try:
            await tx.run(
                """
                MATCH (:OntologyInstance {instance_id:$instance_id})-[:HAS_SCENE]->
                      (scene:Scene {id:$scene_id})<-[:PROJECTS_ON]-
                      (perspective:ScenePerspective)
                OPTIONAL MATCH (perspective)-[:EVOKES|FORMS_BELIEF|HAS_IMPACT]->(child)
                DETACH DELETE child
                """,
                instance_id=instance_id,
                scene_id=scene_id,
            )
            await tx.run(
                """
                MATCH (:OntologyInstance {instance_id:$instance_id})-[:HAS_SCENE]->
                      (scene:Scene {id:$scene_id})<-[:PROJECTS_ON]-
                      (perspective:ScenePerspective)
                DETACH DELETE perspective
                """,
                instance_id=instance_id,
                scene_id=scene_id,
            )
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

            for relates in payload.relates_to:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})
                    MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                    MERGE (scene)-[:RELATES_TO {label: $label}]->(entity)
                    """,
                    scene_id=scene_id,
                    entity_instance_id=relates.entity_instance_id,
                    label=relates.label,
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
            for milestone in normalized_milestones:
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
            for milestone in normalized_milestones:
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
        if trigger_background_jobs:
            _enqueue_embed_reconciliation(
                ontology_id=ontology_id,
                instance_id=None,
                node_ids=[scene_id, *sorted(milestone_ids)],
                author_id="scene-create",
            )
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
        if payload.relates_to is not None:
            await self._validate_scene_relates_to_entities(
                instance_id=instance_id,
                entity_instance_ids=[item.entity_instance_id for item in payload.relates_to],
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

            if payload.relates_to is not None:
                await tx.run(
                    """
                    MATCH (scene:Scene {id: $scene_id})-[rel:RELATES_TO]->()
                    DELETE rel
                    """,
                    scene_id=scene_id,
                )
                for relates in payload.relates_to:
                    await tx.run(
                        """
                        MATCH (scene:Scene {id: $scene_id})
                        MATCH (entity:EntityInstance {entity_instance_id: $entity_instance_id})
                        MERGE (scene)-[:RELATES_TO {label: $label}]->(entity)
                        """,
                        scene_id=scene_id,
                        entity_instance_id=relates.entity_instance_id,
                        label=relates.label,
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
        _enqueue_embed_reconciliation(
            ontology_id=ontology_id,
            instance_id=None,
            node_ids=[scene_id],
            author_id="scene-update",
        )

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
        _enqueue_embed_reconciliation(
            ontology_id=ontology_id,
            instance_id=None,
            node_ids=[scene_id],
            author_id="scene-delete",
        )

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
            milestone_id = _normalize_optional_str((dict(node)).get("id")) or ""
            derived_from_entity_id = _normalize_optional_str(
                row.get("derived_from_entity_id")
            )
            if not derived_from_entity_id:
                raise ValueError(
                    f"Milestone '{milestone_id}' must have a DERIVED_FROM entity_instance_id"
                )
            relates = []
            for item in row.get("relates") or []:
                if not isinstance(item, dict):
                    continue
                entity_instance_id = _normalize_optional_str(item.get("entity_instance_id"))
                label = _normalize_optional_str(item.get("label"))
                if not entity_instance_id and not label:
                    continue
                if not entity_instance_id or not label:
                    raise ValueError(
                        f"Milestone '{milestone_id}' has malformed RELATES_TO relationship"
                    )
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
                    derived_from_entity_id=derived_from_entity_id,
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
        self,
        instance_id: str,
        scene_id: str,
        payload: MilestoneCreate,
        *,
        trigger_background_jobs: bool = True,
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
            if payload.local_order.followed_by_milestone_id:
                await tx.run(
                    """
                    MATCH (source:Milestone {id: $milestone_id, scene_id: $scene_id})
                    MATCH (target:Milestone {id: $target_milestone_id, scene_id: $scene_id})
                    MERGE (source)-[:FOLLOWED_BY]->(target)
                    MERGE (target)-[:PRECEDED_BY]->(source)
                    """,
                    milestone_id=milestone_id,
                    scene_id=scene_id,
                    target_milestone_id=payload.local_order.followed_by_milestone_id,
                )
            if payload.local_order.preceded_by_milestone_id:
                await tx.run(
                    """
                    MATCH (source:Milestone {id: $milestone_id, scene_id: $scene_id})
                    MATCH (target:Milestone {id: $target_milestone_id, scene_id: $scene_id})
                    MERGE (source)-[:PRECEDED_BY]->(target)
                    MERGE (target)-[:FOLLOWED_BY]->(source)
                    """,
                    milestone_id=milestone_id,
                    scene_id=scene_id,
                    target_milestone_id=payload.local_order.preceded_by_milestone_id,
                )
        except Exception:
            await tx.rollback()
            await tx.close()
            raise
        else:
            await tx.commit()
            await tx.close()

        if trigger_background_jobs:
            _enqueue_embed_reconciliation(
                ontology_id=ontology_id,
                instance_id=None,
                node_ids=[milestone_id],
                author_id="milestone-create",
            )

        # No milestone count or boundary validation enforced
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
        _enqueue_embed_reconciliation(
            ontology_id=ontology_id,
            instance_id=None,
            node_ids=[milestone_id],
            author_id="milestone-update",
        )

        # No milestone count or boundary validation enforced
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
        _enqueue_embed_reconciliation(
            ontology_id=ontology_id,
            instance_id=None,
            node_ids=[milestone_id],
            author_id="milestone-delete",
        )

        milestones = await self.list_milestones(instance_id, scene_id)
        begin_count = sum(1 for milestone in milestones if milestone.boundary_type == "begin")
        end_count = sum(1 for milestone in milestones if milestone.boundary_type == "end")
        if len(milestones) < 2 or begin_count != 1 or end_count != 1:
            raise ValueError(
                "Cannot delete milestone because scene would become invalid (needs >=2 milestones with one begin and one end)"
            )
