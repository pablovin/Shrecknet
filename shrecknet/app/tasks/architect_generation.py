"""Background task for Architect step 2 generation from reviewed frontend payload."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.architect.entity_generator import EntityGenerator
from app.models.ontology import AuthorType as OntologyAuthorType
from app.models.architect import ArchitectProposalStatus, ArchitectProposalType
from app.models.background_job import AuthorType, JobType
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.ontology_instance import (
    MilestoneCreate,
    MilestoneDerivedFrom,
    MilestoneEntityRelation,
    MilestoneLocalOrder,
    OntologyInstanceCreate,
    OntologyInstanceEntityCreate,
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


def _elapsed_seconds(started_at: float) -> float:
    return round(perf_counter() - started_at, 3)


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
    total_started_at = perf_counter()
    settings = get_settings()
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI API key not configured")


    outputs = reviewed_pipeline_output.get("outputs") or {}
    entity_proposals = _normalize_entity_proposals(outputs.get("entity_proposals") or [])
    # Accept both 'scene_proposals' and 'scenes' for compatibility
    scene_proposals = _normalize_scene_proposals(
        outputs.get("scene_proposals") or outputs.get("scenes") or []
    )
    # Accept both 'milestones_per_scene', 'milestone_proposals', and 'milestones' for compatibility
    milestones_per_scene = _normalize_milestone_groups(
        outputs.get("milestones_per_scene")
        or outputs.get("milestone_proposals")
        or outputs.get("milestones")
        or [],
        scene_proposals,
    )

    logger.info(
        "architect.generate run=%s start payload entity_proposals=%d scene_proposals=%d milestone_scene_groups=%d",
        run_id,
        len(entity_proposals),
        len(scene_proposals),
        len(milestones_per_scene),
    )

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

            instance_meta_result = await graph_session.run(
                """
                MATCH (i:OntologyInstance {instance_id: $instance_id})
                RETURN i.ontology_id AS ontology_id
                LIMIT 1
                """,
                instance_id=run.ontology_instance_id,
            )
            instance_meta = await instance_meta_result.single()
            if not instance_meta:
                raise ValueError(
                    f"Ontology instance '{run.ontology_instance_id}' not found in graph"
                )
            graph_ontology_id = instance_meta.get("ontology_id")
            if graph_ontology_id is None:
                raise ValueError(
                    f"Ontology instance '{run.ontology_instance_id}' missing ontology_id in graph"
                )

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

            logger.info(
                "architect.generate run=%s context_loaded existing_entities=%d auto_generatable_definitions=%d elapsed=%ss",
                run_id,
                len(existing_entities_map),
                len(entity_definitions_map),
                _elapsed_seconds(total_started_at),
            )

            created_entity_ids: list[str] = []
            updated_entity_ids: list[str] = []
            approved_entities = [item for item in entity_proposals if _is_approved(item.get("status"))]

            proposal_to_entity_id: dict[int, str] = {}
            proposal_scene_refs: dict[str, list[str]] = {}
            update_targets: set[str] = set()
            created_instance_ids: list[str] = []
            skipped_entities = 0

            await update_job_progress(
                job_id,
                0.14,
                {"status": "Step 0/4: applying approved update_instance entity updates"},
            )
            step0_started_at = perf_counter()
            for idx, proposal in enumerate(approved_entities):
                alias = (
                    ((proposal.get("updates") or {}).get("name"))
                    or proposal.get("name")
                    or "Unnamed"
                ).strip()
                scene_refs = [str(ref) for ref in (proposal.get("scene_refs") or []) if str(ref or "").strip()]
                if alias:
                    proposal_scene_refs[_norm(alias)] = scene_refs
                canonical = (proposal.get("canonical") or "").strip()
                if canonical:
                    proposal_scene_refs[_norm(canonical)] = scene_refs

                proposal_type = _effective_proposal_type(proposal)
                if proposal_type != ArchitectProposalType.UPDATE_INSTANCE.value:
                    continue

                explicit_id = _extract_effective_entity_instance_id(proposal)
                if not explicit_id:
                    logger.warning(
                        "Skipping update_instance proposal without entity_instance_id alias=%s",
                        alias,
                    )
                    skipped_entities += 1
                    continue

                definition_id = _extract_effective_definition_id(proposal)
                definition_id_int: int | None = None
                if definition_id is not None:
                    try:
                        candidate = int(definition_id)
                    except (TypeError, ValueError):
                        candidate = None
                    if candidate is not None and candidate in entity_definitions_map:
                        definition_id_int = candidate

                result = await graph_session.run(
                    """
                    MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_ENTITY]->(e:EntityInstance {entity_instance_id: $entity_id})
                    SET e.alias = coalesce($alias, e.alias),
                        e.entity_definition_id = coalesce($definition_id, e.entity_definition_id),
                        e.updated_at = datetime(),
                        e.last_updated_date = datetime(),
                        e.author_type = 'agent',
                        e.author_id = $author_id
                    RETURN e.entity_instance_id AS entity_id
                    """,
                    instance_id=run.ontology_instance_id,
                    entity_id=explicit_id,
                    alias=alias or None,
                    definition_id=definition_id_int,
                    author_id=author_id,
                )
                row = await result.single()
                if not row:
                    logger.warning(
                        "Skipping update_instance proposal for unknown entity_instance_id=%s",
                        explicit_id,
                    )
                    skipped_entities += 1
                    continue

                update_targets.add(explicit_id)
                proposal_to_entity_id[idx] = explicit_id
                if alias:
                    alias_to_entity_id[_norm(alias)] = explicit_id
                if canonical:
                    alias_to_entity_id[_norm(canonical)] = explicit_id
                existing_entity = existing_entities_map.get(explicit_id)
                if existing_entity is not None:
                    if alias:
                        existing_entity["alias"] = alias
                    if definition_id_int is not None:
                        existing_entity["definition_id"] = definition_id_int

            logger.info(
                "architect.generate run=%s step=0 done elapsed=%ss updated_entities=%d",
                run_id,
                _elapsed_seconds(step0_started_at),
                len(update_targets),
            )

            await update_job_progress(job_id, 0.18, {"status": "Step 1/4: inserting approved new entities"})
            step1_started_at = perf_counter()
            logger.info(
                "architect.generate run=%s step=1 start approved_entities=%d",
                run_id,
                len(approved_entities),
            )

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

                proposal_type = _effective_proposal_type(proposal)
                if proposal_type and proposal_type != ArchitectProposalType.NEW_INSTANCE.value:
                    logger.info(
                        "Skipping non-new proposal in step1 entity-creation mode alias=%s proposal_type=%s",
                        alias,
                        proposal_type,
                    )
                    skipped_entities += 1
                    continue

                ontology_name = ((proposal.get("updates") or {}).get("ontology") or proposal.get("ontology") or "").strip()
                definition_id = _extract_effective_definition_id(proposal)
                if definition_id is None and ontology_name:
                    definition_id = by_name.get(_norm(ontology_name))
                if definition_id is None:
                    logger.warning("Skipping proposal '%s' without resolvable definition", alias)
                    skipped_entities += 1
                    continue

                try:
                    definition_id_int = int(definition_id)
                except (TypeError, ValueError):
                    logger.warning(
                        "Skipping proposal '%s' with invalid definition id '%s'",
                        alias,
                        definition_id,
                    )
                    skipped_entities += 1
                    continue
                if definition_id_int not in entity_definitions_map:
                    logger.warning(
                        "Skipping proposal '%s' with out-of-ontology definition id '%s'",
                        alias,
                        definition_id_int,
                    )
                    skipped_entities += 1
                    continue

                created_instance = await service.create_instance(
                    OntologyInstanceCreate(
                        ontology_id=int(graph_ontology_id),
                        name=alias,
                        entities=[
                            OntologyInstanceEntityCreate(
                                definition_id=definition_id_int,
                                alias=alias,
                                text="",
                                node_avatar_url=None,
                                autogenerated_text=(proposal.get("why") or "").strip(),
                                author_type=OntologyAuthorType.AGENT,
                                author_id=author_id,
                                properties=[],
                                relationships=[],
                            )
                        ],
                        scenes=[],
                    )
                )
                if not created_instance.entities:
                    raise ValueError(
                        f"Failed to create entity for proposal alias '{alias}' in instance '{run.ontology_instance_id}'"
                    )
                new_entity_id = created_instance.entities[0].entity_instance_id
                created_entity_ids.append(new_entity_id)
                created_instance_ids.append(created_instance.instance_id)
                proposal_to_entity_id[idx] = new_entity_id
                alias_to_entity_id[_norm(alias)] = new_entity_id
                canonical = proposal.get("canonical")
                if canonical:
                    alias_to_entity_id[_norm(canonical)] = new_entity_id

            impacted_entity_ids: set[str] = set(created_entity_ids) | set(update_targets)

            logger.info(
                "architect.generate run=%s step=1 done elapsed=%ss created_entities=%d update_targets=%d skipped_entities=%d impacted_entities=%d",
                run_id,
                _elapsed_seconds(step1_started_at),
                len(created_entity_ids),
                len(update_targets),
                skipped_entities,
                len(impacted_entity_ids),
            )

            await update_job_progress(
                job_id,
                0.32,
                {
                    "status": "Step 1/4 completed",
                    "created_entities": len(created_entity_ids),
                    "created_instances": len(created_instance_ids),
                    "skipped_entities": skipped_entities,
                },
            )

            await update_job_progress(job_id, 0.36, {"status": "Step 2/4: inserting approved scenes"})
            step2_started_at = perf_counter()

            approved_scenes = [item for item in scene_proposals if _is_approved(item.get("status"))]
            approved_scenes.sort(key=lambda s: int(s.get("scene_order") or 0))
            logger.info(
                "architect.generate run=%s step=2 start approved_scenes=%d",
                run_id,
                len(approved_scenes),
            )
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
                for related in _effective_scene_related(scene):
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
                    name=(
                        (scene.get("updates") or {}).get("name")
                        or scene.get("scene_name")
                        or "Scene"
                    ).strip(),
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

            scene_relation_links = sum(
                len(entity_ids) for entity_ids in scene_ref_to_entities.values()
            )
            logger.info(
                "architect.generate run=%s step=2 done elapsed=%ss created_scenes=%d scene_rel_links=%d impacted_entities=%d",
                run_id,
                _elapsed_seconds(step2_started_at),
                created_scenes,
                scene_relation_links,
                len(impacted_entity_ids),
            )

            await update_job_progress(job_id, 0.54, {"status": "Step 3/4: inserting approved milestones"})
            step3_started_at = perf_counter()
            logger.info(
                "architect.generate run=%s step=3 start milestone_scene_groups=%d",
                run_id,
                len(milestones_per_scene),
            )

            created_milestones = 0
            milestone_rel_links = 0
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
                    for rel in _effective_milestone_related(milestone):
                        target_id = _resolve_alias([rel.get("entity")], alias_to_entity_id)
                        if not target_id:
                            target_id = rel.get("entity_instance_id")
                        if not target_id:
                            raise ValueError(
                                f"Unresolvable milestone related entity '{rel.get('entity')}' in scene {scene_ref}"
                            )
                        if target_id not in allowed_entities:
                            allowed_entities.add(target_id)
                            scene_ref_to_entities.setdefault(scene_ref, set()).add(target_id)
                        relates.append(
                            MilestoneEntityRelation(
                                entity_instance_id=target_id,
                                label=_normalize_label(rel.get("relationship_label") or "related_to"),
                            )
                        )
                        milestone_rel_links += 1
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

            logger.info(
                "architect.generate run=%s step=3 done elapsed=%ss created_milestones=%d milestone_rel_links=%d impacted_entities=%d",
                run_id,
                _elapsed_seconds(step3_started_at),
                created_milestones,
                milestone_rel_links,
                len(impacted_entity_ids),
            )

            await update_job_progress(job_id, 0.74, {"status": "Step 4/4: enriching and updating entities"})
            step4_started_at = perf_counter()
            logger.info(
                "architect.generate run=%s step=4 start enrichment_targets=%d",
                run_id,
                len(impacted_entity_ids),
            )

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
            entity_scene_refs: dict[str, set[str]] = defaultdict(set)
            for scene_ref, related_ids in scene_ref_to_entities.items():
                for related_id in related_ids:
                    entity_scene_refs[related_id].add(scene_ref)
            try:
                enrichment_stats = await _apply_enrichment_updates(
                    graph_session=graph_session,
                    generator=generator,
                    target_entity_ids=sorted(impacted_entity_ids),
                    entity_definitions_map=entity_definitions_map,
                    existing_entities_map=current_entities_map,
                    alias_to_entity_id=alias_to_entity_id,
                    scene_proposals=approved_scenes,
                    scene_ref_to_entities=scene_ref_to_entities,
                    proposal_scene_refs=proposal_scene_refs,
                    entity_scene_refs=entity_scene_refs,
                    original_text=original_text,
                    author_id=author_id,
                )
            finally:
                await llm_client.aclose()

            logger.info(
                "architect.generate run=%s step=4 done elapsed=%ss scanned=%d skipped=%d summary_updates=%d property_updates=%d relationship_creates=%d",
                run_id,
                _elapsed_seconds(step4_started_at),
                enrichment_stats["scanned_entities"],
                enrichment_stats["skipped_entities"],
                enrichment_stats["summary_updates"],
                enrichment_stats["property_updates"],
                enrichment_stats["relationship_creates"],
            )

            await update_job_progress(job_id, 0.9, {"status": "Triggering linking and embedding jobs"})
            step5_started_at = perf_counter()
            logger.info(
                "architect.generate run=%s step=post_jobs start impacted_entities=%d",
                run_id,
                len(impacted_entity_ids),
            )

            if impacted_entity_ids:
                link_instance_task.delay(run.ontology_instance_id, author_type="agent", author_id=author_id)
                embed_nodes_task.delay(ontology_id, sorted(impacted_entity_ids), author_type="agent", author_id=author_id)
                embed_instance_task.delay(run.ontology_instance_id, author_type="agent", author_id=author_id)

            logger.info(
                "architect.generate run=%s step=post_jobs done elapsed=%ss jobs_triggered=%s",
                run_id,
                _elapsed_seconds(step5_started_at),
                bool(impacted_entity_ids),
            )

            await _sync_entity_proposal_states(
                repo=repo,
                proposals=run.proposals,
                frontend_entities=entity_proposals,
                proposal_to_entity_id=proposal_to_entity_id,
            )
            await session.commit()

            logger.info(
                "architect.generate run=%s done total_elapsed=%ss created_entities=%d updated_entities=%d created_scenes=%d created_milestones=%d impacted_entities=%d",
                run_id,
                _elapsed_seconds(total_started_at),
                len(created_entity_ids),
                len(update_targets),
                created_scenes,
                created_milestones,
                len(impacted_entity_ids),
            )

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
    entity_scene_refs: dict[str, set[str]],
    original_text: str,
    author_id: str,
) -> dict[str, int]:
    stats = {
        "scanned_entities": 0,
        "skipped_entities": 0,
        "summary_updates": 0,
        "property_updates": 0,
        "relationship_creates": 0,
    }
    for entity_id in target_entity_ids:
        stats["scanned_entities"] += 1
        entity_data = existing_entities_map.get(entity_id)
        if not entity_data:
            stats["skipped_entities"] += 1
            continue

        alias = entity_data.get("alias") or ""
        scene_refs = set(proposal_scene_refs.get(_norm(alias), []))
        scene_refs |= set(entity_scene_refs.get(entity_id, set()))
        for scene in scene_proposals:
            related = _effective_scene_related(scene)
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
            stats["skipped_entities"] += 1
            continue

        definition_id = entity_data.get("definition_id")
        entity_def = entity_definitions_map.get(definition_id)
        if not entity_def:
            stats["skipped_entities"] += 1
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
            stats["summary_updates"] += 1

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
            stats["property_updates"] += 1

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
            stats["relationship_creates"] += 1

    return stats


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


def _effective_proposal_type(proposal: dict[str, Any]) -> str:
    updates = proposal.get("updates") or {}
    raw = (
        updates.get("corrected_proposal_type")
        or updates.get("proposal_type")
        or proposal.get("corrected_proposal_type")
        or proposal.get("proposal_type")
        or ""
    )
    return _norm(raw)


def _extract_effective_entity_instance_id(proposal: dict[str, Any]) -> str | None:
    updates = proposal.get("updates") or {}
    value = (
        updates.get("corrected_entity_instance_id")
        or updates.get("entity_instance_id")
        or proposal.get("corrected_entity_instance_id")
        or proposal.get("entity_instance_id")
    )
    normalized = str(value or "").strip()
    return normalized or None


def _extract_effective_definition_id(proposal: dict[str, Any]) -> Any:
    updates = proposal.get("updates") or {}
    return (
        updates.get("corrected_entity_definition_id")
        or updates.get("entity_definition_id")
        or proposal.get("corrected_entity_definition_id")
        or proposal.get("entity_definition_id")
    )


def _effective_scene_related(scene: dict[str, Any]) -> list[dict[str, Any]]:
    updates = scene.get("updates") or {}
    base = (
        updates.get("related_to")
        if isinstance(updates.get("related_to"), list)
        else (scene.get("related_to") or [])
    )
    related = [dict(item) for item in base if isinstance(item, dict)]

    for deletion in updates.get("relationship_deletions") or []:
        if not isinstance(deletion, dict):
            continue
        if _norm(deletion.get("operation")) != "delete":
            continue
        if _norm(deletion.get("relation_type")) != "related_to":
            continue
        target_idx = deletion.get("target_proposal_index")
        target_alias = _norm(deletion.get("target_alias"))
        target_id = deletion.get("target_entity_instance_id")
        filtered: list[dict[str, Any]] = []
        for item in related:
            proposal_idx = item.get("proposal_index")
            alias = _norm(item.get("alias"))
            canonical = _norm(item.get("canonical"))
            entity_id = item.get("entity_instance_id")
            should_delete = False
            if target_idx is not None and proposal_idx == target_idx:
                should_delete = True
            if target_alias and target_alias in {alias, canonical}:
                should_delete = True
            if target_id and target_id == entity_id:
                should_delete = True
            if not should_delete:
                filtered.append(item)
        related = filtered

    for entity_id in updates.get("additional_related_entity_instance_ids") or []:
        entity_id_str = str(entity_id or "").strip()
        if not entity_id_str:
            continue
        if any(item.get("entity_instance_id") == entity_id_str for item in related):
            continue
        related.append(
            {"entity_instance_id": entity_id_str, "alias": None, "canonical": None}
        )

    return related


def _effective_milestone_related(milestone: dict[str, Any]) -> list[dict[str, Any]]:
    updates = milestone.get("updates") or {}
    base = (
        updates.get("related_to")
        if isinstance(updates.get("related_to"), list)
        else (milestone.get("related_to") or [])
    )
    related = [dict(item) for item in base if isinstance(item, dict)]

    for deletion in updates.get("relationship_deletions") or []:
        if not isinstance(deletion, dict):
            continue
        if _norm(deletion.get("operation")) != "delete":
            continue
        if _norm(deletion.get("relation_type")) != "related_to":
            continue
        target_alias = _norm(deletion.get("target_alias"))
        target_id = deletion.get("target_entity_instance_id")
        filtered: list[dict[str, Any]] = []
        for item in related:
            entity_name = _norm(item.get("entity"))
            entity_id = item.get("entity_instance_id")
            should_delete = False
            if target_alias and entity_name == target_alias:
                should_delete = True
            if target_id and target_id == entity_id:
                should_delete = True
            if not should_delete:
                filtered.append(item)
        related = filtered

    return related


def _normalize_entity_proposals(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _normalize_scene_proposals(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _normalize_milestone_groups(
    raw: Any,
    scenes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    if raw and isinstance(raw[0], dict) and isinstance(raw[0].get("milestones"), list):
        return [dict(item) for item in raw if isinstance(item, dict)]

    scene_id_to_ref: dict[str, str] = {}
    for scene in scenes:
        scene_ref = str(scene.get("scene_ref") or "").strip()
        scene_id = str(scene.get("scene_id") or "").strip()
        if scene_ref and scene_id:
            scene_id_to_ref[scene_id] = scene_ref

    grouped: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        scene_ref = str(item.get("scene_ref") or "").strip()
        if not scene_ref:
            scene_id = str(item.get("scene_id") or "").strip()
            scene_ref = scene_id_to_ref.get(scene_id, scene_id)
        if not scene_ref:
            continue
        if scene_ref not in grouped:
            grouped[scene_ref] = {
                "scene_ref": scene_ref,
                "scene_id": item.get("scene_id"),
                "milestones": [],
            }
        grouped[scene_ref]["milestones"].append(item)

    return list(grouped.values())


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
