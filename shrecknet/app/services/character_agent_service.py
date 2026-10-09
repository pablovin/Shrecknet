"""Neo4j business rules for ontology-scoped character administration."""

from __future__ import annotations

import re
import json
import asyncio
import logging
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
    CharacterAgentCreate, CharacterAgentCreateRequest, CharacterAgentEmbodimentUpdate, CharacterAgentRead, CharacterAgentUpdate,
    CharacterAspectAssignmentCreate, CharacterAspectAssignmentRead,
    CharacterAspectAssignmentUpdate, CharacterAspectCreate, CharacterAspectRead,
    CharacterAspectUpdate, CharacterGoalCreate, CharacterGoalRead,
    CharacterGoalUpdate, CharacterGoalAssignmentRead, CharacterGoalAssignmentUpdate, CharacterEmbodimentCandidate,
    CharacterEmbodimentCandidatePage,
    CharacterBeliefCreate, CharacterBeliefRead, CharacterBeliefUpdate,
    CharacterImpactCreate, CharacterImpactRead, CharacterImpactUpdate,
    EmotionalInterpretationCreate, EmotionalInterpretationRead,
    EmotionalInterpretationUpdate, ScenePerspectiveAggregateRead,
    ScenePerspectiveCreate, ScenePerspectiveRead, ScenePerspectiveUpdate,
    CharacterIdentityRevisionRead, CharacterIdentityChangeRead,
    CharacterTimelineProjection,
    IdentityDescription,
)


from app.schemas.character_traits import TraitProfile, TraitEvidence, DIRECTIONAL_TRAITS
from app.services.character_trait_service import apply_manual_edits, POLICY_VERSION
from app.graphrag.embedding_service import EmbeddingService
from app.jobs.character_agent.memory import render_memory_document
from app.core.config_store import get_settings

logger = logging.getLogger(__name__)


def _read_profile(value) -> TraitProfile:
    try:
        if isinstance(value, str):
            return TraitProfile.model_validate_json(value)
        return TraitProfile.model_validate(value) if value is not None else TraitProfile()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="CharacterAgent trait profile requires point-based regeneration") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _props(record: Any, key: str = "node") -> dict[str, Any]:
    return dict(record[key])


def _canonical_emotion_valence(value: Any) -> Any:
    """Translate pre-v3 generated signed valence onto the public 0..100 scale."""
    if isinstance(value, int) and value < 0:
        return round((value + 100) / 2)
    return value


def _perspective_props(record: Any, key: str = "node") -> dict[str, Any]:
    """Project legacy graph records onto the current perspective contract."""
    data = _props(record, key)
    if not data.get("perspective"):
        legacy_parts = [data.get("interpretation"), data.get("character_reflection")]
        data["perspective"] = "\n\n".join(
            str(part).strip() for part in legacy_parts if part and str(part).strip()
        ) or str(data.get("summary") or "")
    return {name: value for name, value in data.items() if name in ScenePerspectiveRead.model_fields}


def _agent_data(data: dict[str, Any], *, allow_legacy_profile: bool = False) -> dict[str, Any]:
    """Project a graph node onto the current public CharacterAgent contract.

    CharacterAgent nodes predate the dispositional-traits model and can retain
    retired personality properties.  Reads must remain possible so those nodes
    can be inspected or deleted, but the strict API schema must not expose (or
    accept) properties outside the current contract.
    """
    projected = {
        key: value for key, value in data.items()
        if key in CharacterAgentRead.model_fields
    }
    projected["entity_instance_id"] = data["embodied_entity_instance_id"]
    projected["trait_profile_requires_regeneration"] = False
    try:
        projected["trait_profile"] = _read_profile(data.get("trait_profile"))
    except HTTPException as exc:
        if not allow_legacy_profile or exc.status_code != 409:
            raise
        # Keep legacy nodes inspectable/deletable without translating or
        # rewriting their obsolete trait format.
        projected["trait_profile"] = TraitProfile()
        projected["trait_profile_requires_regeneration"] = True
    identity_description = data.get("identity_description")
    if isinstance(identity_description, str):
        try:
            identity_description = json.loads(identity_description)
        except ValueError:
            identity_description = None
    projected["identity_description"] = (
        IdentityDescription.model_validate(identity_description) if identity_description else None
    )
    projected.setdefault("visibility", "private")
    return projected


