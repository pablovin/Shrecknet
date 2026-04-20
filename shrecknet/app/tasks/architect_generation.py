"""Background task for Architect step 2 generation from reviewed frontend payload."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.entity_generator import EntityGenerator
from app.models.architect import ArchitectProposalStatus, ArchitectProposalType
from app.models.background_job import AuthorType, JobType
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.ontology_instance import (
    MilestoneCreate,
    MilestoneDerivedFrom,
    MilestoneEntityRelation,
    MilestoneLocalOrder,
    SceneCreate,
    SceneDerivedFrom,
    SceneLocalOrder,
)
from app.services.ontology_instance_service import OntologyInstanceService
from app.tasks.neo4j_embedding import embed_instance as embed_instance_task
from app.tasks.neo4j_embedding import embed_nodes as embed_nodes_task
from app.tasks.ontology_links import link_instance as link_instance_task
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="architect.generate_entities")
def generate_entities(
    run_id: str,
    reviewed_pipeline_output: dict[str, Any],
    *,
    author_type: str = "agent",
    author_id: str = "system",
) -> dict[str, Any]:
    """Generate entities/scenes/milestones and perform enrichment updates."""

    description = f"Architect generation from reviewed payload for run {run_id}"
    job_id = run_async(
        create_background_job(
            author_type=AuthorType.AGENT,
            author_id=author_id,
            job_type=JobType.ARCHITECT_GENERATION,
            description=description,
            celery_task_id=generate_entities.request.id,
            details={"run_id": run_id},
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(_attach_generation_job_to_run(run_id, job_id))
        result = run_async(
            _execute_generation(
                run_id=run_id,
                reviewed_pipeline_output=reviewed_pipeline_output,
                job_id=job_id,
                author_id=author_id,
                author_type=author_type,
            )
        )
        run_async(mark_job_done(job_id, result))
        return {"status": "success", "job_id": job_id, **result}
    except Exception as exc:
        logger.error("architect generation failed for run %s: %s", run_id, exc, exc_info=True)
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _attach_generation_job_to_run(run_id: str, job_id: int) -> None:
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        await repo.attach_generation_job(run_id, job_id)
        await session.commit()


async def _execute_generation(
    *,
    run_id: str,
    reviewed_pipeline_output: dict[str, Any],
    job_id: int,
    author_id: str,
    author_type: str,
) -> dict[str, Any]:
    settings = get_settings()
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI API key not configured")

    outputs = reviewed_pipeline_output.get("outputs") or {}
    entity_proposals = outputs.get("entity_proposals") or []
    scene_proposals = outputs.get("scene_proposals") or []
    milestones_per_scene = outputs.get("milestones_per_scene") or []

    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        run = await repo.get_run(run_id, with_proposals=True)
        if not run:
            raise ValueError("Architect run not found")

        await update_job_progress(job_id, 0.05, {"status": "Loading ontology and instance context"})

        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as graph_session:
            service = OntologyInstanceService(session, graph_session)
            onto_repo = OntologyRepository(session)
            ontology_id = run.ontology_id
            if not ontology_id:
                raise ValueError("Run ontology id is missing")

            instance = await service.get_instance(run.ontology_instance_id)
            existing_entities_map = {
                entity.entity_instance_id: {
                    "alias": entity.alias,
                    "definition_id": entity.definition_id,
                    "properties": [p.model_dump() for p in entity.properties],
                    "relationships": [r.model_dump() for r in entity.relationships],
                    "text": entity.text,
                    "autogenerated_text": entity.autogenerated_text,
                }
                for entity in instance.entities
            }
            alias_to_entity_id = {
                _norm(entity.alias): entity.entity_instance_id
                for entity in instance.entities
                if entity.alias
            }

            entity_defs = await onto_repo.list_entities(ontology_id)
            entity_definitions_map = _build_entity_definitions_map(entity_defs)
            by_name = {
                _norm(data["name"]): definition_id
                for definition_id, data in entity_definitions_map.items()
            }

            created_entity_ids: list[str] = []
            updated_entity_ids: list[str] = []
            approved_entities = [item for item in entity_proposals if _is_approved(item.get("status"))]

            await update_job_progress(job_id, 0.18, {"status": "Step 1/4: inserting approved entities"})

            proposal_to_entity_id: dict[int, str] = {}
            proposal_scene_refs: dict[str, list[str]] = {}
            update_targets: set[str] = set()

            for idx, proposal in enumerate(approved_entities):
                alias = (
                    ((proposal.get("updates") or {}).get("name"))
                    or proposal.get("name")
                    or "Unnamed"
                ).strip()
                if not alias:
                    continue

                scene_refs = [str(ref) for ref in (proposal.get("scene_refs") or [])]
                proposal_scene_refs[_norm(alias)] = scene_refs
                proposal_scene_refs[_norm(proposal.get("canonical") or "")] = scene_refs

                explicit_id = ((proposal.get("updates") or {}).get("corrected_entity_instance_id") or proposal.get("entity_instance_id"))
                if explicit_id:
                    update_targets.add(explicit_id)
                    proposal_to_entity_id[idx] = explicit_id
                    alias_to_entity_id[_norm(alias)] = explicit_id
                    continue

                ontology_name = ((proposal.get("updates") or {}).get("ontology") or proposal.get("ontology") or "").strip()
                definition_id = (proposal.get("updates") or {}).get("corrected_entity_definition_id")
                if definition_id is None and ontology_name:
                    definition_id = by_name.get(_norm(ontology_name))
                if definition_id is None:
                    logger.warning("Skipping proposal '%s' without resolvable definition", alias)
                    continue

                new_entity_id = str(uuid4())
                now = datetime.utcnow().isoformat() + "Z"
                await graph_session.run(
                    """
                    MATCH (i:OntologyInstance {instance_id: $instance_id})
                    CREATE (i)-[:HAS_ENTITY]->(e:EntityInstance {
                        entity_instance_id: $entity_instance_id,
                        instance_id: $instance_id,
                        ontology_id: $ontology_id,
                        entity_definition_id: $entity_definition_id,
                        properties: $properties,
                        text: $text,
                        text_linked: $text,
                        autogenerated_text: $autogenerated_text,
                        autogenerated_text_linked: $autogenerated_text,
                        created_date: $now,
                        last_updated_date: $now,
                        author_type: $author_type,
                        author_id: $author_id,
                        created_at: $now,
                        updated_at: $now,
                        alias: $alias,
                        is_embedded: false,
                        last_embedded_date: null
                    })
                    """,
                    instance_id=run.ontology_instance_id,
                    ontology_id=ontology_id,
                    entity_instance_id=new_entity_id,
                    entity_definition_id=int(definition_id),
                    properties=json.dumps({}),
                    text="",
                    autogenerated_text=(proposal.get("why") or "").strip(),
                    now=now,
                    author_type="agent",
                    author_id=author_id,
                    alias=alias,
                )
                created_entity_ids.append(new_entity_id)
                proposal_to_entity_id[idx] = new_entity_id
                alias_to_entity_id[_norm(alias)] = new_entity_id
                canonical = proposal.get("canonical")
                if canonical:
                    alias_to_entity_id[_norm(canonical)] = new_entity_id

            impacted_entity_ids: set[str] = set(created_entity_ids) | set(update_targets)

            await update_job_progress(job_id, 0.36, {"status": "Step 2/4: inserting approved scenes"})

            approved_scenes = [item for item in scene_proposals if _is_approved(item.get("status"))]
            approved_scenes.sort(key=lambda s: int(s.get("scene_order") or 0))
            scene_ref_to_scene_id: dict[str, str] = {}
            scene_ref_to_entities: dict[str, set[str]] = {}
            previous_scene_id: str | None = None
            created_scenes = 0

            existing_entity_ids = set(existing_entities_map.keys()) | set(created_entity_ids)
            default_source_entity_id = next(iter(existing_entity_ids), None)
            if default_source_entity_id is None:
                raise ValueError("No entity available to anchor derived_from for scenes")

            for scene in approved_scenes:
                source_entity_id = scene.get("source_entity_instance_id") or default_source_entity_id
                if source_entity_id not in existing_entity_ids:
                    source_entity_id = default_source_entity_id

                scene_ref = str(scene.get("scene_ref") or "")
                scene_id = str(scene.get("scene_id") or uuid4())
                scene_ref_to_scene_id[scene_ref] = scene_id

                related_entity_ids: set[str] = set()
                for related in scene.get("related_to") or []:
                    related_id = related.get("entity_instance_id")
                    if not related_id:
                        candidate_aliases = [related.get("alias"), related.get("canonical")]
                        related_id = _resolve_alias(candidate_aliases, alias_to_entity_id)
                    if not related_id:
                        raise ValueError(
                            f"Unresolvable related_to alias in scene {scene_ref}: {related.get('alias') or related.get('canonical')}"
                        )
                    related_entity_ids.add(related_id)
                scene_ref_to_entities[scene_ref] = related_entity_ids
                impacted_entity_ids |= related_entity_ids

                payload = SceneCreate(
                    id=scene_id,
                    name=(scene.get("scene_name") or "Scene").strip(),
                    description=(scene.get("scene_description") or scene.get("scene_text") or "").strip()[:2000],
                    created_by_type="agent",
                    created_by_author=author_id,
                    derived_from=SceneDerivedFrom(entity_instance_id=source_entity_id),
                    local_order=SceneLocalOrder(preceded_by_scene_id=previous_scene_id),
                    milestones=[],
                )
                await service.create_scene(run.ontology_instance_id, payload)
                previous_scene_id = scene_id
                created_scenes += 1

            await update_job_progress(job_id, 0.54, {"status": "Step 3/4: inserting approved milestones"})

            created_milestones = 0
            for scene_bundle in milestones_per_scene:
                scene_ref = scene_bundle.get("scene_ref")
                if scene_ref not in scene_ref_to_scene_id:
                    continue
                scene_id = scene_ref_to_scene_id[scene_ref]
                milestones = scene_bundle.get("milestones") or []
                milestones = [m for m in milestones if _is_approved(m.get("status", "approved"))]
                milestones.sort(key=lambda m: int(m.get("milestone_order") or 0))
                prev_milestone_id: str | None = None

                for milestone in milestones:
                    milestone_id = str(milestone.get("milestone_ref") or uuid4())
                    source_entity_id = _pick_scene_source_entity(
                        scene_ref=scene_ref,
                        scene_proposals=approved_scenes,
                        fallback=default_source_entity_id,
                    )
                    relates = []
                    allowed_entities = scene_ref_to_entities.get(scene_ref, set())
                    for rel in milestone.get("related_to") or []:
                        target_id = _resolve_alias([rel.get("entity")], alias_to_entity_id)
                        if not target_id:
                            raise ValueError(
                                f"Unresolvable milestone related entity '{rel.get('entity')}' in scene {scene_ref}"
                            )
                        if target_id not in allowed_entities:
                            continue
                        relates.append(
                            MilestoneEntityRelation(
                                entity_instance_id=target_id,
                                label=_normalize_label(rel.get("relationship_label") or "related_to"),
                            )
                        )
                        impacted_entity_ids.add(target_id)

                    payload = MilestoneCreate(
                        id=milestone_id,
                        name=(milestone.get("title") or milestone.get("label") or "Milestone").strip(),
                        description=(milestone.get("description") or "").strip(),
                        created_by_type="agent",
                        created_by_author=author_id,
                        boundary_type=milestone.get("boundary_type") or "none",
                        local_order=MilestoneLocalOrder(preceded_by_milestone_id=prev_milestone_id),
                        derived_from=MilestoneDerivedFrom(entity_instance_id=source_entity_id),
                        relates_to=relates,
                    )
                    await service.create_milestone(run.ontology_instance_id, scene_id, payload)
                    prev_milestone_id = milestone_id
                    created_milestones += 1

            await update_job_progress(job_id, 0.74, {"status": "Step 4/4: enriching and updating entities"})

            refreshed_instance = await service.get_instance(run.ontology_instance_id)
            current_entities_map = {
                entity.entity_instance_id: {
                    "alias": entity.alias,
                    "definition_id": entity.definition_id,
                    "properties": [p.model_dump() for p in entity.properties],
                    "relationships": [r.model_dump() for r in entity.relationships],
                    "text": entity.text,
                    "autogenerated_text": entity.autogenerated_text,
                }
                for entity in refreshed_instance.entities
            }

            original_text = "\n\n".join(
                [
                    t
                    for entity in refreshed_instance.entities
                    for t in [entity.text, entity.autogenerated_text]
                    if t
                ]
            )

            model_policy = ModelPolicy(
                decompose_model=settings.model_decompose,
                subanswer_model=settings.model_subanswer,
                synthesis_model=settings.model_synthesis,
                validation_model=settings.model_validation,
                style_model=settings.model_style,
                architect_extract_model=getattr(
                    settings, "model_architect_extract", settings.model_decompose
                ),
            )
            llm_client = OpenAIClient(
                api_key=settings.openai_api_key,
                timeout=60,
                max_retries=3,
            )
            generator = EntityGenerator(llm_client, model_policy)
            try:
                await _apply_enrichment_updates(
                    graph_session=graph_session,
                    generator=generator,
                    target_entity_ids=sorted(impacted_entity_ids),
                    entity_definitions_map=entity_definitions_map,
                    existing_entities_map=current_entities_map,
                    alias_to_entity_id=alias_to_entity_id,
                    scene_proposals=approved_scenes,
                    scene_ref_to_entities=scene_ref_to_entities,
                    proposal_scene_refs=proposal_scene_refs,
                    original_text=original_text,
                    author_id=author_id,
                )
            finally:
                await llm_client.aclose()

            await update_job_progress(job_id, 0.9, {"status": "Triggering linking and embedding jobs"})

            if impacted_entity_ids:
                link_instance_task.delay(run.ontology_instance_id, author_type="agent", author_id=author_id)
                embed_nodes_task.delay(ontology_id, sorted(impacted_entity_ids), author_type="agent", author_id=author_id)
                embed_instance_task.delay(run.ontology_instance_id, author_type="agent", author_id=author_id)

            await _sync_entity_proposal_states(
                repo=repo,
                proposals=run.proposals,
                frontend_entities=entity_proposals,
                proposal_to_entity_id=proposal_to_entity_id,
            )
            await session.commit()

            return {
                "run_id": run_id,
                "created_entities": len(created_entity_ids),
                "updated_entities": len(update_targets),
                "created_scenes": created_scenes,
                "created_milestones": created_milestones,
                "impacted_entities": len(impacted_entity_ids),
            }


async def _apply_enrichment_updates(
    *,
    graph_session: Any,
    generator: EntityGenerator,
    target_entity_ids: list[str],
    entity_definitions_map: dict[int, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
    alias_to_entity_id: dict[str, str],
    scene_proposals: list[dict[str, Any]],
    scene_ref_to_entities: dict[str, set[str]],
    proposal_scene_refs: dict[str, list[str]],
    original_text: str,
    author_id: str,
) -> None:
    for entity_id in target_entity_ids:
        entity_data = existing_entities_map.get(entity_id)
        if not entity_data:
            continue

        alias = entity_data.get("alias") or ""
        scene_refs = set(proposal_scene_refs.get(_norm(alias), []))
        for scene in scene_proposals:
            related = scene.get("related_to") or []
            for rel in related:
                rel_id = rel.get("entity_instance_id") or _resolve_alias([rel.get("alias"), rel.get("canonical")], alias_to_entity_id)
                if rel_id == entity_id:
                    scene_refs.add(str(scene.get("scene_ref") or ""))

        chunks: list[str] = []
        allowed_targets: set[str] = set()
        for scene in scene_proposals:
            scene_ref = str(scene.get("scene_ref") or "")
            if scene_ref in scene_refs:
                text = (scene.get("scene_text") or "").strip()
                if text:
                    chunks.append(text)
                allowed_targets |= scene_ref_to_entities.get(scene_ref, set())

        if not chunks:
            continue

        definition_id = entity_data.get("definition_id")
        entity_def = entity_definitions_map.get(definition_id)
        if not entity_def:
            continue

        extracted = await generator._extract_properties_and_relationships(
            entity_definition_id=definition_id,
            entity_alias=alias,
            entity_type_name=entity_def.get("name") or "Entity",
            properties_catalog=entity_def.get("properties") or [],
            relationships_catalog=entity_def.get("relationships") or [],
            chunks=chunks,
            original_text=original_text,
            is_update=True,
            existing_text=entity_data.get("text") or "",
            existing_autogenerated_text=entity_data.get("autogenerated_text") or "",
            existing_properties=entity_data.get("properties") or [],
            existing_relationships=entity_data.get("relationships") or [],
        )

        existing_summary = (entity_data.get("autogenerated_text") or "").strip()
        candidate_summary = (extracted.updated_autogenerated_summary or "").strip()
        if candidate_summary and not _summary_contains(existing_summary, candidate_summary):
            merged_summary = _merge_summaries(existing_summary, candidate_summary)
            await graph_session.run(
                """
                MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                SET e.autogenerated_text = $summary,
                    e.autogenerated_text_linked = $summary,
                    e.last_updated_date = datetime(),
                    e.updated_at = datetime(),
                    e.author_type = 'agent',
                    e.author_id = $author_id
                """,
                entity_id=entity_id,
                summary=merged_summary,
                author_id=author_id,
            )

        # Update properties only when evidence indicates a change/new value.
        result = await graph_session.run(
            """
            MATCH (e:EntityInstance {entity_instance_id: $entity_id})
            RETURN e.properties AS properties
            """,
            entity_id=entity_id,
        )
        record = await result.single()
        prop_map: dict[str, Any] = {}
        if record and record.get("properties"):
            raw_props = record.get("properties")
            if isinstance(raw_props, str):
                try:
                    prop_map = json.loads(raw_props)
                except Exception:
                    prop_map = {}

        properties_changed = False
        for prop in extracted.new_properties:
            key = str(prop.definition_id)
            old_value = prop_map.get(key)
            if old_value is None or old_value != prop.value:
                prop_map[key] = prop.value
                properties_changed = True

        if properties_changed:
            await graph_session.run(
                """
                MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                SET e.properties = $properties,
                    e.last_updated_date = datetime(),
                    e.updated_at = datetime(),
                    e.author_type = 'agent',
                    e.author_id = $author_id
                """,
                entity_id=entity_id,
                properties=json.dumps(prop_map),
                author_id=author_id,
            )

        # Strict relationship rule: only create relationships to entities linked to the same scene context.
        rel_defs = {r["id"]: r for r in (entity_def.get("relationships") or [])}
        for rel in extracted.new_relationships:
            rel_def = rel_defs.get(rel.definition_id)
            if not rel_def:
                continue
            target_id = rel.target_entity_instance_id or _resolve_alias(
                [rel.target_alias],
                alias_to_entity_id,
            )
            if not target_id or target_id not in allowed_targets:
                continue

            await graph_session.run(
                """
                MATCH (source:EntityInstance {entity_instance_id: $source_id})
                MATCH (target:EntityInstance {entity_instance_id: $target_id})
                OPTIONAL MATCH (source)-[existing:RELATES_TO {
                    relationship_definition_id: $definition_id
                }]->(target)
                WITH source, target, existing
                WHERE existing IS NULL
                CREATE (source)-[:RELATES_TO {
                    relationship_instance_id: $relationship_id,
                    relationship_definition_id: $definition_id,
                    destiny_entity_definition_id: $destiny_entity_definition_id,
                    data: $data,
                    created_at: datetime(),
                    updated_at: datetime()
                }]->(target)
                """,
                source_id=entity_id,
                target_id=target_id,
                relationship_id=str(uuid4()),
                definition_id=rel.definition_id,
                destiny_entity_definition_id=rel_def.get("destiny_entity_id"),
                data=json.dumps({"justification": rel.justification or ""}),
            )


async def _sync_entity_proposal_states(
    *,
    repo: ArchitectRepository,
    proposals: list[Any],
    frontend_entities: list[dict[str, Any]],
    proposal_to_entity_id: dict[int, str],
) -> None:
    status_by_index = {
        idx: item.get("status")
        for idx, item in enumerate(frontend_entities)
    }
    if not proposals:
        return
    for idx, proposal in enumerate(proposals):
        if proposal.proposal_type not in {
            ArchitectProposalType.NEW_INSTANCE,
            ArchitectProposalType.UPDATE_INSTANCE,
        }:
            continue
        if idx not in status_by_index:
            continue
        status = status_by_index[idx]
        mapped_status = ArchitectProposalStatus.REJECTED
        if _is_approved(status):
            mapped_status = ArchitectProposalStatus.APPROVED
        await repo.update_proposal_validation(
            proposal_id=proposal.id,
            status=mapped_status,
            corrected_alias=proposal.alias,
            corrected_entity_definition_id=proposal.entity_definition_id,
            corrected_proposal_type=proposal.proposal_type,
            corrected_entity_instance_id=proposal.entity_instance_id,
            merged_into_proposal_id=proposal.merged_into_proposal_id,
        )
        generated_id = proposal_to_entity_id.get(idx)
        if generated_id:
            await repo.update_proposal_generated_entity(proposal.id, generated_id)


def _is_approved(status: Any) -> bool:
    return str(status or "").lower() in {"approved", "approved_with_updates", "merged"}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_label(label: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (label or "related_to").strip().lower())
    cleaned = cleaned.strip("_")
    if not cleaned:
        return "related_to"
    return cleaned[:64]


def _resolve_alias(candidates: list[Any], alias_to_entity_id: dict[str, str]) -> str | None:
    for candidate in candidates:
        normalized = _norm(candidate)
        if normalized and normalized in alias_to_entity_id:
            return alias_to_entity_id[normalized]
    return None


def _build_summary_candidate(chunks: list[str], max_chars: int = 900) -> str:
    text = " ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _summary_contains(existing_summary: str, candidate: str) -> bool:
    if not existing_summary:
        return False
    normalized_existing = _norm(existing_summary)
    normalized_candidate = _norm(candidate)
    if not normalized_candidate:
        return True
    return normalized_candidate in normalized_existing


def _merge_summaries(existing_summary: str, candidate: str) -> str:
    if not existing_summary:
        return candidate
    return f"{existing_summary}\n\n{candidate}"


def _pick_scene_source_entity(
    *,
    scene_ref: str,
    scene_proposals: list[dict[str, Any]],
    fallback: str,
) -> str:
    for scene in scene_proposals:
        if str(scene.get("scene_ref") or "") == scene_ref:
            source = scene.get("source_entity_instance_id")
            if source:
                return source
            return fallback
    return fallback


def _build_entity_definitions_map(entity_defs: list[Any]) -> dict[int, dict[str, Any]]:
    definitions: dict[int, dict[str, Any]] = {}
    for entity_def in entity_defs:
        properties = []
        for prop in entity_def.properties:
            if not prop.auto_generatable:
                continue
            properties.append(
                {
                    "id": prop.id,
                    "name": prop.name,
                    "description": prop.description,
                    "data_type": prop.data_type.value,
                    "cardinality": prop.cardinality.value,
                }
            )
        relationships = []
        for rel in entity_def.relationships:
            if not rel.auto_generatable:
                continue
            relationships.append(
                {
                    "id": rel.id,
                    "name": rel.name,
                    "description": rel.description,
                    "destiny_entity_id": rel.destiny_entity_id,
                    "destiny_entity_name": rel.destiny_entity.name if rel.destiny_entity else None,
                    "bi_directional": rel.bi_directional,
                }
            )
        definitions[entity_def.id] = {
            "id": entity_def.id,
            "name": entity_def.name,
            "description": entity_def.description,
            "properties": properties,
            "relationships": relationships,
        }
    return definitions
