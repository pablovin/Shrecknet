"""Neo4j business rules for ontology-scoped character administration."""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from neo4j import AsyncSession
from neo4j.exceptions import ConstraintError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.models.ontology import Ontology, OntologyEntity, OntologyProperty, PropertyDataType
from app.models.character_embodiment import CharacterEmbodimentDraft, CharacterEmbodimentDraftStatus

from app.schemas.character_agent import (
    CharacterAgentCreate, CharacterAgentCreateRequest, CharacterAgentRead, CharacterAgentUpdate,
    CharacterAspectAssignmentCreate, CharacterAspectAssignmentRead,
    CharacterAspectAssignmentUpdate, CharacterAspectCreate, CharacterAspectRead,
    CharacterAspectUpdate, CharacterGoalCreate, CharacterGoalRead,
    CharacterGoalUpdate, CharacterEmbodimentCandidate,
    CharacterEmbodimentCandidatePage,
    CharacterBeliefCreate, CharacterBeliefRead, CharacterBeliefUpdate,
    CharacterImpactCreate, CharacterImpactRead, CharacterImpactUpdate,
    EmotionalInterpretationCreate, EmotionalInterpretationRead,
    EmotionalInterpretationUpdate, ScenePerspectiveAggregateRead,
    ScenePerspectiveCreate, ScenePerspectiveRead, ScenePerspectiveUpdate,
    CharacterIdentityRevisionRead, CharacterIdentityChangeRead,
    CharacterTimelineProjection, BEHAVIOURAL_AXES,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _props(record: Any, key: str = "node") -> dict[str, Any]:
    return dict(record[key])


def _agent_data(data: dict[str, Any]) -> dict[str, Any]:
    data["entity_instance_id"] = data["embodied_entity_instance_id"]
    data.setdefault("trait_adherence", 80)
    data.setdefault("visibility", "private")
    return data


class CharacterAgentService:
    def __init__(self, sql_session: SqlAsyncSession, graph_session: AsyncSession) -> None:
        self.sql = sql_session
        self.graph = graph_session

    async def _require_ontology(self, ontology_id: int) -> None:
        result = await self.sql.execute(select(Ontology.id).where(Ontology.id == ontology_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Ontology not found")

    async def _entity_type(self, ontology_id: int, entity_definition_id: int) -> OntologyEntity:
        result = await self.sql.execute(
            select(OntologyEntity).where(
                OntologyEntity.id == entity_definition_id,
                OntologyEntity.ontology_id == ontology_id,
            )
        )
        entity_type = result.scalar_one_or_none()
        if entity_type is None:
            raise HTTPException(status_code=404, detail="Entity type not found in ontology")
        return entity_type

    async def _image_property_ids(self, entity_definition_id: int) -> list[str]:
        result = await self.sql.execute(
            select(OntologyProperty.id).where(
                OntologyProperty.entity_id == entity_definition_id,
                OntologyProperty.data_type == PropertyDataType.IMAGE,
            )
        )
        return [str(value) for value in result.scalars().all()]

    @staticmethod
    def _property_image(properties: Any, image_property_ids: list[str]) -> str | None:
        if not properties:
            return None
        try:
            values = json.loads(properties) if isinstance(properties, str) else dict(properties)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        for property_id in image_property_ids:
            value = values.get(property_id)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def list_embodiment_candidates(
        self, ontology_id: int, entity_definition_id: int, search: str | None,
        skip: int, limit: int,
    ) -> CharacterEmbodimentCandidatePage:
        await self._require_ontology(ontology_id)
        entity_type = await self._entity_type(ontology_id, entity_definition_id)
        image_property_ids = await self._image_property_ids(entity_definition_id)
        search_text = (search or "").strip().casefold()
        params = {
            "ontology_id": ontology_id, "entity_definition_id": entity_definition_id,
            "search": search_text, "skip": skip, "limit": limit,
        }
        where = """
            entity.ontology_id = $ontology_id
            AND toInteger(entity.entity_definition_id) = $entity_definition_id
            AND NOT (:CharacterAgent)-[:EMBODIES]->(entity)
            AND ($search = '' OR toLower(coalesce(entity.alias, '')) CONTAINS $search
                 OR toLower(coalesce(entity.text, '')) CONTAINS $search
                 OR toLower(coalesce(entity.autogenerated_text, '')) CONTAINS $search)
        """
        count_row = await self._one(
            f"MATCH (entity:EntityInstance) WHERE {where} RETURN count(entity) AS total",
            **params,
        )
        result = await self.graph.run(
            f"MATCH (entity:EntityInstance) WHERE {where} RETURN entity "
            "ORDER BY toLower(coalesce(entity.alias, '')), entity.entity_instance_id "
            "SKIP $skip LIMIT $limit",
            **params,
        )
        candidates = []
        async for row in result:
            entity = dict(row["entity"])
            name = str(entity.get("alias") or entity["entity_instance_id"])
            background = str(entity.get("text") or entity.get("autogenerated_text") or name)
            avatar = entity.get("node_avatar_url")
            candidates.append(CharacterEmbodimentCandidate(
                entity_instance_id=str(entity["entity_instance_id"]), ontology_id=ontology_id,
                entity_definition_id=entity_definition_id, entity_type_name=entity_type.name,
                entity_type_image_url=entity_type.image_url, name=name,
                background_story=background, avatar_url=avatar,
                image_url=self._property_image(entity.get("properties"), image_property_ids),
            ))
        return CharacterEmbodimentCandidatePage(
            total=int(count_row["total"] if count_row else 0), skip=skip,
            limit=limit, results=candidates,
        )

    async def _one(self, query: str, **params) -> Any | None:
        result = await self.graph.run(query, **params)
        return await result.single()

    async def _node(self, label: str, node_id: str) -> dict[str, Any]:
        row = await self._one(
            f"MATCH (node:{label} {{id: $node_id}}) "
            "OPTIONAL MATCH (node)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN node, scene.id AS obtained_from_scene_id",
            node_id=node_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        data = _props(row)
        if label != "CharacterAgent":
            data["obtained_from_scene_id"] = row["obtained_from_scene_id"]
        return data

    async def create_agent(
        self, payload: CharacterAgentCreate | CharacterAgentCreateRequest, user_id: int
    ) -> CharacterAgentRead:
        if isinstance(payload, CharacterAgentCreateRequest) and (
            payload.embodiment_draft_id or payload.aspects or payload.goals
        ):
            return await self._create_agent_aggregate(payload, user_id)
        if isinstance(payload, CharacterAgentCreateRequest):
            payload = CharacterAgentCreate.model_validate(payload.model_dump(
                exclude={"embodiment_draft_id", "aspects", "goals"}
            ))
        data = payload.model_dump(mode="json")
        entity_id = data.pop("entity_instance_id")
        ontology_id = data["ontology_id"]
        await self._require_ontology(ontology_id)
        image_property_ids_result = await self.sql.execute(
            select(OntologyProperty.id).join(
                OntologyEntity, OntologyEntity.id == OntologyProperty.entity_id
            ).where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyProperty.data_type == PropertyDataType.IMAGE,
            )
        )
        image_property_ids = [str(value) for value in image_property_ids_result.scalars().all()]
        node_id, timestamp = str(uuid4()), _now()

        existing_agent = await self.graph_session.run(
            "MATCH (agent:CharacterAgent {embodied_entity_instance_id: $entity_id}) RETURN agent.id AS id",
            entity_id=entity_id,
        )
        existing_row = await existing_agent.single()
        if existing_row:
            await self.delete_agent(existing_row["id"])

        async def work(tx):
            result = await tx.run(
                """
                OPTIONAL MATCH (entity:EntityInstance {entity_instance_id: $entity_id})
                RETURN entity.ontology_id AS entity_ontology_id, entity IS NOT NULL AS entity_exists,
                       entity.alias AS entity_name, entity.text AS entity_text,
                       entity.autogenerated_text AS entity_autogenerated_text,
                       entity.node_avatar_url AS entity_avatar_url,
                       entity.properties AS entity_properties
                """, entity_id=entity_id,
            )
            scope = await result.single()
            if not scope["entity_exists"]:
                raise HTTPException(status_code=404, detail="EntityInstance not found")
            if scope["entity_ontology_id"] is None or int(scope["entity_ontology_id"]) != ontology_id:
                raise HTTPException(status_code=400, detail="EntityInstance does not belong to ontology")
            data["name"] = data.get("name") or scope["entity_name"] or entity_id
            data["background_story"] = (
                data.get("background_story") or scope["entity_text"]
                or scope["entity_autogenerated_text"] or data["name"]
            )
            data["image_url"] = (
                data.get("image_url") or scope["entity_avatar_url"]
                or self._property_image(scope["entity_properties"], image_property_ids)
            )
            props = {
                **data, "id": node_id,
                "embodied_entity_instance_id": entity_id, "created_by_user_id": user_id,
                "created_at": timestamp, "updated_at": timestamp,
            }
            created = await tx.run(
                """
                MATCH (entity:EntityInstance {entity_instance_id: $entity_id, ontology_id: $ontology_id})
                CREATE (agent:CharacterAgent)
                SET agent = $props
                CREATE (agent)-[:EMBODIES]->(entity)
                RETURN agent
                """, ontology_id=ontology_id, entity_id=entity_id, props=props,
            )
            created_agent = _props(await created.single(), "agent")
            await self._create_revision_tx(
                tx, created_agent, str(uuid4()), 0, timestamp,
                provenance_type="initial",
            )
            return created_agent

        try:
            created = await self.graph.execute_write(work)
        except ConstraintError as exc:
            raise HTTPException(status_code=409, detail="EntityInstance already has a CharacterAgent") from exc
        return CharacterAgentRead.model_validate(_agent_data(created))

    async def _create_agent_aggregate(
        self, payload: CharacterAgentCreateRequest, user_id: int,
    ) -> CharacterAgentRead:
        """Create a reviewed form payload and its aspects/goals in one transaction."""
        draft = None
        draft_id = payload.embodiment_draft_id
        if draft_id:
            draft = await self.sql.get(CharacterEmbodimentDraft, draft_id)
            if not draft:
                raise HTTPException(status_code=404, detail="Embodiment draft not found")
            if draft.status == CharacterEmbodimentDraftStatus.ACCEPTED and draft.target_character_agent_id:
                return await self.get_agent(draft.target_character_agent_id)
            if draft.status != CharacterEmbodimentDraftStatus.READY:
                raise HTTPException(status_code=409, detail="Embodiment draft is not ready")
            if (
                draft.ontology_id != payload.ontology_id
                or draft.source_entity_id != payload.entity_instance_id
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Embodiment draft does not match the requested entity and ontology",
                )
            known_evidence = set(json.loads(draft.source_evidence_ids or "[]"))
            referenced = {
                evidence_id
                for item in [*payload.aspects, *payload.goals]
                for evidence_id in item.evidence_ids
            }
            if not referenced <= known_evidence:
                raise HTTPException(status_code=422, detail="Creation payload references unknown draft evidence")

        entity_id, ontology_id = payload.entity_instance_id, payload.ontology_id
        await self._require_ontology(ontology_id)
        node_id, timestamp = str(uuid4()), _now()
        base = payload.model_dump(
            mode="json",
            exclude={"entity_instance_id", "embodiment_draft_id", "aspects", "goals"},
        )
        image_property_ids_result = await self.sql.execute(
            select(OntologyProperty.id).join(
                OntologyEntity, OntologyEntity.id == OntologyProperty.entity_id
            ).where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyProperty.data_type == PropertyDataType.IMAGE,
            )
        )
        image_property_ids = [
            str(value) for value in image_property_ids_result.scalars().all()
        ]
        agent_props = {
            "id": node_id, "ontology_id": ontology_id,
            "embodied_entity_instance_id": entity_id, **base,
            "created_by_user_id": user_id, "created_at": timestamp, "updated_at": timestamp,
            "embodiment_draft_id": draft_id,
        }
        aspects = [item.model_dump(mode="json") for item in payload.aspects]
        goals = [item.model_dump(mode="json") for item in payload.goals]
        timeline = (
            CharacterTimelineProjection.model_validate_json(draft.timeline_projection)
            if draft and draft.timeline_projection else None
        )

        async def work(tx):
            if draft_id:
                existing = await tx.run(
                    """
                    MATCH (agent:CharacterAgent)
                    WHERE agent[$draft_property] = $draft_id
                    RETURN agent
                    """,
                    draft_property="embodiment_draft_id", draft_id=draft_id,
                )
                row = await existing.single()
                if row:
                    return _props(row, "agent")
            scope = await tx.run(
                """
                OPTIONAL MATCH (entity:EntityInstance {entity_instance_id:$entity_id})
                RETURN entity, EXISTS {
                  MATCH (:CharacterAgent)-[:EMBODIES]->(entity)
                } AS embodied
                """, entity_id=entity_id,
            )
            scope_row = await scope.single()
            if not scope_row or scope_row["entity"] is None:
                raise HTTPException(status_code=404, detail="EntityInstance not found")
            entity = dict(scope_row["entity"])
            if int(entity.get("ontology_id") or 0) != ontology_id:
                raise HTTPException(status_code=400, detail="EntityInstance does not belong to ontology")
            if scope_row["embodied"]:
                raise HTTPException(status_code=409, detail="EntityInstance already has a CharacterAgent")
            agent_props["name"] = agent_props.get("name") or entity.get("alias") or entity_id
            agent_props["background_story"] = (
                agent_props.get("background_story") or entity.get("text")
                or entity.get("autogenerated_text") or agent_props["name"]
            )
            agent_props["image_url"] = (
                agent_props.get("image_url") or entity.get("node_avatar_url")
                or self._property_image(entity.get("properties"), image_property_ids)
            )
            created = await tx.run(
                """
                MATCH (entity:EntityInstance {entity_instance_id:$entity_id})
                CREATE (agent:CharacterAgent) SET agent=$props
                CREATE (agent)-[:EMBODIES]->(entity)
                RETURN agent
                """, entity_id=entity_id, props=agent_props,
            )
            created_agent = _props(await created.single(), "agent")
            for item in aspects:
                normalized = _normalize_name(item["name"])
                aspect_id = str(uuid4())
                definition_props = {
                    "id": aspect_id, "ontology_id": ontology_id, "name": item["name"],
                    "normalized_name": normalized, "category": item["category"],
                    "description": item.get("description"), "status": "active",
                    "created_at": timestamp, "updated_at": timestamp,
                    "generated_by_embodiment_draft_id": draft_id,
                    "evidence_ids": json.dumps(item["evidence_ids"]),
                    "confidence": item.get("confidence"),
                    "justification": item.get("justification"),
                }
                await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id})
                    MERGE (aspect:CharacterAspect {ontology_id:$ontology_id, normalized_name:$normalized})
                    ON CREATE SET aspect=$props
                    MERGE (agent)-[rel:HAS_ASPECT]->(aspect)
                    ON CREATE SET rel.importance=$importance, rel.intensity=$intensity,
                      rel.status='active', rel.created_at=$timestamp, rel.updated_at=$timestamp,
                      rel.evidence_ids=$evidence_ids, rel.confidence=$confidence,
                      rel.justification=$justification
                    """, agent_id=node_id, ontology_id=ontology_id, normalized=normalized,
                    props=definition_props, importance=item["importance"],
                    intensity=item.get("intensity"), timestamp=timestamp,
                    evidence_ids=json.dumps(item["evidence_ids"]), confidence=item.get("confidence"),
                    justification=item.get("justification"),
                )
            for item in goals:
                normalized = _normalize_name(item["title"])
                goal_id = str(uuid4())
                existing_goal = await tx.run(
                    """
                    MATCH (goal:CharacterGoal {ontology_id:$ontology_id})
                    WHERE toLower(trim(goal.title))=$normalized
                    RETURN goal.id AS id ORDER BY goal.created_at, goal.id LIMIT 1
                    """, ontology_id=ontology_id, normalized=normalized,
                )
                existing_goal_row = await existing_goal.single()
                if existing_goal_row:
                    goal_id = str(existing_goal_row["id"])
                else:
                    await tx.run(
                        "CREATE (goal:CharacterGoal) SET goal=$props",
                        props={
                            "id": goal_id, "ontology_id": ontology_id, "title": item["title"],
                            "description": item["description"], "goal_type": item["goal_type"],
                            "status": item["status"], "priority": item["priority"],
                            "commitment": item["commitment"], "created_at": timestamp,
                            "updated_at": timestamp, "generated_by_embodiment_draft_id": draft_id,
                            "evidence_ids": json.dumps(item["evidence_ids"]),
                            "confidence": item.get("confidence"), "basis": item.get("basis"),
                            "justification": item.get("justification"),
                        },
                    )
                await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id}), (goal:CharacterGoal {id:$goal_id})
                    MERGE (agent)-[rel:PURSUES]->(goal)
                    ON CREATE SET rel.created_at=$timestamp, rel.evidence_ids=$evidence_ids,
                      rel.confidence=$confidence, rel.justification=$justification,
                      rel.status=$status, rel.priority=$priority, rel.commitment=$commitment
                    """, agent_id=node_id, goal_id=goal_id, timestamp=timestamp,
                    evidence_ids=json.dumps(item["evidence_ids"]),
                    confidence=item.get("confidence"),
                    justification=item.get("justification"),
                    status=item["status"], priority=item["priority"],
                    commitment=item["commitment"],
                )
            if timeline:
                await self._persist_timeline_tx(
                    tx, created_agent, timeline, timestamp,
                    provider=draft.provider if draft else None,
                    model=draft.model if draft else None,
                    prompt_version=draft.prompt_version if draft else None,
                )
            else:
                await self._create_revision_tx(
                    tx, created_agent, str(uuid4()), 0, timestamp,
                    provenance_type="initial",
                    active_aspect_ids=[
                        str(item.get("suggestion_id") or item["name"]) for item in aspects
                    ],
                    active_goal_ids=[
                        str(item.get("suggestion_id") or item["title"]) for item in goals
                    ],
                )
            return created_agent
        try:
            result = await self.graph.execute_write(work)
        except ConstraintError as exc:
            raise HTTPException(status_code=409, detail="EntityInstance already has a CharacterAgent") from exc
        agent = CharacterAgentRead.model_validate(_agent_data(result))
        if draft:
            draft.status = CharacterEmbodimentDraftStatus.ACCEPTED
            draft.active_entity_key = None
            draft.target_character_agent_id = agent.id
            await self.sql.commit()
        return agent

    async def list_agents(self, ontology_id: int | None, agent_status: str | None,
                          entity_id: str | None, skip: int, limit: int,
                          public_only: bool = False) -> list[CharacterAgentRead]:
        clauses, params = [], {"skip": skip, "limit": limit}
        if public_only:
            clauses.append("coalesce(agent.visibility, 'private') = 'public'")
        if ontology_id is not None:
            clauses.append("agent.ontology_id = $ontology_id"); params["ontology_id"] = ontology_id
        if agent_status:
            clauses.append("agent.status = $status"); params["status"] = agent_status
        if entity_id:
            clauses.append("agent.embodied_entity_instance_id = $entity_id"); params["entity_id"] = entity_id
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        result = await self.graph.run(
            f"MATCH (agent:CharacterAgent){where} RETURN agent "
            "ORDER BY agent.created_at DESC, agent.id ASC SKIP $skip LIMIT $limit", **params,
        )
        return [CharacterAgentRead.model_validate(_agent_data(_props(row, "agent"))) async for row in result]

    async def get_agent(self, node_id: str, public_only: bool = False) -> CharacterAgentRead:
        row = await self._one(
            "MATCH (node:CharacterAgent {id: $node_id}) "
            "WHERE NOT $public_only OR coalesce(node.visibility, 'private') = 'public' "
            "RETURN node",
            node_id=node_id,
            public_only=public_only,
        )
        if not row:
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        return CharacterAgentRead.model_validate(_agent_data(_props(row)))

    async def load_query_snapshot(self, node_id: str, public_only: bool = False) -> dict[str, Any]:
        """Load the complete active character identity in one graph operation."""
        row = await self._one(
            """
            MATCH (agent:CharacterAgent {id: $node_id})-[:EMBODIES]->(entity:EntityInstance)
            WHERE NOT $public_only OR coalesce(agent.visibility, 'private') = 'public'
            CALL {
              WITH agent
              OPTIONAL MATCH (agent)-[assignment:HAS_ASPECT]->(aspect:CharacterAspect)
              WHERE aspect.status = 'active' AND coalesce(assignment.status, 'active') = 'active'
              WITH aspect, assignment
              ORDER BY assignment.importance DESC, assignment.intensity DESC, aspect.id ASC
              RETURN collect(CASE WHEN aspect IS NULL THEN null ELSE {
                id: aspect.id, name: aspect.name, category: aspect.category,
                description: aspect.description, importance: assignment.importance,
                intensity: assignment.intensity, notes: assignment.notes
              } END) AS aspects
            }
            CALL {
              WITH agent
              OPTIONAL MATCH (agent)-[pursuit:PURSUES]->(goal:CharacterGoal)
              WHERE coalesce(pursuit.status, goal.status) = 'active'
              WITH goal, pursuit
              // Legacy fallback was: ORDER BY goal.priority DESC
              ORDER BY coalesce(pursuit.priority, goal.priority) DESC,
                       coalesce(pursuit.commitment, goal.commitment) DESC, goal.id ASC
              RETURN collect(CASE WHEN goal IS NULL THEN null ELSE {
                id: goal.id, title: goal.title, description: goal.description,
                goal_type: goal.goal_type, status: coalesce(pursuit.status, goal.status),
                priority: coalesce(pursuit.priority, goal.priority),
                commitment: coalesce(pursuit.commitment, goal.commitment)
              } END) AS goals
            }
            RETURN agent, entity, [item IN aspects WHERE item IS NOT NULL] AS aspects,
                   [item IN goals WHERE item IS NOT NULL] AS goals
            """,
            node_id=node_id, public_only=public_only,
        )
        if not row:
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        agent = dict(row["agent"])
        if agent.get("status") != "active":
            raise HTTPException(status_code=409, detail="CharacterAgent is not active")
        agent.setdefault("trait_adherence", 80)
        traits = {
            key: int(agent.get(key, 50))
            for key in (
                "calm_aggressive", "cautious_reckless", "compassionate_ruthless",
                "trusting_suspicious", "honest_deceptive", "patient_impulsive",
                "humble_proud", "cooperative_dominating",
            )
        }
        return {
            "character_agent": {
                "name": str(agent.get("name") or row["entity"].get("alias") or "Character"),
                "subtitle": agent.get("subtitle"),
                "background_story": str(agent.get("background_story") or ""),
                "behavioural_traits": traits,
                "trait_adherence": int(agent["trait_adherence"]),
            },
            "aspects": [dict(item) for item in row["aspects"]],
            "goals": [dict(item) for item in row["goals"]],
        }

    async def ensure_queryable(self, node_id: str, public_only: bool = False) -> None:
        """Enforce query visibility and active status without loading identity data."""
        row = await self._one(
            """
            MATCH (agent:CharacterAgent {id: $node_id})
            WHERE NOT $public_only OR coalesce(agent.visibility, 'private') = 'public'
            RETURN agent.status AS status
            """,
            node_id=node_id,
            public_only=public_only,
        )
        if not row:
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        if row["status"] != "active":
            raise HTTPException(status_code=409, detail="CharacterAgent is not active")

    async def update_agent(self, node_id: str, payload: CharacterAgentUpdate) -> CharacterAgentRead:
        changes = payload.model_dump(exclude_unset=True, mode="json")
        timestamp = _now()
        changes["updated_at"] = timestamp

        async def work(tx):
            found = await tx.run(
                "MATCH (agent:CharacterAgent {id:$node_id}) "
                "OPTIONAL MATCH (agent)-[:HAS_REVISION]->(latest:CharacterIdentityRevision) "
                "WITH agent, latest ORDER BY latest.revision_number DESC LIMIT 1 "
                "RETURN agent, latest",
                node_id=node_id,
            )
            current = await found.single()
            if not current:
                return None
            before = dict(current["agent"])
            updated = await tx.run(
                "MATCH (agent:CharacterAgent {id:$node_id}) SET agent += $changes RETURN agent AS node",
                node_id=node_id, changes=changes,
            )
            row = await updated.single()
            after = dict(row["node"])
            revision_number = int(
                dict(current["latest"]).get("revision_number", -1)
                if current["latest"] is not None else -1
            ) + 1
            revision_id = str(uuid4())
            await self._create_revision_tx(
                tx, after, revision_id, revision_number, timestamp,
                provenance_type="manual",
            )
            for field in ("subtitle", *BEHAVIOURAL_AXES, "trait_adherence"):
                if field in changes and before.get(field) != after.get(field):
                    await self._create_change_tx(
                        tx, node_id, revision_id, revision_number, timestamp,
                        change_type="subtitle" if field == "subtitle" else "axis",
                        field_name=field, previous=before.get(field),
                        new=after.get(field), provenance_type="manual",
                    )
            return row

        row = await self.graph.execute_write(work)
        if not row:
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        return CharacterAgentRead.model_validate(_agent_data(_props(row)))

    async def _create_revision_tx(
        self, tx, agent: dict[str, Any], revision_id: str, revision_number: int,
        timestamp: str, *, provenance_type: str, source_group_id: str | None = None,
        last_processed_scene_id: str | None = None, active_aspect_ids: list[str] | None = None,
        active_goal_ids: list[str] | None = None, provider: str | None = None,
        model: str | None = None, prompt_version: str | None = None,
    ) -> None:
        axes = {axis: int(agent.get(axis, 50)) for axis in BEHAVIOURAL_AXES}
        props = {
            "id": revision_id, "character_agent_id": agent["id"],
            "revision_number": revision_number, "source_group_id": source_group_id,
            "last_processed_scene_id": last_processed_scene_id,
            "name": str(agent.get("name") or "Character"),
            "subtitle": agent.get("subtitle"),
            "trait_adherence": int(agent.get("trait_adherence", 80)),
            "behavioural_axes": json.dumps(axes),
            "active_aspect_ids": json.dumps(active_aspect_ids or []),
            "active_goal_ids": json.dumps(active_goal_ids or []),
            "provenance_type": provenance_type, "provider": provider, "model": model,
            "prompt_version": prompt_version, "created_at": timestamp,
        }
        await tx.run(
            """
            MATCH (agent:CharacterAgent {id:$agent_id})
            CREATE (revision:CharacterIdentityRevision) SET revision=$props
            CREATE (agent)-[:HAS_REVISION]->(revision)
            WITH revision
            OPTIONAL MATCH (source:EntityInstance {entity_instance_id:$source_group_id})
            FOREACH (_ IN CASE WHEN source IS NULL THEN [] ELSE [1] END |
              CREATE (revision)-[:CONSOLIDATED_FROM]->(source))
            """,
            agent_id=agent["id"], source_group_id=source_group_id, props=props,
        )

    async def _create_change_tx(
        self, tx, agent_id: str, revision_id: str, revision_number: int,
        timestamp: str, *, change_type: str, field_name: str, previous: Any,
        new: Any, provenance_type: str, source_group_id: str | None = None,
        confidence: float | None = None, justification: str | None = None,
        evidence_ids: list[str] | None = None,
    ) -> None:
        props = {
            "id": str(uuid4()), "character_agent_id": agent_id,
            "revision_number": revision_number, "source_group_id": source_group_id,
            "change_type": change_type, "field_name": field_name,
            "previous_value": json.dumps(previous), "new_value": json.dumps(new),
            "confidence": confidence, "justification": justification,
            "evidence_ids": json.dumps(evidence_ids or []),
            "provenance_type": provenance_type, "created_at": timestamp,
        }
        await tx.run(
            """
            MATCH (revision:CharacterIdentityRevision {id:$revision_id})
            CREATE (change:CharacterIdentityChange) SET change=$props
            CREATE (revision)-[:HAS_CHANGE]->(change)
            """,
            revision_id=revision_id, props=props,
        )

    async def _persist_timeline_tx(
        self, tx, agent: dict[str, Any], timeline: CharacterTimelineProjection,
        timestamp: str, *, provider: str | None, model: str | None,
        prompt_version: str | None,
    ) -> None:
        revision_ids: dict[int, str] = {}
        previous = None
        projections = {
            item.resulting_revision.revision_number: item
            for item in timeline.source_projections
        }
        for revision in timeline.revisions:
            revision_id = str(uuid4())
            revision_ids[revision.revision_number] = revision_id
            snapshot = {
                **agent, "name": revision.name, "subtitle": revision.subtitle,
                "trait_adherence": revision.trait_adherence,
                **revision.behavioural_axes,
            }
            aspect_ids = [
                str(item.suggestion_id or item.name) for item in revision.active_aspects
            ]
            goal_ids = [
                str(item.suggestion_id or item.title) for item in revision.active_goals
            ]
            await self._create_revision_tx(
                tx, snapshot, revision_id, revision.revision_number, timestamp,
                provenance_type="initial" if revision.revision_number == 0 else "generated",
                source_group_id=revision.source_group_id,
                last_processed_scene_id=revision.last_processed_scene_id,
                active_aspect_ids=aspect_ids, active_goal_ids=goal_ids,
                provider=provider, model=model, prompt_version=prompt_version,
            )
            projection = projections.get(revision.revision_number)
            if previous and projection:
                for axis in projection.axis_changes:
                    await self._create_change_tx(
                        tx, agent["id"], revision_id, revision.revision_number,
                        timestamp, change_type="axis", field_name=axis.axis,
                        previous=previous.behavioural_axes[axis.axis], new=axis.value,
                        provenance_type="generated",
                        source_group_id=revision.source_group_id,
                        confidence=axis.confidence, justification=axis.justification,
                        evidence_ids=axis.evidence_ids,
                    )
                subtitle_change = projection.subtitle_change
                if subtitle_change.operation != "retain":
                    await self._create_change_tx(
                        tx, agent["id"], revision_id, revision.revision_number,
                        timestamp, change_type="subtitle", field_name="subtitle",
                        previous=previous.subtitle, new=revision.subtitle,
                        provenance_type="generated",
                        source_group_id=revision.source_group_id,
                        confidence=subtitle_change.confidence,
                        justification=subtitle_change.justification,
                        evidence_ids=subtitle_change.evidence_ids,
                    )
                for kind, old_ids, new_ids in (
                    ("aspect", {
                        str(item.suggestion_id or item.name)
                        for item in previous.active_aspects
                    }, set(aspect_ids)),
                    ("goal", {
                        str(item.suggestion_id or item.title)
                        for item in previous.active_goals
                    }, set(goal_ids)),
                ):
                    for item_id in sorted(old_ids ^ new_ids):
                        await self._create_change_tx(
                            tx, agent["id"], revision_id, revision.revision_number,
                            timestamp, change_type=kind, field_name=item_id,
                            previous="active" if item_id in old_ids else None,
                            new="active" if item_id in new_ids else "inactive",
                            provenance_type="generated",
                            source_group_id=revision.source_group_id,
                        )
            previous = revision

        for projection in timeline.source_projections:
            starting_revision_id = revision_ids[projection.starting_revision_number]
            for item in projection.perspectives:
                perspective_id = str(uuid4())
                props = {
                    "id": perspective_id, "ontology_id": agent["ontology_id"],
                    "character_agent_id": agent["id"], "scene_id": item.scene_id,
                    "generated_with_revision_id": starting_revision_id,
                    "source_group_id": projection.source_group_id,
                    **item.model_dump(mode="json", exclude={"scene_id"}),
                    "created_at": timestamp, "updated_at": timestamp,
                }
                await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id}),
                          (scene:Scene {id:$scene_id}),
                          (revision:CharacterIdentityRevision {id:$revision_id})
                    CREATE (perspective:ScenePerspective) SET perspective=$props
                    CREATE (agent)-[:HAS_PERSPECTIVE]->(perspective)
                    CREATE (perspective)-[:PROJECTS_ON]->(scene)
                    CREATE (perspective)-[:GENERATED_WITH]->(revision)
                    """,
                    agent_id=agent["id"], scene_id=item.scene_id,
                    revision_id=starting_revision_id, props=props,
                )

    async def list_identity_revisions(
        self, agent_id: str, skip: int, limit: int, public_only: bool = False,
    ) -> list[CharacterIdentityRevisionRead]:
        result = await self.graph.run(
            """
            MATCH (agent:CharacterAgent {id:$agent_id})-[:HAS_REVISION]->
                  (revision:CharacterIdentityRevision)
            WHERE NOT $public_only OR coalesce(agent.visibility, 'private')='public'
            RETURN revision AS node ORDER BY revision.revision_number ASC
            SKIP $skip LIMIT $limit
            """,
            agent_id=agent_id, public_only=public_only, skip=skip, limit=limit,
        )
        values = [CharacterIdentityRevisionRead.model_validate(_props(row)) async for row in result]
        if not values and not await self._one(
            "MATCH (agent:CharacterAgent {id:$agent_id}) "
            "WHERE NOT $public_only OR coalesce(agent.visibility,'private')='public' RETURN agent",
            agent_id=agent_id, public_only=public_only,
        ):
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        return values

    async def list_identity_changes(
        self, agent_id: str, change_type: str | None, skip: int, limit: int,
        public_only: bool = False,
    ) -> list[CharacterIdentityChangeRead]:
        result = await self.graph.run(
            """
            MATCH (agent:CharacterAgent {id:$agent_id})-[:HAS_REVISION]->
                  (:CharacterIdentityRevision)-[:HAS_CHANGE]->(change:CharacterIdentityChange)
            WHERE (NOT $public_only OR coalesce(agent.visibility,'private')='public')
              AND ($change_type IS NULL OR change.change_type=$change_type)
            RETURN change AS node ORDER BY change.revision_number ASC, change.created_at ASC
            SKIP $skip LIMIT $limit
            """,
            agent_id=agent_id, change_type=change_type, public_only=public_only,
            skip=skip, limit=limit,
        )
        return [CharacterIdentityChangeRead.model_validate(_props(row)) async for row in result]

    async def delete_agent(self, node_id: str) -> None:
        async def work(tx):
            found = await tx.run(
                "MATCH (agent:CharacterAgent {id: $node_id}) "
                "OPTIONAL MATCH (agent)-[:HAS_ASPECT]->(aspect:CharacterAspect) "
                "OPTIONAL MATCH (agent)-[:PURSUES]->(goal:CharacterGoal) "
                "RETURN agent, collect(DISTINCT aspect.id) AS aspects, collect(DISTINCT goal.id) AS goals",
                node_id=node_id,
            )
            row = await found.single()
            if not row:
                raise HTTPException(status_code=404, detail="CharacterAgent not found")
            aspect_ids, goal_ids = row["aspects"], row["goals"]
            await tx.run(
                """
                MATCH (:CharacterAgent {id:$node_id})-[:HAS_PERSPECTIVE]->
                      (perspective:ScenePerspective)
                OPTIONAL MATCH (perspective)-[:EVOKES|FORMS_BELIEF|HAS_IMPACT]->(child)
                DETACH DELETE child, perspective
                """,
                node_id=node_id,
            )
            await tx.run(
                """
                MATCH (:CharacterAgent {id:$node_id})-[:HAS_REVISION]->
                      (revision:CharacterIdentityRevision)
                OPTIONAL MATCH (revision)-[:HAS_CHANGE]->(change:CharacterIdentityChange)
                DETACH DELETE change, revision
                """,
                node_id=node_id,
            )
            await tx.run("MATCH (agent:CharacterAgent {id: $node_id}) DETACH DELETE agent", node_id=node_id)
            await tx.run(
                "UNWIND $ids AS id MATCH (node:CharacterAspect {id:id}) "
                "WHERE NOT (:CharacterAgent)-[:HAS_ASPECT]->(node) DETACH DELETE node",
                ids=aspect_ids,
            )
            await tx.run(
                "UNWIND $ids AS id MATCH (node:CharacterGoal {id:id}) "
                "WHERE NOT (:CharacterAgent)-[:PURSUES]->(node) DETACH DELETE node",
                ids=goal_ids,
            )
        await self.graph.execute_write(work)

    async def _validate_scene(self, tx, ontology_id: int, scene_id: str | None) -> None:
        if scene_id is None:
            return
        result = await tx.run(
            "MATCH (scene:Scene {id:$scene_id, ontology_id:$ontology_id}) RETURN scene",
            ontology_id=ontology_id, scene_id=scene_id,
        )
        if not await result.single():
            raise HTTPException(status_code=400, detail="Scene does not belong to OntologyInstance")

    async def _create_definition(self, label: str, payload, user_id: int) -> dict[str, Any]:
        data = payload.model_dump(mode="json")
        scene_id = data.pop("obtained_from_scene_id", None)
        node_id, timestamp = str(uuid4()), _now()
        await self._require_ontology(data["ontology_id"])
        if label == "CharacterAspect":
            data["normalized_name"] = _normalize_name(data["name"])

        async def work(tx):
            await self._validate_scene(tx, data["ontology_id"], scene_id)
            if label == "CharacterAspect":
                duplicate = await tx.run(
                    "MATCH (n:CharacterAspect {ontology_id:$ontology_id, normalized_name:$normalized}) RETURN n.id AS id",
                    ontology_id=data["ontology_id"], normalized=data["normalized_name"],
                )
                if await duplicate.single():
                    raise HTTPException(status_code=409, detail="CharacterAspect normalized name already exists")
            props = {**data, "id": node_id, "created_at": timestamp, "updated_at": timestamp}
            created = await tx.run(f"CREATE (node:{label}) SET node = $props RETURN node", props=props)
            node = _props(await created.single())
            if scene_id:
                await tx.run(
                    f"MATCH (node:{label} {{id:$node_id}}), (scene:Scene {{id:$scene_id}}) CREATE (node)-[:OBTAINED_FROM]->(scene)",
                    node_id=node_id, scene_id=scene_id,
                )
            node["obtained_from_scene_id"] = scene_id
            return node
        try:
            return await self.graph.execute_write(work)
        except ConstraintError as exc:
            if label == "CharacterAspect":
                raise HTTPException(status_code=409, detail="CharacterAspect normalized name already exists") from exc
            raise

    async def create_aspect(self, payload: CharacterAspectCreate, user_id: int) -> CharacterAspectRead:
        return CharacterAspectRead.model_validate(await self._create_definition("CharacterAspect", payload, user_id))

    async def create_goal(self, payload: CharacterGoalCreate, user_id: int) -> CharacterGoalRead:
        return CharacterGoalRead.model_validate(await self._create_definition("CharacterGoal", payload, user_id))

    async def _list_definitions(self, label: str, ontology_id: int, skip: int, limit: int):
        result = await self.graph.run(
            f"MATCH (node:{label} {{ontology_id:$ontology_id}}) "
            "OPTIONAL MATCH (node)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN node, scene.id AS obtained_from_scene_id "
            "ORDER BY node.created_at DESC, node.id ASC SKIP $skip LIMIT $limit",
            ontology_id=ontology_id, skip=skip, limit=limit,
        )
        rows = []
        async for row in result:
            item = _props(row); item["obtained_from_scene_id"] = row["obtained_from_scene_id"]; rows.append(item)
        return rows

    async def list_aspects(self, ontology_id: int, skip: int, limit: int) -> list[CharacterAspectRead]:
        return [CharacterAspectRead.model_validate(x) for x in await self._list_definitions("CharacterAspect", ontology_id, skip, limit)]

    async def list_goals(self, ontology_id: int, skip: int, limit: int) -> list[CharacterGoalRead]:
        return [CharacterGoalRead.model_validate(x) for x in await self._list_definitions("CharacterGoal", ontology_id, skip, limit)]

    async def get_aspect(self, node_id: str) -> CharacterAspectRead:
        return CharacterAspectRead.model_validate(await self._node("CharacterAspect", node_id))

    async def get_goal(self, node_id: str) -> CharacterGoalRead:
        return CharacterGoalRead.model_validate(await self._node("CharacterGoal", node_id))

    async def _update_definition(self, label: str, node_id: str, payload) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True, mode="json")
        scene_was_set = "obtained_from_scene_id" in changes
        scene_id = changes.pop("obtained_from_scene_id", None)
        if label == "CharacterAspect" and "name" in changes:
            changes["normalized_name"] = _normalize_name(changes["name"])
        changes["updated_at"] = _now()

        async def work(tx):
            current = await tx.run(f"MATCH (node:{label} {{id:$node_id}}) RETURN node", node_id=node_id)
            row = await current.single()
            if not row:
                raise HTTPException(status_code=404, detail=f"{label} not found")
            old = _props(row)
            await self._validate_scene(tx, old["ontology_id"], scene_id if scene_was_set else None)
            if label == "CharacterAspect" and "normalized_name" in changes:
                duplicate = await tx.run(
                    "MATCH (n:CharacterAspect {ontology_id:$ontology_id, normalized_name:$normalized}) WHERE n.id <> $node_id RETURN n.id",
                    ontology_id=old["ontology_id"], normalized=changes["normalized_name"], node_id=node_id,
                )
                if await duplicate.single():
                    raise HTTPException(status_code=409, detail="CharacterAspect normalized name already exists")
            updated = await tx.run(f"MATCH (node:{label} {{id:$node_id}}) SET node += $changes RETURN node", node_id=node_id, changes=changes)
            node = _props(await updated.single())
            if scene_was_set:
                await tx.run(f"MATCH (node:{label} {{id:$node_id}})-[r:OBTAINED_FROM]->() DELETE r", node_id=node_id)
                if scene_id:
                    await tx.run(f"MATCH (node:{label} {{id:$node_id}}), (scene:Scene {{id:$scene_id}}) CREATE (node)-[:OBTAINED_FROM]->(scene)", node_id=node_id, scene_id=scene_id)
            provenance = await tx.run(
                f"MATCH (node:{label} {{id:$node_id}}) "
                "OPTIONAL MATCH (node)-[obtained_rel]->(scene:Scene) "
                "WHERE type(obtained_rel) = 'OBTAINED_FROM' RETURN scene.id AS id",
                node_id=node_id,
            )
            node["obtained_from_scene_id"] = (await provenance.single())["id"]
            return node
        try:
            return await self.graph.execute_write(work)
        except ConstraintError as exc:
            raise HTTPException(status_code=409, detail="CharacterAspect normalized name already exists") from exc

    async def update_aspect(self, node_id: str, payload: CharacterAspectUpdate) -> CharacterAspectRead:
        return CharacterAspectRead.model_validate(await self._update_definition("CharacterAspect", node_id, payload))

    async def update_goal(self, node_id: str, payload: CharacterGoalUpdate) -> CharacterGoalRead:
        return CharacterGoalRead.model_validate(await self._update_definition("CharacterGoal", node_id, payload))

    async def delete_definition(self, label: str, node_id: str) -> None:
        rel = "HAS_ASPECT" if label == "CharacterAspect" else "PURSUES"
        row = await self._one(
            f"MATCH (node:{label} {{id:$node_id}}) "
            f"RETURN count {{ MATCH (:CharacterAgent)-[:{rel}]->(node) }} AS uses, "
            "count { MATCH (:CharacterImpact)-[:AFFECTS]->(node) } AS impacts",
            node_id=node_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        if int(row["uses"] or 0):
            raise HTTPException(status_code=409, detail=f"{label} is still assigned")
        if int(row["impacts"] or 0):
            raise HTTPException(status_code=409, detail=f"{label} is referenced by CharacterImpact")
        await self.graph.run(f"MATCH (node:{label} {{id:$node_id}}) DETACH DELETE node", node_id=node_id)

    async def assign_aspect(self, agent_id: str, payload: CharacterAspectAssignmentCreate) -> CharacterAspectAssignmentRead:
        values = payload.model_dump(mode="json"); aspect_id = values.pop("character_aspect_id"); timestamp = _now()
        values.update(created_at=timestamp, updated_at=timestamp)
        row = await self._one(
            """
            MATCH (agent:CharacterAgent {id:$agent_id}), (aspect:CharacterAspect {id:$aspect_id})
            WHERE agent.ontology_id = aspect.ontology_id
              AND NOT (agent)-[:HAS_ASPECT]->(aspect)
            CREATE (agent)-[rel:HAS_ASPECT]->(aspect) SET rel = $values
            WITH aspect, rel
            OPTIONAL MATCH (aspect)-[obtained_rel]->(scene:Scene)
            WHERE type(obtained_rel) = 'OBTAINED_FROM'
            RETURN aspect, rel, scene.id AS obtained_from_scene_id
            """, agent_id=agent_id, aspect_id=aspect_id, values=values,
        )
        if not row:
            await self._assignment_error(agent_id, aspect_id, "CharacterAspect", "HAS_ASPECT")
        return self._aspect_assignment(row)

    async def _assignment_error(self, agent_id: str, target_id: str, label: str, rel: str) -> None:
        agent = await self._one("MATCH (n:CharacterAgent {id:$id}) RETURN n", id=agent_id)
        target = await self._one(f"MATCH (n:{label} {{id:$id}}) RETURN n", id=target_id)
        if not agent or not target:
            raise HTTPException(status_code=404, detail="CharacterAgent or target not found")
        duplicate = await self._one(f"MATCH (:CharacterAgent {{id:$agent}})-[r:{rel}]->(:{label} {{id:$target}}) RETURN r", agent=agent_id, target=target_id)
        if duplicate:
            raise HTTPException(status_code=409, detail="Relationship already exists")
        raise HTTPException(status_code=400, detail="Cross-ontology connection rejected")

    def _aspect_assignment(self, row) -> CharacterAspectAssignmentRead:
        aspect = _props(row, "aspect"); aspect["obtained_from_scene_id"] = row["obtained_from_scene_id"]
        rel = dict(row["rel"])
        return CharacterAspectAssignmentRead(aspect=CharacterAspectRead.model_validate(aspect), **rel)

    async def list_agent_aspects(
        self, agent_id: str, public_only: bool = False
    ) -> list[CharacterAspectAssignmentRead]:
        if not await self._one(
            "MATCH (n:CharacterAgent {id:$id}) "
            "WHERE NOT $public_only OR coalesce(n.visibility, 'private') = 'public' "
            "RETURN n",
            id=agent_id,
            public_only=public_only,
        ):
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        result = await self.graph.run(
            "MATCH (:CharacterAgent {id:$id})-[rel:HAS_ASPECT]->(aspect:CharacterAspect) "
            "OPTIONAL MATCH (aspect)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN aspect, rel, scene.id AS obtained_from_scene_id ORDER BY rel.created_at DESC, aspect.id ASC", id=agent_id,
        )
        return [self._aspect_assignment(row) async for row in result]

    async def update_assignment(self, agent_id: str, aspect_id: str, payload: CharacterAspectAssignmentUpdate) -> CharacterAspectAssignmentRead:
        changes = payload.model_dump(exclude_unset=True, mode="json"); changes["updated_at"] = _now()
        row = await self._one(
            "MATCH (:CharacterAgent {id:$agent})-[rel:HAS_ASPECT]->(aspect:CharacterAspect {id:$aspect}) "
            "SET rel += $changes WITH aspect, rel "
            "OPTIONAL MATCH (aspect)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN aspect, rel, scene.id AS obtained_from_scene_id",
            agent=agent_id, aspect=aspect_id, changes=changes,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Aspect assignment not found")
        return self._aspect_assignment(row)

    async def pursue_goal(self, agent_id: str, goal_id: str) -> CharacterGoalRead:
        row = await self._one(
            "MATCH (agent:CharacterAgent {id:$agent}), (goal:CharacterGoal {id:$goal}) "
            "WHERE agent.ontology_id=goal.ontology_id AND NOT (agent)-[:PURSUES]->(goal) "
            "// CREATE (agent)-[:PURSUES {created_at:$now}]->(goal) WITH goal OPTIONAL MATCH\n"
            "CREATE (agent)-[:PURSUES {created_at:$now, status:coalesce(goal.status,'active'), "
            "priority:coalesce(goal.priority,50), commitment:coalesce(goal.commitment,50)}]->(goal) "
            "WITH goal "
            "OPTIONAL MATCH (goal)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN goal AS node, scene.id AS obtained_from_scene_id",
            agent=agent_id, goal=goal_id, now=_now(),
        )
        if not row:
            await self._assignment_error(agent_id, goal_id, "CharacterGoal", "PURSUES")
        data = _props(row); data["obtained_from_scene_id"] = row["obtained_from_scene_id"]
        return CharacterGoalRead.model_validate(data)

    async def list_agent_goals(
        self, agent_id: str, public_only: bool = False
    ) -> list[CharacterGoalRead]:
        if not await self._one(
            "MATCH (n:CharacterAgent {id:$id}) "
            "WHERE NOT $public_only OR coalesce(n.visibility, 'private') = 'public' "
            "RETURN n",
            id=agent_id,
            public_only=public_only,
        ):
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        return [CharacterGoalRead.model_validate(x) for x in await self._list_related_goals(agent_id)]

    async def _list_related_goals(self, agent_id: str):
        result = await self.graph.run(
            "MATCH (:CharacterAgent {id:$id})-[pursuit:PURSUES]->(node:CharacterGoal) "
            "OPTIONAL MATCH (node)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN node, scene.id AS obtained_from_scene_id, "
            "coalesce(pursuit.status,node.status) AS pursuit_status, "
            "coalesce(pursuit.priority,node.priority) AS pursuit_priority, "
            "coalesce(pursuit.commitment,node.commitment) AS pursuit_commitment "
            "ORDER BY node.created_at DESC, node.id ASC", id=agent_id,
        )
        rows=[]
        async for row in result:
            data=_props(row)
            data.update(
                status=row["pursuit_status"], priority=row["pursuit_priority"],
                commitment=row["pursuit_commitment"],
                obtained_from_scene_id=row["obtained_from_scene_id"],
            )
            rows.append(data)
        return rows

    async def _require_perspective_owner(
        self, agent_id: str, perspective_id: str, public_only: bool = False
    ) -> dict[str, Any]:
        row = await self._one(
            """
            MATCH (agent:CharacterAgent {id:$agent_id})-[:HAS_PERSPECTIVE]->
                  (perspective:ScenePerspective {id:$perspective_id})
            WHERE NOT $public_only OR coalesce(agent.visibility, 'private') = 'public'
            RETURN perspective AS node
            """,
            agent_id=agent_id,
            perspective_id=perspective_id,
            public_only=public_only,
        )
        if not row:
            raise HTTPException(status_code=404, detail="ScenePerspective not found")
        return _props(row)

    async def create_perspective(
        self, agent_id: str, payload: ScenePerspectiveCreate
    ) -> ScenePerspectiveAggregateRead:
        values = payload.model_dump(mode="json")
        scene_id = values.pop("scene_id")
        perspective_id, timestamp = str(uuid4()), _now()

        async def work(tx):
            scope = await tx.run(
                """
                OPTIONAL MATCH (agent:CharacterAgent {id:$agent_id})-[:EMBODIES]->
                               (entity:EntityInstance)
                OPTIONAL MATCH (scene:Scene {id:$scene_id})
                RETURN agent, entity, scene,
                  CASE WHEN agent IS NULL OR entity IS NULL OR scene IS NULL THEN false
                       ELSE EXISTS {
                         MATCH (scene)-[:DERIVED_FROM|RELATES_TO]->(entity)
                       } OR EXISTS {
                         MATCH (scene)-[:CONTAINS]->(:Milestone)
                               -[:DERIVED_FROM|RELATES_TO]->(entity)
                       }
                  END AS eligible,
                  EXISTS {
                    MATCH (agent)-[:HAS_PERSPECTIVE]->
                          (:ScenePerspective {scene_id:$scene_id})
                  } AS duplicate
                """,
                agent_id=agent_id,
                scene_id=scene_id,
            )
            row = await scope.single()
            if not row or row["agent"] is None:
                raise HTTPException(status_code=404, detail="CharacterAgent not found")
            if row["scene"] is None:
                raise HTTPException(status_code=404, detail="Scene not found")
            agent, entity, scene = dict(row["agent"]), dict(row["entity"]), dict(row["scene"])
            if (
                int(agent.get("ontology_id") or 0) != int(scene.get("ontology_id") or 0)
                or int(entity.get("ontology_id") or 0) != int(scene.get("ontology_id") or 0)
                or str(entity.get("instance_id") or "") != str(scene.get("instance_id") or "")
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Scene and CharacterAgent must share ontology and instance scope",
                )
            if not row["eligible"]:
                raise HTTPException(
                    status_code=400,
                    detail="Scene is not linked to the embodied entity",
                )
            if row["duplicate"]:
                raise HTTPException(
                    status_code=409,
                    detail="CharacterAgent already has a perspective for this Scene",
                )
            props = {
                "id": perspective_id,
                "ontology_id": int(agent["ontology_id"]),
                "character_agent_id": agent_id,
                "scene_id": scene_id,
                **values,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            created = await tx.run(
                """
                MATCH (agent:CharacterAgent {id:$agent_id}), (scene:Scene {id:$scene_id})
                CREATE (perspective:ScenePerspective) SET perspective=$props
                CREATE (agent)-[:HAS_PERSPECTIVE]->(perspective)
                CREATE (perspective)-[:PROJECTS_ON]->(scene)
                RETURN perspective AS node
                """,
                agent_id=agent_id,
                scene_id=scene_id,
                props=props,
            )
            return _props(await created.single())

        try:
            await self.graph.execute_write(work)
        except ConstraintError as exc:
            raise HTTPException(
                status_code=409,
                detail="CharacterAgent already has a perspective for this Scene",
            ) from exc
        return await self.get_perspective(agent_id, perspective_id)

    async def list_perspectives(
        self, agent_id: str, status: str | None, skip: int, limit: int,
        public_only: bool = False,
    ) -> list[ScenePerspectiveRead]:
        if not await self._one(
            "MATCH (agent:CharacterAgent {id:$agent_id}) "
            "WHERE NOT $public_only OR coalesce(agent.visibility, 'private')='public' "
            "RETURN agent",
            agent_id=agent_id,
            public_only=public_only,
        ):
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        result = await self.graph.run(
            """
            MATCH (:CharacterAgent {id:$agent_id})-[:HAS_PERSPECTIVE]->
                  (perspective:ScenePerspective)
            WHERE $status IS NULL OR perspective.status=$status
            RETURN perspective AS node
            ORDER BY perspective.created_at ASC, perspective.id ASC
            SKIP $skip LIMIT $limit
            """,
            agent_id=agent_id,
            status=status,
            skip=skip,
            limit=limit,
        )
        return [
            ScenePerspectiveRead.model_validate(_props(row))
            async for row in result
        ]

    async def get_perspective(
        self, agent_id: str, perspective_id: str, public_only: bool = False
    ) -> ScenePerspectiveAggregateRead:
        perspective = await self._require_perspective_owner(
            agent_id, perspective_id, public_only
        )
        return ScenePerspectiveAggregateRead(
            **perspective,
            emotions=await self.list_perspective_children(
                agent_id, perspective_id, "emotions", public_only
            ),
            beliefs=await self.list_perspective_children(
                agent_id, perspective_id, "beliefs", public_only
            ),
            impacts=await self.list_perspective_children(
                agent_id, perspective_id, "impacts", public_only
            ),
        )

    async def update_perspective(
        self, agent_id: str, perspective_id: str, payload: ScenePerspectiveUpdate
    ) -> ScenePerspectiveAggregateRead:
        changes = payload.model_dump(exclude_unset=True, mode="json")
        changes["updated_at"] = _now()
        row = await self._one(
            """
            MATCH (:CharacterAgent {id:$agent_id})-[:HAS_PERSPECTIVE]->
                  (perspective:ScenePerspective {id:$perspective_id})
            SET perspective += $changes
            RETURN perspective AS node
            """,
            agent_id=agent_id,
            perspective_id=perspective_id,
            changes=changes,
        )
        if not row:
            raise HTTPException(status_code=404, detail="ScenePerspective not found")
        return await self.get_perspective(agent_id, perspective_id)

    async def delete_perspective(self, agent_id: str, perspective_id: str) -> None:
        result = await self.graph.run(
            """
            MATCH (:CharacterAgent {id:$agent_id})-[:HAS_PERSPECTIVE]->
                  (perspective:ScenePerspective {id:$perspective_id})
            OPTIONAL MATCH (perspective)-[:EVOKES|FORMS_BELIEF|HAS_IMPACT]->(child)
            WITH perspective, collect(child) AS children
            FOREACH (child IN children | DETACH DELETE child)
            DETACH DELETE perspective
            RETURN count(*) AS deleted
            """,
            agent_id=agent_id,
            perspective_id=perspective_id,
        )
        row = await result.single()
        if not row or int(row["deleted"] or 0) == 0:
            raise HTTPException(status_code=404, detail="ScenePerspective not found")

    @staticmethod
    def _child_spec(kind: str):
        specs = {
            "emotions": (
                "EmotionalInterpretation", "EVOKES", EmotionalInterpretationRead
            ),
            "beliefs": ("CharacterBelief", "FORMS_BELIEF", CharacterBeliefRead),
            "impacts": ("CharacterImpact", "HAS_IMPACT", CharacterImpactRead),
        }
        if kind not in specs:
            raise ValueError("unknown perspective child kind")
        return specs[kind]

    @staticmethod
    def _child_data(row: Any, kind: str) -> dict[str, Any]:
        data = _props(row)
        if kind == "impacts":
            data["target_id"] = row["target_id"]
            data["target_type"] = row["target_type"]
            data["caused_by_milestone_id"] = row["caused_by_milestone_id"]
        return data

    async def list_perspective_children(
        self, agent_id: str, perspective_id: str, kind: str,
        public_only: bool = False,
    ) -> list[Any]:
        label, rel, model = self._child_spec(kind)
        await self._require_perspective_owner(agent_id, perspective_id, public_only)
        impact_matches = (
            "OPTIONAL MATCH (node)-[:AFFECTS]->(target) "
            "OPTIONAL MATCH (node)-[:CAUSED_BY]->(milestone:Milestone) "
            "RETURN node, target.id AS target_id, "
            "CASE WHEN target:CharacterGoal THEN 'goal' ELSE 'aspect' END AS target_type, "
            "milestone.id AS caused_by_milestone_id "
            if kind == "impacts"
            else "RETURN node, null AS target_id, null AS target_type, "
                 "null AS caused_by_milestone_id "
        )
        result = await self.graph.run(
            f"""
            MATCH (:ScenePerspective {{id:$perspective_id}})-[:{rel}]->(node:{label})
            {impact_matches}
            ORDER BY node.created_at ASC, node.id ASC
            """,
            perspective_id=perspective_id,
        )
        return [
            model.model_validate(self._child_data(row, kind))
            async for row in result
        ]

    async def get_perspective_child(
        self, agent_id: str, perspective_id: str, child_id: str, kind: str,
        public_only: bool = False,
    ) -> Any:
        label, rel, model = self._child_spec(kind)
        await self._require_perspective_owner(agent_id, perspective_id, public_only)
        impact_matches = (
            "OPTIONAL MATCH (node)-[:AFFECTS]->(target) "
            "OPTIONAL MATCH (node)-[:CAUSED_BY]->(milestone:Milestone) "
            "RETURN node, target.id AS target_id, "
            "CASE WHEN target:CharacterGoal THEN 'goal' ELSE 'aspect' END AS target_type, "
            "milestone.id AS caused_by_milestone_id"
            if kind == "impacts"
            else "RETURN node, null AS target_id, null AS target_type, "
                 "null AS caused_by_milestone_id"
        )
        row = await self._one(
            f"""
            MATCH (:ScenePerspective {{id:$perspective_id}})-[:{rel}]->
                  (node:{label} {{id:$child_id}})
            {impact_matches}
            """,
            perspective_id=perspective_id,
            child_id=child_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail=f"{label} not found")
        return model.model_validate(self._child_data(row, kind))

    async def create_perspective_child(
        self, agent_id: str, perspective_id: str, kind: str,
        payload: EmotionalInterpretationCreate | CharacterBeliefCreate,
    ) -> Any:
        label, rel, _ = self._child_spec(kind)
        perspective = await self._require_perspective_owner(agent_id, perspective_id)
        child_id, timestamp = str(uuid4()), _now()
        props = {
            "id": child_id,
            "ontology_id": perspective["ontology_id"],
            **payload.model_dump(mode="json"),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        await self.graph.run(
            f"""
            MATCH (perspective:ScenePerspective {{id:$perspective_id}})
            CREATE (node:{label}) SET node=$props
            CREATE (perspective)-[:{rel}]->(node)
            """,
            perspective_id=perspective_id,
            props=props,
        )
        return await self.get_perspective_child(
            agent_id, perspective_id, child_id, kind
        )

    async def create_impact(
        self, agent_id: str, perspective_id: str, payload: CharacterImpactCreate
    ) -> CharacterImpactRead:
        perspective = await self._require_perspective_owner(agent_id, perspective_id)
        data = payload.model_dump(mode="json")
        target_id = data.pop("target_id")
        milestone_id = data.pop("caused_by_milestone_id")
        target_label = (
            "CharacterGoal" if data["impact_type"] == "goal_change" else "CharacterAspect"
        )
        assignment = "PURSUES" if target_label == "CharacterGoal" else "HAS_ASPECT"
        child_id, timestamp = str(uuid4()), _now()

        async def work(tx):
            found = await tx.run(
                f"""
                MATCH (perspective:ScenePerspective {{id:$perspective_id}})
                OPTIONAL MATCH (:CharacterAgent {{id:$agent_id}})-[:{assignment}]->
                               (target:{target_label} {{id:$target_id}})
                OPTIONAL MATCH (perspective)-[:PROJECTS_ON]->(scene:Scene)
                OPTIONAL MATCH (scene)-[:CONTAINS]->
                               (milestone:Milestone {{id:$milestone_id}})
                RETURN target, milestone
                """,
                perspective_id=perspective_id,
                agent_id=agent_id,
                target_id=target_id,
                milestone_id=milestone_id,
            )
            row = await found.single()
            if not row or row["target"] is None:
                raise HTTPException(
                    status_code=400,
                    detail="Impact target must be assigned to the CharacterAgent",
                )
            if milestone_id is not None and row["milestone"] is None:
                raise HTTPException(
                    status_code=400,
                    detail="Causal milestone must belong to the projected Scene",
                )
            props = {
                "id": child_id,
                "ontology_id": perspective["ontology_id"],
                **data,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            await tx.run(
                f"""
                MATCH (perspective:ScenePerspective {{id:$perspective_id}}),
                      (target:{target_label} {{id:$target_id}})
                CREATE (impact:CharacterImpact) SET impact=$props
                CREATE (perspective)-[:HAS_IMPACT]->(impact)
                CREATE (impact)-[:AFFECTS]->(target)
                WITH impact
                OPTIONAL MATCH (milestone:Milestone {{id:$milestone_id}})
                FOREACH (_ IN CASE WHEN milestone IS NULL THEN [] ELSE [1] END |
                  CREATE (impact)-[:CAUSED_BY]->(milestone))
                """,
                perspective_id=perspective_id,
                target_id=target_id,
                milestone_id=milestone_id,
                props=props,
            )

        await self.graph.execute_write(work)
        return await self.get_perspective_child(
            agent_id, perspective_id, child_id, "impacts"
        )

    async def update_perspective_child(
        self, agent_id: str, perspective_id: str, child_id: str, kind: str,
        payload: EmotionalInterpretationUpdate | CharacterBeliefUpdate |
                 CharacterImpactUpdate,
    ) -> Any:
        label, rel, _ = self._child_spec(kind)
        current = await self.get_perspective_child(
            agent_id, perspective_id, child_id, kind
        )
        changes = payload.model_dump(
            exclude_unset=True, mode="json",
            exclude={"caused_by_milestone_id"},
        )
        if kind == "impacts" and "direction" in changes:
            permitted = (
                {"advanced", "threatened"}
                if current.impact_type.value == "goal_change"
                else {"created", "reinforced", "invalidated"}
            )
            if changes["direction"] not in permitted:
                raise HTTPException(
                    status_code=422,
                    detail="impact direction is incompatible with impact_type",
                )
        milestone_was_set = (
            kind == "impacts"
            and "caused_by_milestone_id" in payload.model_fields_set
        )
        milestone_id = (
            payload.caused_by_milestone_id if milestone_was_set else None
        )
        if milestone_was_set and milestone_id is not None:
            valid = await self._one(
                """
                MATCH (:ScenePerspective {id:$perspective_id})-[:PROJECTS_ON]->
                      (scene:Scene)-[:CONTAINS]->
                      (milestone:Milestone {id:$milestone_id})
                RETURN milestone
                """,
                perspective_id=perspective_id,
                milestone_id=milestone_id,
            )
            if not valid:
                raise HTTPException(
                    status_code=400,
                    detail="Causal milestone must belong to the projected Scene",
                )
        changes["updated_at"] = _now()
        await self.graph.run(
            f"""
            MATCH (:ScenePerspective {{id:$perspective_id}})-[:{rel}]->
                  (node:{label} {{id:$child_id}})
            SET node += $changes
            """,
            perspective_id=perspective_id,
            child_id=child_id,
            changes=changes,
        )
        if milestone_was_set:
            await self.graph.run(
                """
                MATCH (impact:CharacterImpact {id:$child_id})
                OPTIONAL MATCH (impact)-[old:CAUSED_BY]->()
                DELETE old
                WITH impact
                OPTIONAL MATCH (milestone:Milestone {id:$milestone_id})
                FOREACH (_ IN CASE WHEN milestone IS NULL THEN [] ELSE [1] END |
                  CREATE (impact)-[:CAUSED_BY]->(milestone))
                """,
                child_id=child_id,
                milestone_id=milestone_id,
            )
        return await self.get_perspective_child(
            agent_id, perspective_id, child_id, kind
        )

    async def delete_perspective_child(
        self, agent_id: str, perspective_id: str, child_id: str, kind: str
    ) -> None:
        label, rel, _ = self._child_spec(kind)
        result = await self.graph.run(
            f"""
            MATCH (:CharacterAgent {{id:$agent_id}})-[:HAS_PERSPECTIVE]->
                  (:ScenePerspective {{id:$perspective_id}})-[:{rel}]->
                  (node:{label} {{id:$child_id}})
            DETACH DELETE node
            RETURN count(*) AS deleted
            """,
            agent_id=agent_id,
            perspective_id=perspective_id,
            child_id=child_id,
        )
        row = await result.single()
        if not row or int(row["deleted"] or 0) == 0:
            raise HTTPException(status_code=404, detail=f"{label} not found")

    async def unassign(self, agent_id: str, target_id: str, label: str, rel: str) -> None:
        async def work(tx):
            result = await tx.run(
                f"MATCH (:CharacterAgent {{id:$agent}})-[r:{rel}]->(node:{label} {{id:$target}}) DELETE r RETURN node.id AS id",
                agent=agent_id, target=target_id,
            )
            if not await result.single():
                raise HTTPException(status_code=404, detail="Relationship not found")
            remaining = await tx.run(f"MATCH (:CharacterAgent)-[r:{rel}]->(:{label} {{id:$target}}) RETURN count(r) AS count", target=target_id)
            impacted = await tx.run(
                f"MATCH (:CharacterImpact)-[:AFFECTS]->(:{label} {{id:$target}}) "
                "RETURN count(*) AS count",
                target=target_id,
            )
            if (
                int((await remaining.single())["count"] or 0) == 0
                and int((await impacted.single())["count"] or 0) == 0
            ):
                await tx.run(f"MATCH (node:{label} {{id:$target}}) DETACH DELETE node", target=target_id)
        await self.graph.execute_write(work)