class CharacterAgentService:
    def __init__(self, sql_session: SqlAsyncSession, graph_session: AsyncSession) -> None:
        self.sql = sql_session
        self.graph = graph_session

    async def refresh_perspective_memory(self, agent_id: str, perspective_id: str) -> None:
        """Refresh a perspective-only derived vector; failure leaves lexical recall available."""
        try:
            aggregate = await self.get_perspective(agent_id, perspective_id)
            memory = aggregate.model_dump(mode="json")
            document = render_memory_document(memory)
            vector = await asyncio.to_thread(EmbeddingService().embed_text, document)
            await self.graph.run(
                """
                MATCH (:CharacterAgent {id:$agent_id})-[:HAS_PERSPECTIVE]->
                      (perspective:ScenePerspective {id:$perspective_id})
                SET perspective.memory_document=$document,
                    perspective.memory_embedding=$vector,
                    perspective.memory_embedding_model=$model,
                    perspective.memory_embedding_version=$version,
                    perspective.memory_embedded_at=$timestamp
                """,
                agent_id=agent_id, perspective_id=perspective_id, document=document,
                vector=vector, model=EmbeddingService().model_id,
                version=get_settings().semantic_embedding_version,
                timestamp=_now(),
            )
        except Exception:
            logger.warning(
                "character_perspective_embedding_deferred agent_id=%s perspective_id=%s",
                agent_id, perspective_id, exc_info=True,
            )

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

    async def create_agent(self, payload: CharacterAgentCreate | CharacterAgentCreateRequest,
                           user_id: int) -> CharacterAgentRead:
        if not isinstance(payload, CharacterAgentCreateRequest):
            payload = CharacterAgentCreateRequest.model_validate(payload.model_dump(mode="json"))
        return await self._create_agent_aggregate(payload, user_id)

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
            exclude={"entity_instance_id", "embodiment_draft_id", "aspects", "goals", "trait_edits"},
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
        if draft and draft.generated_proposal:
            proposal_data = json.loads(draft.generated_proposal)
            if proposal_data.get("identity_description"):
                agent_props["identity_description"] = json.dumps(
                    proposal_data["identity_description"], ensure_ascii=False,
                )
        aspects = [item.model_dump(mode="json") for item in payload.aspects]
        goals = [item.model_dump(mode="json") for item in payload.goals]
        timeline = (
            CharacterTimelineProjection.model_validate_json(draft.timeline_projection)
            if draft and draft.timeline_projection else None
        )

        generated_profile = timeline.revisions[-1].trait_profile if timeline else TraitProfile()
        # A reviewed unchanged value keeps its generated provenance.
        edits = {key: edit for key, edit in payload.trait_edits.items()
                 if not timeline or edit.point != generated_profile.estimate(key).point}
        final_profile = apply_manual_edits(generated_profile, edits)
        agent_props["trait_profile"] = final_profile.storage_json()

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
            profile_target_ids: dict[str, str] = {}
            for item in aspects:
                normalized = _normalize_name(item["name"])
                aspect_id = str(uuid4())
                definition_props = {
                    "id": aspect_id, "ontology_id": ontology_id, "name": item["name"],
                    "normalized_name": normalized, "category": item["category"],
                    "description": item.get("description"),
                    "created_at": timestamp, "updated_at": timestamp,
                    "generated_by_embodiment_draft_id": draft_id,
                    "evidence_ids": json.dumps(item["evidence_ids"]),
                }
                aspect_result = await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id})
                    MERGE (aspect:CharacterAspect {ontology_id:$ontology_id, normalized_name:$normalized})
                    ON CREATE SET aspect=$props
                    MERGE (agent)-[rel:HAS_ASPECT]->(aspect)
                    ON CREATE SET rel.status=$status, rel.in_focus=$in_focus,
                      rel.name=$name, rel.category=$category, rel.description=$description,
                      rel.created_at=$timestamp, rel.updated_at=$timestamp,
                      rel.evidence_ids=$evidence_ids,
                      rel.justification=$justification
                    RETURN aspect.id AS id
                    """, agent_id=node_id, ontology_id=ontology_id, normalized=normalized,
                    props=definition_props, status=item["status"], in_focus=item["in_focus"],
                    name=item["name"], category=item["category"], description=item.get("description"),
                    timestamp=timestamp,
                    evidence_ids=json.dumps(item["evidence_ids"]),
                    justification=item.get("justification"),
                )
                aspect_row = await aspect_result.single()
                if aspect_row:
                    profile_target_ids[
                        str(item.get("suggestion_id") or item["name"])
                    ] = str(aspect_row["id"])
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
                            "created_at": timestamp,
                            "updated_at": timestamp, "generated_by_embodiment_draft_id": draft_id,
                            "evidence_ids": json.dumps(item["evidence_ids"]),
                        },
                    )
                await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id}), (goal:CharacterGoal {id:$goal_id})
                    MERGE (agent)-[rel:PURSUES]->(goal)
                    ON CREATE SET rel.created_at=$timestamp, rel.updated_at=$timestamp,
                      rel.evidence_ids=$evidence_ids, rel.justification=$justification,
                      rel.status=$status, rel.in_focus=$in_focus,
                      rel.title=$title, rel.description=$description, rel.goal_type=$goal_type
                    """, agent_id=node_id, goal_id=goal_id, timestamp=timestamp,
                    evidence_ids=json.dumps(item["evidence_ids"]),
                    justification=item.get("justification"),
                    status=item["status"], in_focus=item["in_focus"],
                    title=item["title"], description=item["description"], goal_type=item["goal_type"],
                )
                profile_target_ids[
                    str(item.get("suggestion_id") or item["title"])
                ] = goal_id
            if timeline:
                await self._persist_timeline_tx(
                    tx, created_agent, timeline, timestamp,
                    provider=draft.provider if draft else None,
                    model=draft.model if draft else None,
                    prompt_version=draft.prompt_version if draft else None,
                    profile_target_ids=profile_target_ids,
                )
            else:
                initial_revision_id = str(uuid4())
                await self._create_revision_tx(
                    tx, created_agent, initial_revision_id, 0, timestamp,
                    provenance_type="initial",
                    active_aspect_ids=[
                        profile_target_ids[str(item.get("suggestion_id") or item["name"])] for item in aspects
                    ],
                    active_goal_ids=[
                        profile_target_ids[str(item.get("suggestion_id") or item["title"])] for item in goals
                    ],
                )
                if edits:
                    await self._record_manual_traits_tx(tx, node_id, initial_revision_id, 0, timestamp,
                        TraitProfile(), final_profile, edits, user_id)
            if timeline and edits:
                manual_number = timeline.revisions[-1].revision_number + 1
                manual_id = str(uuid4())
                created_agent["trait_profile"] = final_profile.storage_json()
                await tx.run("MATCH (agent:CharacterAgent {id:$id}) SET agent.trait_profile=$profile",
                    id=node_id, profile=created_agent["trait_profile"])
                await self._create_revision_tx(tx, created_agent, manual_id, manual_number, timestamp,
                    provenance_type="manual",
                    active_aspect_ids=[profile_target_ids[str(a.get("suggestion_id") or a["name"])] for a in aspects],
                    active_goal_ids=[profile_target_ids.get(str(g.get("suggestion_id") or g["title"]), "") for g in goals])
                await self._record_manual_traits_tx(tx, node_id, manual_id, manual_number, timestamp,
                    generated_profile, final_profile, edits, user_id)
            return created_agent
        try:
            result = await self.graph.execute_write(work)
        except ConstraintError as exc:
            raise HTTPException(status_code=409, detail="EntityInstance already has a CharacterAgent") from exc
        # Embodiment materializes a complete perspective aggregate in one graph
        # transaction. Refresh its vectors afterwards so a failed encoder cannot
        # roll back an accepted identity; lexical memory documents are already
        # present as a safe fallback.
        if timeline:
            for perspective in await self.list_perspectives(result["id"], 0, 10_000):
                await self.refresh_perspective_memory(result["id"], perspective.id)
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
        return [CharacterAgentRead.model_validate(_agent_data(_props(row, "agent"), allow_legacy_profile=True)) async for row in result]

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
        return CharacterAgentRead.model_validate(_agent_data(_props(row), allow_legacy_profile=True))

    async def load_query_snapshot(self, node_id: str, public_only: bool = False) -> dict[str, Any]:
        """Load the complete active character identity in one graph operation."""
        row = await self._one(
            """
            MATCH (agent:CharacterAgent {id: $node_id})-[:EMBODIES]->(entity:EntityInstance)
            WHERE NOT $public_only OR coalesce(agent.visibility, 'private') = 'public'
            CALL {
              WITH agent
              OPTIONAL MATCH (agent)-[assignment:HAS_ASPECT]->(aspect:CharacterAspect)
              WHERE coalesce(assignment.status, aspect.status, 'active') = 'active'
                AND coalesce(assignment.in_focus, true) = true
              WITH aspect, assignment
              ORDER BY assignment.updated_at DESC, assignment.created_at DESC, aspect.id ASC
              RETURN collect(CASE WHEN aspect IS NULL THEN null ELSE {
                id: aspect.id, name: coalesce(assignment.name, aspect.name),
                category: coalesce(assignment.category, aspect.category),
                description: coalesce(assignment.description, aspect.description),
                status: coalesce(assignment.status, aspect.status, 'active'), in_focus: true
              } END) AS aspects
            }
            CALL {
              WITH agent
              OPTIONAL MATCH (agent)-[pursuit:PURSUES]->(goal:CharacterGoal)
              WHERE coalesce(pursuit.status, goal.status, 'active') = 'active'
                AND coalesce(pursuit.in_focus, true) = true
              WITH goal, pursuit
              ORDER BY pursuit.updated_at DESC, pursuit.created_at DESC, goal.id ASC
              RETURN collect(CASE WHEN goal IS NULL THEN null ELSE {
                id: goal.id, title: coalesce(pursuit.title, goal.title),
                description: coalesce(pursuit.description, goal.description),
                goal_type: coalesce(pursuit.goal_type, goal.goal_type),
                status: coalesce(pursuit.status, goal.status, 'active'),
                in_focus: true
              } END) AS goals
            }
            CALL {
              WITH agent
              OPTIONAL MATCH (agent)-[:HAS_PERSPECTIVE]->(perspective:ScenePerspective)
              WITH perspective
              ORDER BY perspective.updated_at DESC, perspective.id ASC
              RETURN collect(CASE WHEN perspective IS NULL THEN null ELSE {
                id: perspective.id,
                perspective: CASE WHEN perspective.perspective IS NOT NULL
                  THEN perspective.perspective
                  ELSE trim(coalesce(perspective.interpretation, '') + '\n' +
                            coalesce(perspective.character_reflection, '')) END,
                source_type: perspective.source_type,
                memory_document: CASE WHEN perspective.perspective IS NOT NULL
                  THEN perspective.memory_document ELSE null END,
                memory_embedding: CASE WHEN perspective.perspective IS NOT NULL
                  THEN perspective.memory_embedding ELSE null END,
                emotions: [(perspective)-[:EVOKES]->(emotion:EmotionalInterpretation) |
                  {description: emotion.description, arousal: emotion.arousal, valence: emotion.valence}],
                beliefs: [(perspective)-[:FORMS_BELIEF]->(belief:CharacterBelief) |
                  {statement: belief.statement, confidence: belief.confidence}],
                impacts: [(perspective)-[:HAS_IMPACT]->(impact:CharacterImpact)-[:AFFECTS]->(target) |
                  {impact_type: impact.impact_type, direction: impact.direction,
                   description: impact.description, target_name: coalesce(target.title, target.name)}]
              } END) AS memories
            }
            RETURN agent, entity, [item IN aspects WHERE item IS NOT NULL] AS aspects,
                   [item IN goals WHERE item IS NOT NULL] AS goals,
                   [item IN memories WHERE item IS NOT NULL] AS memories
            """,
            node_id=node_id, public_only=public_only,
        )
        if not row:
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        agent = dict(row["agent"])
        if agent.get("status") != "active":
            raise HTTPException(status_code=409, detail="CharacterAgent is not active")
        profile = _read_profile(agent.get("trait_profile"))
        return {
            "character_agent": {
                "name": str(agent.get("name") or row["entity"].get("alias") or "Character"),
                "subtitle": agent.get("subtitle"),
                "background_story": str(agent.get("background_story") or ""),
                "identity_description": _agent_data(agent).get("identity_description").model_dump(mode="json")
                if _agent_data(agent).get("identity_description") else None,
                "trait_profile": profile.model_dump(mode="json"),
            },
            "aspects": [dict(item) for item in row["aspects"]],
            "goals": [dict(item) for item in row["goals"]],
            "memories": [dict(item) for item in row["memories"]],
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

    async def update_agent(self, node_id: str, payload: CharacterAgentUpdate | CharacterAgentEmbodimentUpdate, user_id: int | None = None) -> CharacterAgentRead:
        if isinstance(payload, CharacterAgentEmbodimentUpdate) and payload.embodiment_draft_id:
            return await self._update_agent_from_embodiment(node_id, payload, user_id)
        changes = payload.model_dump(exclude_unset=True, mode="json", exclude={"trait_edits"})
        timestamp = _now()
        changes["updated_at"] = timestamp

        async def work(tx):
            found = await tx.run(
                "MATCH (agent:CharacterAgent {id:$node_id}) "
                "SET agent.updated_at = agent.updated_at "
                "WITH agent OPTIONAL MATCH (agent)-[:HAS_REVISION]->(latest:CharacterIdentityRevision) "
                "WITH agent, latest ORDER BY latest.revision_number DESC LIMIT 1 "
                "RETURN agent, latest",
                node_id=node_id,
            )
            current = await found.single()
            if not current:
                return None
            before = dict(current["agent"])
            previous_profile = _read_profile(before.get("trait_profile"))
            next_profile = apply_manual_edits(previous_profile, payload.trait_edits)
            if payload.trait_edits:
                changes["trait_profile"] = next_profile.storage_json()
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
                active_aspect_ids=json.loads(dict(current["latest"]).get("active_aspect_ids") or "[]") if current["latest"] else [],
                active_goal_ids=json.loads(dict(current["latest"]).get("active_goal_ids") or "[]") if current["latest"] else [],
            )
            if "subtitle" in changes and before.get("subtitle") != after.get("subtitle"):
                await self._create_change_tx(tx, node_id, revision_id, revision_number, timestamp,
                    change_type="subtitle", field_name="subtitle", previous=before.get("subtitle"),
                    new=after.get("subtitle"), provenance_type="manual", actor_user_id=user_id)
            await self._record_manual_traits_tx(tx, node_id, revision_id, revision_number, timestamp,
                previous_profile, next_profile, payload.trait_edits, user_id)
            return row

        row = await self.graph.execute_write(work)
        if not row:
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        return CharacterAgentRead.model_validate(_agent_data(_props(row)))

    async def _update_agent_from_embodiment(
        self,
        node_id: str,
        payload: CharacterAgentEmbodimentUpdate,
        user_id: int | None,
    ) -> CharacterAgentRead:
        draft = await self.sql.get(CharacterEmbodimentDraft, payload.embodiment_draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Embodiment draft not found")
        if user_id is not None and draft.created_by_user_id != user_id:
            raise HTTPException(status_code=404, detail="Embodiment draft not found")
        if draft.status == CharacterEmbodimentDraftStatus.ACCEPTED and draft.target_character_agent_id == node_id:
            return await self.get_agent(node_id)
        if draft.status != CharacterEmbodimentDraftStatus.READY:
            raise HTTPException(status_code=409, detail="Embodiment draft is not ready")
        if draft.target_character_agent_id != node_id:
            raise HTTPException(status_code=400, detail="Embodiment draft does not target this CharacterAgent")

        if not draft.timeline_projection:
            raise HTTPException(status_code=409, detail="Embodiment draft has no review timeline")
        timeline = CharacterTimelineProjection.model_validate_json(draft.timeline_projection)
        if not timeline.revisions:
            raise HTTPException(status_code=409, detail="Embodiment draft has no review timeline")
        generated_revision = timeline.revisions[-1]
        generated_profile = generated_revision.trait_profile
        final_profile = apply_manual_edits(generated_profile, payload.trait_edits)
        aspects = payload.aspects if payload.aspects is not None else []
        goals = payload.goals if payload.goals is not None else []
        known_evidence = set(json.loads(draft.source_evidence_ids or "[]"))
        referenced = {
            evidence_id
            for item in [*aspects, *goals]
            for evidence_id in item.evidence_ids
        }
        if not referenced <= known_evidence:
            raise HTTPException(status_code=422, detail="Update payload references unknown draft evidence")

        changes = payload.model_dump(
            exclude_unset=True,
            mode="json",
            exclude={"trait_edits", "embodiment_draft_id", "aspects", "goals"},
        )
        proposal_data = json.loads(draft.generated_proposal or "{}")
        if proposal_data.get("identity_description"):
            changes["identity_description"] = json.dumps(
                proposal_data["identity_description"], ensure_ascii=False,
            )
        changes["embodiment_draft_id"] = draft.id
        timestamp = _now()
        changes["updated_at"] = timestamp
        aspect_ids: list[str] = []
        goal_ids: list[str] = []

        async def work(tx):
            result = await tx.run(
                "MATCH (agent:CharacterAgent {id:$id})-[:EMBODIES]->(entity:EntityInstance) "
                "RETURN agent, entity.entity_instance_id AS entity_id",
                id=node_id,
            )
            row = await result.single()
            if not row:
                raise HTTPException(status_code=404, detail="CharacterAgent not found")
            agent = _props(row, "agent")
            if (
                str(row["entity_id"]) != draft.source_entity_id
                or int(agent.get("ontology_id") or 0) != draft.ontology_id
            ):
                raise HTTPException(status_code=409, detail="Embodiment draft no longer matches this CharacterAgent")

            await tx.run(
                "MATCH (agent:CharacterAgent {id:$id}) SET agent += $changes",
                id=node_id,
                changes=changes,
            )
            agent.update(changes)
            agent["trait_profile"] = final_profile.storage_json()
            agent["updated_at"] = timestamp

            await self._persist_timeline_tx(
                tx,
                agent,
                timeline,
                timestamp,
                provider=draft.provider,
                model=draft.model,
                prompt_version=draft.prompt_version,
                append=True,
            )
            agent.update(changes)
            agent["trait_profile"] = final_profile.storage_json()
            await tx.run(
                "MATCH (agent:CharacterAgent {id:$id}) "
                "SET agent += $changes, agent.trait_profile=$profile",
                id=node_id, changes=changes, profile=agent["trait_profile"],
            )

            # The generated source history is retained. The reviewed form then
            # becomes the current assignment snapshot in one graph transaction.
            await tx.run(
                "MATCH (agent:CharacterAgent {id:$id})-[rel:HAS_ASPECT]->() "
                "SET rel.in_focus=false, rel.updated_at=$timestamp",
                id=node_id, timestamp=timestamp,
            )
            await tx.run(
                "MATCH (agent:CharacterAgent {id:$id})-[rel:PURSUES]->() "
                "SET rel.in_focus=false, rel.updated_at=$timestamp",
                id=node_id, timestamp=timestamp,
            )
            for item in aspects:
                normalized = _normalize_name(item.name)
                aspect_result = await tx.run(
                    "MATCH (agent:CharacterAgent {id:$agent_id}) "
                    "MERGE (aspect:CharacterAspect {ontology_id:$ontology_id, normalized_name:$normalized}) "
                    "ON CREATE SET aspect.id=$new_id, aspect.name=$name, aspect.category=$category, "
                    "aspect.description=$description, "
                    "aspect.created_at=$timestamp, aspect.updated_at=$timestamp, "
                    "aspect.generated_by_embodiment_draft_id=$draft_id "
                    "MERGE (agent)-[rel:HAS_ASPECT]->(aspect) "
                    "SET rel.status=$status, rel.in_focus=$in_focus, "
                    "rel.updated_at=$timestamp, rel.evidence_ids=$evidence_ids "
                    "RETURN aspect.id AS id",
                    agent_id=node_id, ontology_id=draft.ontology_id, normalized=normalized,
                    new_id=str(uuid4()), name=item.name, category=item.category.value,
                    description=item.description, timestamp=timestamp, draft_id=draft.id,
                    status=item.status.value, in_focus=item.in_focus,
                    evidence_ids=json.dumps(item.evidence_ids),
                )
                aspect_row = await aspect_result.single()
                if aspect_row:
                    aspect_ids.append(str(aspect_row["id"]))
            for item in goals:
                normalized = _normalize_name(item.title)
                goal_lookup = await tx.run(
                    "MATCH (goal:CharacterGoal {ontology_id:$ontology_id}) "
                    "WHERE toLower(trim(goal.title))=$normalized "
                    "RETURN goal.id AS id ORDER BY goal.created_at, goal.id LIMIT 1",
                    ontology_id=draft.ontology_id, normalized=normalized,
                )
                goal_row = await goal_lookup.single()
                goal_id = str(goal_row["id"]) if goal_row else str(uuid4())
                if not goal_row:
                    await tx.run(
                        "CREATE (goal:CharacterGoal) SET goal=$props",
                        props={
                            "id": goal_id, "ontology_id": draft.ontology_id,
                            "title": item.title, "description": item.description,
                            "goal_type": item.goal_type.value,
                            "created_at": timestamp, "updated_at": timestamp,
                            "generated_by_embodiment_draft_id": draft.id,
                        },
                    )
                await tx.run(
                    "MATCH (agent:CharacterAgent {id:$agent_id}), (goal:CharacterGoal {id:$goal_id}) "
                    "MERGE (agent)-[rel:PURSUES]->(goal) "
                    "SET rel.status=$status, rel.in_focus=$in_focus, "
                    "rel.updated_at=$timestamp, rel.evidence_ids=$evidence_ids",
                    agent_id=node_id, goal_id=goal_id, status=item.status.value,
                    in_focus=item.in_focus, timestamp=timestamp,
                    evidence_ids=json.dumps(item.evidence_ids),
                )
                goal_ids.append(goal_id)

            final_revision_number = generated_revision.revision_number + 1
            await tx.run(
                "MATCH (agent:CharacterAgent {id:$id}) "
                "SET agent.trait_profile=$profile, agent.updated_at=$timestamp",
                id=node_id, profile=final_profile.storage_json(), timestamp=timestamp,
            )
            agent["trait_profile"] = final_profile.storage_json()
            revision_id = str(uuid4())
            await self._create_revision_tx(
                tx, agent, revision_id, final_revision_number, timestamp,
                provenance_type="manual", active_aspect_ids=aspect_ids,
                active_goal_ids=goal_ids,
            )
            await self._record_manual_traits_tx(
                tx, node_id, revision_id, final_revision_number, timestamp,
                generated_profile, final_profile, payload.trait_edits, user_id,
            )
            return agent

        updated = await self.graph.execute_write(work)
        draft.status = CharacterEmbodimentDraftStatus.ACCEPTED
        draft.active_entity_key = None
        await self.sql.commit()
        for perspective in await self.list_perspectives(node_id, 0, 10_000):
            await self.refresh_perspective_memory(node_id, perspective.id)
        return CharacterAgentRead.model_validate(_agent_data(updated))

    async def _record_manual_traits_tx(self, tx, agent_id, revision_id, number, timestamp,
                                        before, after, edits, user_id):
        for key, edit in edits.items():
            await self._create_change_tx(tx, agent_id, revision_id, number, timestamp,
                change_type="steadiness" if key == "steadiness" else "trait", field_name=key,
                previous=before.estimate(key).model_dump(mode="json"),
                new=after.estimate(key).model_dump(mode="json"), provenance_type="manual",
                justification=edit.reason, actor_user_id=user_id)

    async def _create_revision_tx(
        self, tx, agent: dict[str, Any], revision_id: str, revision_number: int,
        timestamp: str, *, provenance_type: str, source_group_id: str | None = None,
        last_processed_scene_id: str | None = None, active_aspect_ids: list[str] | None = None,
        active_goal_ids: list[str] | None = None, provider: str | None = None,
        model: str | None = None, prompt_version: str | None = None,
        trait_evidence: list[TraitEvidence] | None = None,
        batch_id: str | None = None, scene_ids: list[str] | None = None,
    ) -> None:
        profile = _read_profile(agent.get("trait_profile"))
        props = {
            "id": revision_id, "character_agent_id": agent["id"],
            "revision_number": revision_number, "source_group_id": source_group_id,
            "last_processed_scene_id": last_processed_scene_id,
            "name": str(agent.get("name") or "Character"),
            "subtitle": agent.get("subtitle"),
            "trait_profile": profile.storage_json(),
            "trait_evidence": json.dumps([
                {**item.model_dump(mode="json"), "revision_id": revision_id}
                for item in (trait_evidence or [])
            ]),
            "batch_id": batch_id, "scene_ids": json.dumps(scene_ids or []),
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
        observation_ids: list[str] | None = None, actor_user_id: int | None = None,
    ) -> None:
        props = {
            "id": str(uuid4()), "character_agent_id": agent_id,
            "revision_number": revision_number, "source_group_id": source_group_id,
            "change_type": change_type, "field_name": field_name,
            "previous_value": json.dumps(previous), "new_value": json.dumps(new),
            "confidence": confidence, "justification": justification,
            "evidence_ids": json.dumps(evidence_ids or []),
            "observation_ids": json.dumps(observation_ids or []),
            "actor_user_id": actor_user_id, "policy_version": POLICY_VERSION,
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
        profile_target_ids: dict[str, str] | None = None,
        append: bool = False,
    ) -> None:
        profile_target_ids = profile_target_ids or {}
        existing_revision_id = None
        if append:
            locked = await tx.run(
                "MATCH (agent:CharacterAgent {id:$id}) SET agent.updated_at=agent.updated_at "
                "WITH agent MATCH (agent)-[:HAS_REVISION]->(r:CharacterIdentityRevision) "
                "RETURN r ORDER BY r.revision_number DESC LIMIT 1", id=agent["id"])
            latest = await locked.single()
            if not latest or latest["r"]["revision_number"] != timeline.revisions[0].revision_number:
                raise HTTPException(status_code=409, detail="Character identity changed; retry from latest revision")
            existing_revision_id = latest["r"]["id"]
        scene_ids = [item.scene_id for projection in timeline.source_projections
                     for item in projection.perspectives]
        if scene_ids:
            scoped = await tx.run(
                "MATCH (:CharacterAgent {id:$id})-[:EMBODIES]->(entity:EntityInstance) "
                "MATCH (scene:Scene) "
                "WHERE scene.id IN $scene_ids "
                "AND coalesce(scene.ontology_id, entity.ontology_id)=entity.ontology_id "
                "AND (EXISTS { MATCH (scene)-[:RELATES_TO]->(entity) } "
                "OR EXISTS { MATCH (scene)-[:CONTAINS]->(:Milestone)-[:RELATES_TO]->(entity) }) "
                "RETURN collect(DISTINCT scene.id) AS scoped_scene_ids",
                id=agent["id"], scene_ids=scene_ids)
            row = await scoped.single()
            if not row or set(row["scoped_scene_ids"]) != set(scene_ids):
                raise HTTPException(status_code=409, detail="Scene scope changed; regenerate embodiment")
        for revision in timeline.revisions:
            for item in revision.active_aspects:
                generated_id = str(item.suggestion_id or item.name)
                if generated_id in profile_target_ids:
                    continue
                normalized = _normalize_name(item.name)
                definition_id = str(uuid4())
                result = await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id})
                    MERGE (aspect:CharacterAspect {
                      ontology_id:$ontology_id, normalized_name:$normalized
                    })
                    ON CREATE SET aspect=$props
                    MERGE (agent)-[rel:HAS_ASPECT]->(aspect)
                    ON CREATE SET rel.status=$status, rel.in_focus=$in_focus,
                      rel.created_at=$timestamp,
                      rel.updated_at=$timestamp, rel.evidence_ids=$evidence_ids,
                      rel.justification=$justification
                    RETURN aspect.id AS id
                    """,
                    agent_id=agent["id"],
                    ontology_id=agent["ontology_id"],
                    normalized=normalized,
                    props={
                        "id": definition_id,
                        "ontology_id": agent["ontology_id"],
                        "name": item.name,
                        "normalized_name": normalized,
                        "category": item.category.value,
                        "description": item.description,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    },
                    status=item.status.value,
                    in_focus=item.in_focus,
                    timestamp=timestamp,
                    evidence_ids=json.dumps(item.evidence_ids),
                    justification=item.justification,
                )
                row = await result.single()
                if row:
                    profile_target_ids[generated_id] = str(row["id"])
            for item in revision.active_goals:
                generated_id = str(item.suggestion_id or item.title)
                if generated_id in profile_target_ids:
                    continue
                normalized = _normalize_name(item.title)
                goal_id = str(uuid4())
                existing = await tx.run(
                    """
                    MATCH (goal:CharacterGoal {ontology_id:$ontology_id})
                    WHERE toLower(trim(goal.title))=$normalized
                    RETURN goal.id AS id ORDER BY goal.created_at, goal.id LIMIT 1
                    """,
                    ontology_id=agent["ontology_id"],
                    normalized=normalized,
                )
                row = await existing.single()
                if row:
                    goal_id = str(row["id"])
                else:
                    await tx.run(
                        "CREATE (goal:CharacterGoal) SET goal=$props",
                        props={
                            "id": goal_id,
                            "ontology_id": agent["ontology_id"],
                            "title": item.title,
                            "description": item.description,
                            "goal_type": item.goal_type.value,
                            "created_at": timestamp,
                            "updated_at": timestamp,
                            "evidence_ids": json.dumps(item.evidence_ids),
                        },
                    )
                await tx.run(
                    """
                    MATCH (agent:CharacterAgent {id:$agent_id}),
                          (goal:CharacterGoal {id:$goal_id})
                    MERGE (agent)-[rel:PURSUES]->(goal)
                    ON CREATE SET rel.created_at=$timestamp,
                      rel.updated_at=$timestamp, rel.evidence_ids=$evidence_ids,
                      rel.justification=$justification,
                      rel.status=$status, rel.in_focus=$in_focus
                    """,
                    agent_id=agent["id"],
                    goal_id=goal_id,
                    timestamp=timestamp,
                    evidence_ids=json.dumps(item.evidence_ids),
                    justification=item.justification,
                    status=item.status.value,
                    in_focus=item.in_focus,
                )
                profile_target_ids[generated_id] = goal_id
        revision_ids: dict[int, str] = {}
        previous = None
        projections = {
            item.resulting_revision.revision_number: item
            for item in timeline.source_projections
        }
        for revision in timeline.revisions:
            if append and revision is timeline.revisions[0]:
                revision_ids[revision.revision_number] = existing_revision_id
                previous = revision
                continue
            revision_id = str(uuid4())
            revision_ids[revision.revision_number] = revision_id
            snapshot = {
                **agent, "name": revision.name, "subtitle": revision.subtitle,
                "trait_profile": revision.trait_profile.storage_json(),
            }
            aspect_ids = [
                profile_target_ids.get(
                    str(item.suggestion_id or item.name),
                    str(item.suggestion_id or item.name),
                )
                for item in revision.active_aspects
                if item.status.value == "active" and item.in_focus
            ]
            goal_ids = [
                profile_target_ids.get(
                    str(item.suggestion_id or item.title),
                    str(item.suggestion_id or item.title),
                )
                for item in revision.active_goals
                if item.status.value == "active" and item.in_focus
            ]
            await self._create_revision_tx(
                tx, snapshot, revision_id, revision.revision_number, timestamp,
                provenance_type="initial" if revision.revision_number == 0 else "generated",
                source_group_id=revision.source_group_id,
                last_processed_scene_id=revision.last_processed_scene_id,
                active_aspect_ids=aspect_ids, active_goal_ids=goal_ids,
                provider=provider, model=model, prompt_version=prompt_version,
                trait_evidence=revision.trait_evidence, batch_id=revision.batch_id, scene_ids=revision.scene_ids,
            )
            projection = projections.get(revision.revision_number)
            if previous and projection:
                for change in projection.trait_changes:
                    await self._create_change_tx(tx, agent["id"], revision_id, revision.revision_number,
                        timestamp, change_type="steadiness" if change.trait == "steadiness" else "trait",
                        field_name=change.trait, previous=change.previous.model_dump(mode="json"),
                        new=change.current.model_dump(mode="json"), provenance_type="generated",
                        source_group_id=revision.source_group_id, justification=change.justification,
                        evidence_ids=change.evidence_ids, observation_ids=change.observation_ids)
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
                for kind, operations, before_items in (
                    ("aspect", projection.aspect_operations, previous.active_aspects),
                    ("goal", projection.goal_operations, previous.active_goals),
                ):
                    for operation in operations:
                        key = operation.target_id or operation.candidate_id
                        key = str(key or "")
                        if not key:
                            continue
                        resolved_id = profile_target_ids.get(key, key)
                        before = next((item for item in before_items
                                       if str(item.suggestion_id or
                                              (item.name if kind == "aspect" else item.title)) in {key, resolved_id}), None)
                        await self._create_change_tx(
                            tx, agent["id"], revision_id, revision.revision_number,
                            timestamp, change_type=kind, field_name=resolved_id,
                            previous=before.model_dump(mode="json") if before else None,
                            new=operation.model_dump(mode="json"),
                            provenance_type="generated",
                            source_group_id=revision.source_group_id,
                            justification=operation.justification,
                            evidence_ids=operation.evidence_ids,
                        )
                for kind, previous_items, current_items, selected, name_field in (
                    ("aspect", previous.active_aspects, revision.active_aspects,
                     projection.focused_aspects, "name"),
                    ("goal", previous.active_goals, revision.active_goals,
                     projection.focused_goals, "title"),
                ):
                    previous_focus = {
                        profile_target_ids.get(str(item.suggestion_id or getattr(item, name_field)),
                                               str(item.suggestion_id or getattr(item, name_field)))
                        for item in previous_items if item.in_focus
                    }
                    current_ids = {
                        profile_target_ids.get(str(item.suggestion_id or getattr(item, name_field)),
                                               str(item.suggestion_id or getattr(item, name_field)))
                        for item in current_items
                    }
                    current_focus = {
                        profile_target_ids.get(item, item) for item in selected
                    }
                    for item_id in sorted(previous_focus ^ current_focus):
                        item = next((entry for entry in current_items
                                     if profile_target_ids.get(str(entry.suggestion_id or getattr(entry, name_field)),
                                                               str(entry.suggestion_id or getattr(entry, name_field))) == item_id), None)
                        await self._create_change_tx(
                            tx, agent["id"], revision_id, revision.revision_number,
                            timestamp, change_type=kind, field_name=item_id,
                            previous={"in_focus": item_id in previous_focus},
                            new={"in_focus": item_id in current_focus},
                            provenance_type="generated", source_group_id=revision.source_group_id,
                            justification="Psychological focus was refreshed for this source.",
                            evidence_ids=[f"scene:{scene_id}" for scene_id in revision.scene_ids],
                        )
            previous = revision

        final_revision = timeline.revisions[-1]
        await tx.run("MATCH (agent:CharacterAgent {id:$id}) SET agent.trait_profile=$profile, agent.subtitle=$subtitle, agent.updated_at=$timestamp",
            id=agent["id"], profile=final_revision.trait_profile.storage_json(), subtitle=final_revision.subtitle, timestamp=timestamp)
        aspect_status = {
            profile_target_ids.get(str(item.suggestion_id or item.name), str(item.suggestion_id or item.name)):
            item.status.value for item in final_revision.active_aspects
        }
        aspect_profile_by_id = {
            profile_target_ids.get(str(item.suggestion_id or item.name), str(item.suggestion_id or item.name)):
                {"name": item.name, "category": item.category.value, "description": item.description}
            for item in final_revision.active_aspects
        }
        aspect_focus = [
            profile_target_ids.get(str(item.suggestion_id or item.name), str(item.suggestion_id or item.name))
            for item in final_revision.active_aspects if item.in_focus
        ]
        goal_status = {
            profile_target_ids.get(str(item.suggestion_id or item.title), str(item.suggestion_id or item.title)):
            item.status.value for item in final_revision.active_goals
        }
        goal_profile_by_id = {
            profile_target_ids.get(str(item.suggestion_id or item.title), str(item.suggestion_id or item.title)):
                {"title": item.title, "goal_type": item.goal_type.value, "description": item.description}
            for item in final_revision.active_goals
        }
        goal_focus = [
            profile_target_ids.get(str(item.suggestion_id or item.title), str(item.suggestion_id or item.title))
            for item in final_revision.active_goals if item.in_focus
        ]
        await tx.run(
            """
            MATCH (agent:CharacterAgent {id:$agent_id})-[rel:HAS_ASPECT]->
                  (aspect:CharacterAspect)
            SET rel += coalesce(($profile_by_id)[aspect.id], {}),
            rel.status = coalesce(($status_by_id)[aspect.id], rel.status, 'active'),
            rel.in_focus = aspect.id IN $focus_ids,
            rel.updated_at=$timestamp
            """,
            agent_id=agent["id"],
            status_by_id=aspect_status,
            profile_by_id=aspect_profile_by_id,
            focus_ids=aspect_focus,
            timestamp=timestamp,
        )
        await tx.run(
            """
            MATCH (agent:CharacterAgent {id:$agent_id})-[rel:PURSUES]->
                  (goal:CharacterGoal)
            SET rel += coalesce(($profile_by_id)[goal.id], {}),
            rel.status = coalesce(($status_by_id)[goal.id], rel.status, 'active'),
            rel.in_focus = goal.id IN $focus_ids,
            rel.updated_at=$timestamp
            """,
            agent_id=agent["id"],
            status_by_id=goal_status,
            profile_by_id=goal_profile_by_id,
            focus_ids=goal_focus,
            timestamp=timestamp,
        )

        for projection in timeline.source_projections:
            starting_revision_id = revision_ids[projection.starting_revision_number]
            for item in projection.perspectives:
                perspective_id = item.id
                memory_document = render_memory_document(item.model_dump(mode="json"))
                props = {
                    "id": perspective_id, "ontology_id": agent["ontology_id"],
                    "character_agent_id": agent["id"], "scene_id": item.scene_id,
                    "generated_with_revision_id": starting_revision_id,
                    "source_group_id": projection.source_group_id,
                    **item.model_dump(
                        mode="json",
                        # ``scene`` and ``evidence`` are hydrated UI display
                        # references retained in the draft timeline. Neo4j node
                        # properties cannot store maps; the graph relationships
                        # below are their persistence representation.
                        exclude={
                            "scene_id", "scene", "evidence", "emotions",
                            "beliefs", "impacts", "evidence_ids",
                        },
                    ),
                    "created_at": timestamp, "updated_at": timestamp,
                    # The vector is populated by the perspective-memory
                    # reconciliation path; the complete deterministic document
                    # provides safe lexical retrieval until then.
                    "memory_document": memory_document,
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
                for emotion in item.emotions:
                    child_props = {
                        "id": str(uuid4()),
                        "ontology_id": agent["ontology_id"],
                        **emotion.model_dump(mode="json"),
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    await tx.run(
                        """
                        MATCH (perspective:ScenePerspective {id:$perspective_id})
                        CREATE (node:EmotionalInterpretation) SET node=$props
                        CREATE (perspective)-[:EVOKES]->(node)
                        """,
                        perspective_id=perspective_id,
                        props=child_props,
                    )
                for belief in item.beliefs:
                    child_props = {
                        "id": str(uuid4()),
                        "ontology_id": agent["ontology_id"],
                        **belief.model_dump(mode="json"),
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    await tx.run(
                        """
                        MATCH (perspective:ScenePerspective {id:$perspective_id})
                        CREATE (node:CharacterBelief) SET node=$props
                        CREATE (perspective)-[:FORMS_BELIEF]->(node)
                        """,
                        perspective_id=perspective_id,
                        props=child_props,
                    )
                for impact in item.impacts:
                    impact_data = impact.model_dump(mode="json")
                    generated_target_id = impact_data.pop("target_id")
                    # ``target`` is a hydrated UI display reference. The
                    # AFFECTS relationship below is its graph representation;
                    # Neo4j properties cannot store the display-reference map.
                    impact_data.pop("target", None)
                    target_id = profile_target_ids.get(
                        generated_target_id, generated_target_id
                    )
                    target_label = (
                        "CharacterGoal"
                        if impact.impact_type.value == "goal_change"
                        else "CharacterAspect"
                    )
                    assignment = (
                        "PURSUES" if target_label == "CharacterGoal" else "HAS_ASPECT"
                    )
                    child_props = {
                        "id": str(uuid4()),
                        "ontology_id": agent["ontology_id"],
                        **impact_data,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    result = await tx.run(
                        f"""
                        MATCH (agent:CharacterAgent {{id:$agent_id}})-[:{assignment}]->
                              (target:{target_label} {{id:$target_id}}),
                              (perspective:ScenePerspective {{id:$perspective_id}})
                        CREATE (node:CharacterImpact) SET node=$props
                        CREATE (perspective)-[:HAS_IMPACT]->(node)
                        CREATE (node)-[:AFFECTS]->(target)
                        RETURN target.id AS target_id
                        """,
                        agent_id=agent["id"],
                        target_id=target_id,
                        perspective_id=perspective_id,
                        props=child_props,
                    )
                    if await result.single() is None:
                        raise ValueError(
                            "perspective impact target is not active on the CharacterAgent"
                        )

    async def list_trait_evidence(self, agent_id: str, *, trait: str | None = None,
                                  revision: int | None = None, skip: int = 0, limit: int = 100):
        await self.get_agent(agent_id)
        rows = await self.graph.run(
            "MATCH (:CharacterAgent {id:$id})-[:HAS_REVISION]->(r:CharacterIdentityRevision) "
            "WHERE $revision IS NULL OR r.revision_number <= $revision "
            "RETURN r.trait_evidence AS evidence ORDER BY r.revision_number", id=agent_id, revision=revision)
        items = [TraitEvidence.model_validate(item) async for row in rows
                 for item in json.loads(row["evidence"] or "[]") if trait is None or item["trait"] == trait]
        return items[skip:skip + limit]

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
        values = [CharacterIdentityRevisionRead.model_validate({k:v for k,v in _props(row).items() if k != "trait_evidence"}) async for row in result]
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
        if values["status"] == "active" and values["in_focus"]:
            count = await self._one(
                "MATCH (:CharacterAgent {id:$agent})-[r:HAS_ASPECT]->(:CharacterAspect) "
                "WHERE coalesce(r.status,'active')='active' AND coalesce(r.in_focus,false)=true "
                "RETURN count(r) AS count", agent=agent_id,
            )
            if int(count["count"] or 0) >= 10:
                raise HTTPException(status_code=422, detail="At most ten aspects may be in focus")
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
        rel_data = dict(row["rel"])
        for key in ("name", "category", "description"):
            if rel_data.get(key) is not None:
                aspect[key] = rel_data[key]
        rel = {key: value for key, value in rel_data.items()
               if key in {"status", "in_focus", "justification", "evidence_ids", "created_at", "updated_at"}}
        rel.setdefault("status", "active")
        rel.setdefault("in_focus", True)
        rel.setdefault("created_at", aspect.get("created_at"))
        rel.setdefault("updated_at", aspect.get("updated_at"))
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
        if changes.get("in_focus") is True and changes.get("status") in (None, "active"):
            count = await self._one(
                "MATCH (:CharacterAgent {id:$agent})-[r:HAS_ASPECT]->(:CharacterAspect) "
                "WHERE coalesce(r.status,'active')='active' AND coalesce(r.in_focus,false)=true "
                "RETURN count(r) AS count", agent=agent_id,
            )
            current = await self._one(
                "MATCH (:CharacterAgent {id:$agent})-[r:HAS_ASPECT]->(:CharacterAspect {id:$aspect}) "
                "RETURN r.in_focus AS in_focus", agent=agent_id, aspect=aspect_id,
            )
            if int(count["count"] or 0) >= 10 and not (current and current["in_focus"]):
                raise HTTPException(status_code=422, detail="At most ten aspects may be in focus")
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

    async def pursue_goal(self, agent_id: str, goal_id: str) -> CharacterGoalAssignmentRead:
        count = await self._one(
            "MATCH (:CharacterAgent {id:$agent})-[r:PURSUES]->(:CharacterGoal) "
            "WHERE coalesce(r.status,'active')='active' AND coalesce(r.in_focus,false)=true "
            "RETURN count(r) AS count", agent=agent_id,
        )
        if int(count["count"] or 0) >= 10:
            raise HTTPException(status_code=422, detail="At most ten goals may be in focus")
        row = await self._one(
            "MATCH (agent:CharacterAgent {id:$agent}), (goal:CharacterGoal {id:$goal}) "
            "WHERE agent.ontology_id=goal.ontology_id AND NOT (agent)-[:PURSUES]->(goal) "
            "// CREATE (agent)-[:PURSUES {created_at:$now}]->(goal) WITH goal OPTIONAL MATCH\n"
            "CREATE (agent)-[rel:PURSUES {created_at:$now, updated_at:$now, status:'active', in_focus:true}]->(goal) "
            "WITH goal "
            "OPTIONAL MATCH (goal)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN goal AS node, rel AS rel, scene.id AS obtained_from_scene_id",
            agent=agent_id, goal=goal_id, now=_now(),
        )
        if not row:
            await self._assignment_error(agent_id, goal_id, "CharacterGoal", "PURSUES")
        data = _props(row); data["obtained_from_scene_id"] = row["obtained_from_scene_id"]
        return CharacterGoalAssignmentRead(
            goal=CharacterGoalRead.model_validate(data),
            status=row["rel"].get("status", "active"),
            in_focus=row["rel"].get("in_focus", True),
            justification=row["rel"].get("justification"),
            evidence_ids=row["rel"].get("evidence_ids") or [],
            created_at=row["rel"].get("created_at"),
            updated_at=row["rel"].get("updated_at"),
        )

    async def list_agent_goals(
        self, agent_id: str, public_only: bool = False
    ) -> list[CharacterGoalAssignmentRead]:
        if not await self._one(
            "MATCH (n:CharacterAgent {id:$id}) "
            "WHERE NOT $public_only OR coalesce(n.visibility, 'private') = 'public' "
            "RETURN n",
            id=agent_id,
            public_only=public_only,
        ):
            raise HTTPException(status_code=404, detail="CharacterAgent not found")
        return [CharacterGoalAssignmentRead.model_validate(x) for x in await self._list_related_goals(agent_id)]

    async def update_goal_assignment(
        self, agent_id: str, goal_id: str, payload: CharacterGoalAssignmentUpdate,
    ) -> CharacterGoalAssignmentRead:
        changes = payload.model_dump(exclude_unset=True, mode="json")
        if changes.get("in_focus") is True and changes.get("status") in (None, "active"):
            count = await self._one(
                "MATCH (:CharacterAgent {id:$agent})-[r:PURSUES]->(:CharacterGoal) "
                "WHERE coalesce(r.status,'active')='active' AND coalesce(r.in_focus,false)=true "
                "RETURN count(r) AS count", agent=agent_id,
            )
            current = await self._one(
                "MATCH (:CharacterAgent {id:$agent})-[r:PURSUES]->(:CharacterGoal {id:$goal}) "
                "RETURN r.in_focus AS in_focus", agent=agent_id, goal=goal_id,
            )
            if int(count["count"] or 0) >= 10 and not (current and current["in_focus"]):
                raise HTTPException(status_code=422, detail="At most ten goals may be in focus")
        changes["updated_at"] = _now()
        result = await self.graph.run(
            "MATCH (:CharacterAgent {id:$agent})-[r:PURSUES]->(goal:CharacterGoal {id:$goal}) "
            "SET r += $changes WITH goal, r "
            "OPTIONAL MATCH (goal)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel)='OBTAINED_FROM' "
            "RETURN goal AS node, r AS rel, scene.id AS obtained_from_scene_id",
            agent=agent_id, goal=goal_id, changes=changes,
        )
        row = await result.single()
        if not row:
            raise HTTPException(status_code=404, detail="Goal pursuit not found")
        data = _props(row, "node"); data["obtained_from_scene_id"] = row["obtained_from_scene_id"]
        rel = dict(row["rel"])
        return CharacterGoalAssignmentRead(
            goal=CharacterGoalRead.model_validate(data), status=rel.get("status", "active"),
            in_focus=rel.get("in_focus", False), justification=rel.get("justification"),
            evidence_ids=rel.get("evidence_ids") or [], created_at=rel.get("created_at"),
            updated_at=rel.get("updated_at"),
        )

    async def _list_related_goals(self, agent_id: str):
        result = await self.graph.run(
            "MATCH (:CharacterAgent {id:$id})-[pursuit:PURSUES]->(node:CharacterGoal) "
            "OPTIONAL MATCH (node)-[obtained_rel]->(scene:Scene) "
            "WHERE type(obtained_rel) = 'OBTAINED_FROM' "
            "RETURN node, pursuit.title AS title, pursuit.description AS description, "
            "pursuit.goal_type AS goal_type, scene.id AS obtained_from_scene_id, "
            "coalesce(pursuit.status,node.status,'active') AS pursuit_status, "
            "coalesce(pursuit.in_focus,true) AS in_focus, pursuit.justification AS justification, "
            "pursuit.evidence_ids AS evidence_ids, pursuit.created_at AS created_at, "
            "pursuit.updated_at AS updated_at "
            "ORDER BY node.created_at DESC, node.id ASC", id=agent_id,
        )
        rows=[]
        async for row in result:
            data=_props(row, "node")
            for key in ("title", "description", "goal_type"):
                if row.get(key) is not None:
                    data[key] = row[key]
            data.update(obtained_from_scene_id=row["obtained_from_scene_id"])
            rows.append({"goal": data, "status": row["pursuit_status"],
                         "in_focus": row["in_focus"], "justification": row["justification"],
                         "evidence_ids": row["evidence_ids"] or [],
                         "created_at": row["created_at"], "updated_at": row["updated_at"]})
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
        return _perspective_props(row)

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
        await self.refresh_perspective_memory(agent_id, perspective_id)
        return await self.get_perspective(agent_id, perspective_id)

    async def list_perspectives(
        self, agent_id: str, skip: int, limit: int,
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
            RETURN perspective AS node
            ORDER BY perspective.created_at ASC, perspective.id ASC
            SKIP $skip LIMIT $limit
            """,
            agent_id=agent_id,
            skip=skip,
            limit=limit,
        )
        return [
            ScenePerspectiveRead.model_validate(_perspective_props(row))
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
        await self.refresh_perspective_memory(agent_id, perspective_id)
        return await self.get_perspective(agent_id, perspective_id)

    async def delete_perspective(self, agent_id: str, perspective_id: str) -> None:
        revisions = await self.graph.run(
            "MATCH (:CharacterAgent {id:$agent_id})-[:HAS_REVISION]->(r:CharacterIdentityRevision) "
            "RETURN r.trait_evidence AS evidence",
            agent_id=agent_id,
        )
        async for revision in revisions:
            if any(item.get("perspective_id") == perspective_id
                   for item in json.loads(revision["evidence"] or "[]")):
                raise HTTPException(status_code=409, detail="Regenerate trait evidence before deleting this perspective")
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
        if kind == "emotions":
            data["valence"] = _canonical_emotion_valence(data.get("valence"))
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
        await self.refresh_perspective_memory(agent_id, perspective_id)
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
        await self.refresh_perspective_memory(agent_id, perspective_id)
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
        await self.refresh_perspective_memory(agent_id, perspective_id)
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
        await self.refresh_perspective_memory(agent_id, perspective_id)

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
