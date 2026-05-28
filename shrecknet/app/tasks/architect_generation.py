"""Background task for Architect step 2 generation from reviewed frontend payload."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_shreckllm_configured
from app.db.session import AsyncSessionMaker
from app.db.jobs_session import JobsSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.llm.runtime_control import fetch_shreckllm_runtime, resolve_effective_architect_concurrency
from app.jobs.architect.entity_generator import EntityGenerator
from app.models.ontology import AuthorType as OntologyAuthorType
from app.models.architect import ArchitectProposalStatus, ArchitectProposalType, ArchitectRunStatus
from app.models.background_job import AuthorType, JobType
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.background_job_repository import BackgroundJobRepository
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
    SceneEntityRelation,
    SceneLocalOrder,
)
from app.services.ontology_instance_service import OntologyInstanceService
from app.tasks.neo4j_embedding import embed_reconciliation as embed_reconciliation_task
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


def _log_run_llm_usage_summary(*, run_id: str, usage_summary: dict[str, Any]) -> None:
    totals = usage_summary.get("totals") if isinstance(usage_summary, dict) else {}
    totals = totals if isinstance(totals, dict) else {}
    logger.info(
        "architect_generation_llm_usage_summary run_id=%s total_calls=%d input_tokens_est=%d memory_tokens_est=%d output_tokens=%d total_tokens=%d estimated_cost_usd=%.6f by_model=%s by_tag=%s",
        run_id,
        int(totals.get("calls") or 0),
        int(totals.get("input_tokens_est") or 0),
        int(totals.get("memory_tokens_est") or 0),
        int(totals.get("output_tokens") or 0),
        int(totals.get("total_tokens") or 0),
        float(totals.get("estimated_cost_usd") or 0.0),
        usage_summary.get("by_model"),
        usage_summary.get("by_tag"),
    )


def _elapsed_seconds(started_at: float) -> float:
    return round(perf_counter() - started_at, 3)


@celery_app.task(name="architect.generate_entities")
def generate_entities(
    run_id: str,
    reviewed_pipeline_output: dict[str, Any],
    *,
    author_type: str = "agent",
    author_id: str = "system",
    retry_enrichment_only: bool = False,
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
            details={
                "run_id": run_id,
                "generation_metadata": {
                    "reviewed_pipeline_output": reviewed_pipeline_output,
                },
            },
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
                retry_enrichment_only=retry_enrichment_only,
            )
        )
        run_async(mark_job_done(job_id, result))
        return {"status": "success", "job_id": job_id, **result}
    except Exception as exc:
        logger.error("architect generation failed for run %s: %s", run_id, exc, exc_info=True)
        run_async(mark_job_failed(job_id, str(exc)))
        run_async(_mark_run_failed(run_id))
        raise


async def _attach_generation_job_to_run(run_id: str, job_id: int) -> None:
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        await repo.attach_generation_job(run_id, job_id)
        await session.commit()


async def _mark_run_failed(run_id: str) -> None:
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        await repo.update_run_status(run_id, status=ArchitectRunStatus.FAILED)
        await session.commit()


async def _execute_generation(
    *,
    run_id: str,
    reviewed_pipeline_output: dict[str, Any],
    job_id: int,
    author_id: str,
    author_type: str,
    retry_enrichment_only: bool = False,
) -> dict[str, Any]:
    total_started_at = perf_counter()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise RuntimeError("shreckLLM is not configured")


    outputs = reviewed_pipeline_output.get("outputs") or {}
    entity_proposals = _canonicalize_entity_proposals(
        _normalize_entity_proposals(outputs.get("entity_proposals") or [])
    )
    # Accept both 'scene_proposals' and 'scenes' for compatibility
    scene_proposals = _canonicalize_scene_proposals(
        _normalize_scene_proposals(outputs.get("scene_proposals") or outputs.get("scenes") or [])
    )
    # Accept both 'milestones_per_scene', 'milestone_proposals', and 'milestones' for compatibility
    milestones_per_scene = _canonicalize_milestone_groups(
        _normalize_milestone_groups(
            outputs.get("milestones_per_scene")
            or outputs.get("milestone_proposals")
            or outputs.get("milestones")
            or [],
            scene_proposals,
        )
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
        await repo.update_run_status(run_id, status=ArchitectRunStatus.RUNNING)
        await session.commit()

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
            approved_entities = [
                {**item, "_proposal_index": idx}
                for idx, item in enumerate(entity_proposals)
                if _is_approved(item.get("status"))
            ]

            proposal_to_entity_id: dict[int, str] = {}
            proposal_scene_refs: dict[str, list[str]] = {}
            update_targets: set[str] = set()
            created_instance_ids: list[str] = []
            pending_merge_resolutions: list[dict[str, Any]] = []
            skipped_entities = 0

            if not retry_enrichment_only:
                await update_job_progress(
                    job_id,
                    0.14,
                    {"status": "Step 0/4: applying approved update_instance entity updates"},
                )
            step0_started_at = perf_counter()
            for proposal in ([] if retry_enrichment_only else approved_entities):
                proposal_index = int(proposal.get("_proposal_index"))
                alias = str(proposal.get("effective_name") or "Unnamed").strip()
                alias_keys = _proposal_alias_keys(proposal)
                scene_refs = [
                    str(ref)
                    for ref in (proposal.get("effective_scene_refs") or [])
                    if str(ref or "").strip()
                ]
                for alias_key in alias_keys:
                    proposal_scene_refs[alias_key] = scene_refs
                canonical = (proposal.get("canonical") or "").strip()

                proposal_type = str(proposal.get("effective_proposal_type") or "")
                if proposal_type != ArchitectProposalType.UPDATE_INSTANCE.value:
                    continue

                explicit_id = str(proposal.get("effective_entity_instance_id") or "").strip() or None
                if not explicit_id:
                    logger.warning(
                        "Skipping update_instance proposal without entity_instance_id alias=%s",
                        alias,
                    )
                    skipped_entities += 1
                    continue

                definition_id = proposal.get("effective_definition_id")
                definition_id_int: int | None = None
                if definition_id is not None:
                    try:
                        candidate = int(definition_id)
                    except (TypeError, ValueError):
                        candidate = None
                    if candidate is not None and candidate in entity_definitions_map:
                        definition_id_int = candidate

                async def _update_entity_node(target_entity_id: str) -> Any:
                    return await graph_session.run(
                        """
                        MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                        SET e.alias = coalesce($alias, e.alias),
                            e.entity_definition_id = coalesce($definition_id, e.entity_definition_id),
                            e.updated_at = datetime(),
                            e.last_updated_date = datetime(),
                            e.author_type = 'agent',
                            e.author_id = $author_id
                        RETURN e.entity_instance_id AS entity_id
                        """,
                        entity_id=target_entity_id,
                        alias=alias or None,
                        definition_id=definition_id_int,
                        author_id=author_id,
                    )

                row = None
                ontology_scope_result = await graph_session.run(
                    """
                    MATCH (e:EntityInstance {entity_instance_id: $entity_id})<-[:HAS_ENTITY]-(:OntologyInstance {ontology_id: $ontology_id})
                    RETURN e.entity_instance_id AS entity_id
                    LIMIT 1
                    """,
                    entity_id=explicit_id,
                    ontology_id=int(graph_ontology_id),
                )
                ontology_scope_row = await ontology_scope_result.single()
                if ontology_scope_row:
                    update_result = await _update_entity_node(explicit_id)
                    row = await update_result.single()

                if not row:
                    # Fallback: resolve by alias/canonical across the whole ontology, not only
                    # the current instance.
                    ontology_alias_candidates = [alias, canonical]
                    ontology_alias_matches: list[str] = []
                    for alias_candidate in ontology_alias_candidates:
                        alias_value = str(alias_candidate or "").strip()
                        if not alias_value:
                            continue
                        alias_result = await graph_session.run(
                            """
                            MATCH (i:OntologyInstance {ontology_id: $ontology_id})-[:HAS_ENTITY]->(e:EntityInstance)
                            WHERE toLower(coalesce(e.alias, '')) = toLower($alias)
                            RETURN e.entity_instance_id AS entity_id
                            LIMIT 2
                            """,
                            ontology_id=int(graph_ontology_id),
                            alias=alias_value,
                        )
                        alias_rows = await alias_result.data()
                        ontology_alias_matches.extend(
                            [
                                str(item.get("entity_id") or "").strip()
                                for item in alias_rows
                                if str(item.get("entity_id") or "").strip()
                            ]
                        )
                    ontology_alias_matches = list(dict.fromkeys(ontology_alias_matches))
                    if len(ontology_alias_matches) == 1:
                        resolved_id = ontology_alias_matches[0]
                        update_result = await _update_entity_node(resolved_id)
                        fallback_row = await update_result.single()
                        if fallback_row:
                            explicit_id = resolved_id
                            row = fallback_row

                if not row:
                    raise ValueError(
                        "Unresolvable update_instance target "
                        f"proposal_index={proposal_index} alias='{alias}' "
                        f"entity_instance_id='{explicit_id}'. "
                        "The payload references an entity that does not exist in the target ontology instance."
                    )

                update_targets.add(explicit_id)
                proposal_to_entity_id[proposal_index] = explicit_id
                for alias_key in alias_keys:
                    alias_to_entity_id[alias_key] = explicit_id
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

            if not retry_enrichment_only:
                await update_job_progress(job_id, 0.18, {"status": "Step 1/4: inserting approved new entities"})
            step1_started_at = perf_counter()
            logger.info(
                "architect.generate run=%s step=1 start approved_entities=%d",
                run_id,
                len(approved_entities),
            )

            for proposal in ([] if retry_enrichment_only else approved_entities):
                proposal_index = int(proposal.get("_proposal_index"))
                alias = str(proposal.get("effective_name") or "Unnamed").strip()
                if not alias:
                    continue

                scene_refs = [str(ref) for ref in (proposal.get("effective_scene_refs") or [])]
                alias_keys = _proposal_alias_keys(proposal)
                for alias_key in alias_keys:
                    proposal_scene_refs[alias_key] = scene_refs

                proposal_type = str(proposal.get("effective_proposal_type") or "")
                if proposal_type and proposal_type != ArchitectProposalType.NEW_INSTANCE.value:
                    logger.info(
                        "Skipping non-new proposal in step1 entity-creation mode alias=%s proposal_type=%s",
                        alias,
                        proposal_type,
                    )
                    skipped_entities += 1
                    continue

                merge_update = _extract_merge_update(proposal)
                if merge_update:
                    pending_merge_resolutions.append(
                        {
                            "proposal_index": proposal_index,
                            "alias": alias,
                            "canonical": (proposal.get("canonical") or "").strip(),
                            "scene_refs": scene_refs,
                            "merge": merge_update,
                        }
                    )
                    logger.info(
                        "Skipping merged proposal insertion alias=%s maintained_alias=%s",
                        alias,
                        merge_update.get("maintained_alias"),
                    )
                    skipped_entities += 1
                    continue

                ontology_name = str(proposal.get("effective_ontology") or "").strip()
                definition_id = proposal.get("effective_definition_id")
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
                    ),
                    trigger_background_jobs=False,
                )
                if not created_instance.entities:
                    raise ValueError(
                        f"Failed to create entity for proposal alias '{alias}' in instance '{run.ontology_instance_id}'"
                    )
                new_entity_id = created_instance.entities[0].entity_instance_id
                created_entity_ids.append(new_entity_id)
                created_instance_ids.append(created_instance.instance_id)
                proposal_to_entity_id[proposal_index] = new_entity_id
                for alias_key in alias_keys:
                    alias_to_entity_id[alias_key] = new_entity_id

            merge_maintained_entity_ids: set[str] = set()
            for pending in pending_merge_resolutions:
                target_id = _resolve_maintained_entity_id(
                    merge_update=pending.get("merge") or {},
                    alias_to_entity_id=alias_to_entity_id,
                    proposal_to_entity_id=proposal_to_entity_id,
                )
                if not target_id:
                    logger.warning(
                        "Unable to resolve merge target for proposal alias=%s",
                        pending.get("alias"),
                    )
                    continue

                merged_alias = str(pending.get("alias") or "").strip()
                merged_canonical = str(pending.get("canonical") or "").strip()
                maintained_alias = str((pending.get("merge") or {}).get("maintained_alias") or "").strip()
                merged_scene_refs = [
                    str(ref)
                    for ref in (pending.get("scene_refs") or [])
                    if str(ref or "").strip()
                ]

                proposal_to_entity_id[pending["proposal_index"]] = target_id
                merge_maintained_entity_ids.add(target_id)

                for key in [merged_alias, merged_canonical]:
                    normalized = _norm(key)
                    if normalized:
                        alias_to_entity_id[normalized] = target_id

                if maintained_alias and merged_scene_refs:
                    maintained_key = _norm(maintained_alias)
                    existing_refs = proposal_scene_refs.get(maintained_key, [])
                    proposal_scene_refs[maintained_key] = _merge_ref_lists(
                        existing_refs,
                        merged_scene_refs,
                    )

            impacted_entity_ids: set[str] = (
                set(created_entity_ids)
                | set(update_targets)
                | set(merge_maintained_entity_ids)
            )

            logger.info(
                "architect.generate run=%s step=1 done elapsed=%ss created_entities=%d update_targets=%d merge_targets=%d skipped_entities=%d impacted_entities=%d",
                run_id,
                _elapsed_seconds(step1_started_at),
                len(created_entity_ids),
                len(update_targets),
                len(merge_maintained_entity_ids),
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

            approved_scenes = [item for item in scene_proposals if _is_approved(item.get("status"))]
            approved_scenes.sort(key=lambda s: int(s.get("scene_order") or 0))
            scene_ref_to_scene_id: dict[str, str] = {}
            scene_ref_to_entities: dict[str, set[str]] = {}
            previous_scene_id: str | None = None
            created_scenes = 0
            created_scene_ids: list[str] = []
            expected_scene_relation_links = 0

            existing_entity_ids = set(existing_entities_map.keys()) | set(created_entity_ids)
            default_source_entity_id = next(iter(existing_entity_ids), None)
            if default_source_entity_id is None:
                raise ValueError("No entity available to anchor derived_from for scenes")

            if retry_enrichment_only:
                for scene in approved_scenes:
                    scene_ref = str(scene.get("scene_ref") or "")
                    related_entity_ids: set[str] = set()
                    for related in (scene.get("effective_related_to") or []):
                        related_id = _resolve_related_target_entity_id(
                            related=related,
                            proposal_to_entity_id=proposal_to_entity_id,
                            alias_to_entity_id=alias_to_entity_id,
                            alias_candidates=[related.get("alias"), related.get("canonical")],
                            fallback_entity_instance_id=related.get("entity_instance_id"),
                            valid_entity_ids=existing_entity_ids,
                        )
                        if related_id:
                            related_entity_ids.add(related_id)
                    scene_ref_to_entities[scene_ref] = related_entity_ids
                    impacted_entity_ids |= related_entity_ids
            else:
                await update_job_progress(job_id, 0.36, {"status": "Step 2/4: inserting approved scenes"})
                step2_started_at = perf_counter()
                logger.info(
                    "architect.generate run=%s step=2 start approved_scenes=%d",
                    run_id,
                    len(approved_scenes),
                )
                for scene in approved_scenes:
                    source_entity_id = scene.get("source_entity_instance_id") or default_source_entity_id
                    if source_entity_id not in existing_entity_ids:
                        source_entity_id = default_source_entity_id

                    scene_ref = str(scene.get("scene_ref") or "")
                    scene_id = str(scene.get("scene_id") or uuid4())
                    scene_ref_to_scene_id[scene_ref] = scene_id

                    related_entity_ids: set[str] = set()
                    for related in (scene.get("effective_related_to") or []):
                        related_id = _resolve_related_target_entity_id(
                            related=related,
                            proposal_to_entity_id=proposal_to_entity_id,
                            alias_to_entity_id=alias_to_entity_id,
                            alias_candidates=[related.get("alias"), related.get("canonical")],
                            fallback_entity_instance_id=related.get("entity_instance_id"),
                            valid_entity_ids=existing_entity_ids,
                        )
                        if not related_id:
                            raise ValueError(
                                "Unresolvable related_to target in scene "
                                f"{scene_ref}: {related.get('alias') or related.get('canonical') or related.get('entity_instance_id')}"
                            )
                        related_entity_ids.add(related_id)
                    scene_ref_to_entities[scene_ref] = related_entity_ids
                    impacted_entity_ids |= related_entity_ids
                    expected_scene_relation_links += len(related_entity_ids)

                    payload = SceneCreate(
                        id=scene_id,
                        name=str(scene.get("effective_name") or scene.get("scene_name") or "Scene"),
                        description=(scene.get("scene_description") or scene.get("scene_text") or "").strip()[:2000],
                        created_by_type="agent",
                        created_by_author=author_id,
                        derived_from=SceneDerivedFrom(entity_instance_id=source_entity_id),
                        relates_to=[
                            SceneEntityRelation(entity_instance_id=entity_id, label="related_to")
                            for entity_id in sorted(related_entity_ids)
                        ],
                        local_order=SceneLocalOrder(preceded_by_scene_id=previous_scene_id),
                        milestones=[],
                    )
                    await service.create_scene(
                        run.ontology_instance_id,
                        payload,
                        trigger_background_jobs=False,
                    )
                    previous_scene_id = scene_id
                    created_scenes += 1
                    created_scene_ids.append(scene_id)

                actual_scene_relation_links = 0
                if created_scene_ids:
                    scene_rel_result = await graph_session.run(
                        """
                        UNWIND $scene_ids AS scene_id
                        MATCH (scene:Scene {id: scene_id})
                        OPTIONAL MATCH (scene)-[rel:RELATES_TO]->(:EntityInstance)
                        RETURN count(rel) AS relation_count
                        """,
                        scene_ids=created_scene_ids,
                    )
                    scene_rel_row = await scene_rel_result.single()
                    actual_scene_relation_links = int((scene_rel_row or {}).get("relation_count") or 0)
                if actual_scene_relation_links != expected_scene_relation_links:
                    raise ValueError(
                        "Scene relation persistence mismatch "
                        f"expected={expected_scene_relation_links} actual={actual_scene_relation_links}"
                    )

                scene_relation_links = expected_scene_relation_links
                logger.info(
                    "architect.generate run=%s step=2 done elapsed=%ss created_scenes=%d scene_rel_links=%d impacted_entities=%d",
                    run_id,
                    _elapsed_seconds(step2_started_at),
                    created_scenes,
                    scene_relation_links,
                    len(impacted_entity_ids),
                )

            if not retry_enrichment_only:
                await update_job_progress(job_id, 0.54, {"status": "Step 3/4: inserting approved milestones"})
                step3_started_at = perf_counter()
                logger.info(
                    "architect.generate run=%s step=3 start milestone_scene_groups=%d",
                    run_id,
                    len(milestones_per_scene),
                )

            created_milestones = 0
            milestone_rel_links = 0
            created_milestone_ids: list[str] = []
            milestone_ref_to_milestone_id: dict[str, str] = {}
            expected_milestone_relation_links = 0
            for scene_bundle in ([] if retry_enrichment_only else milestones_per_scene):
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
                    milestone_relation_pairs: set[tuple[str, str]] = set()
                    allowed_entities = scene_ref_to_entities.get(scene_ref, set())
                    for rel in (milestone.get("effective_related_to") or []):
                        target_id = _resolve_related_target_entity_id(
                            related=rel,
                            proposal_to_entity_id=proposal_to_entity_id,
                            alias_to_entity_id=alias_to_entity_id,
                            alias_candidates=[rel.get("entity"), rel.get("alias"), rel.get("canonical")],
                            fallback_entity_instance_id=rel.get("entity_instance_id"),
                            valid_entity_ids=existing_entity_ids,
                        )
                        if not target_id:
                            raise ValueError(
                                "Unresolvable milestone related target "
                                f"'{rel.get('entity') or rel.get('entity_instance_id')}' in scene {scene_ref}"
                            )
                        if target_id not in allowed_entities:
                            allowed_entities.add(target_id)
                            scene_ref_to_entities.setdefault(scene_ref, set()).add(target_id)
                        label = _normalize_label(rel.get("relationship_label") or "related_to")
                        pair = (str(target_id), label)
                        if pair in milestone_relation_pairs:
                            continue
                        milestone_relation_pairs.add(pair)
                        relates.append(
                            MilestoneEntityRelation(
                                entity_instance_id=target_id,
                                label=label,
                            )
                        )
                        impacted_entity_ids.add(target_id)

                    milestone_rel_links += len(milestone_relation_pairs)
                    expected_milestone_relation_links += len(milestone_relation_pairs)

                    payload = MilestoneCreate(
                        id=milestone_id,
                        name=str(milestone.get("title") or milestone.get("label") or "Milestone"),
                        description=(milestone.get("description") or "").strip(),
                        created_by_type="agent",
                        created_by_author=author_id,
                        boundary_type=milestone.get("boundary_type") or "none",
                        local_order=MilestoneLocalOrder(preceded_by_milestone_id=prev_milestone_id),
                        derived_from=MilestoneDerivedFrom(entity_instance_id=source_entity_id),
                        relates_to=relates,
                    )
                    await service.create_milestone(
                        run.ontology_instance_id,
                        scene_id,
                        payload,
                        trigger_background_jobs=False,
                    )
                    prev_milestone_id = milestone_id
                    created_milestones += 1
                    created_milestone_ids.append(milestone_id)
                    milestone_ref = str(milestone.get("milestone_ref") or milestone_id)
                    milestone_ref_to_milestone_id[milestone_ref] = milestone_id

            actual_milestone_relation_links = 0
            if created_milestone_ids:
                milestone_rel_result = await graph_session.run(
                    """
                    UNWIND $milestone_ids AS milestone_id
                    MATCH (milestone:Milestone {id: milestone_id})
                    OPTIONAL MATCH (milestone)-[rel:RELATES_TO]->(:EntityInstance)
                    RETURN count(rel) AS relation_count
                    """,
                    milestone_ids=created_milestone_ids,
                )
                milestone_rel_row = await milestone_rel_result.single()
                actual_milestone_relation_links = int((milestone_rel_row or {}).get("relation_count") or 0)
            if not retry_enrichment_only and actual_milestone_relation_links != expected_milestone_relation_links:
                raise ValueError(
                    "Milestone relation persistence mismatch "
                    f"expected={expected_milestone_relation_links} actual={actual_milestone_relation_links}"
                )

            if not retry_enrichment_only:
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
            enrichment_target_entity_ids = sorted(
                _collect_scene_linked_entity_ids(scene_ref_to_entities)
            )
            if retry_enrichment_only:
                previous_failed_ids: list[str] = []
                if run.generation_job_id is not None:
                    async with JobsSessionMaker() as jobs_session:
                        jobs_repo = BackgroundJobRepository(jobs_session)
                        previous_job = await jobs_repo.get_by_id(int(run.generation_job_id))
                        if previous_job and isinstance(previous_job.details, str):
                            try:
                                previous_payload = json.loads(previous_job.details)
                            except Exception:
                                previous_payload = {}
                            if isinstance(previous_payload, dict):
                                raw_failed = previous_payload.get("enrichment_failed_entity_ids")
                                if isinstance(raw_failed, list):
                                    previous_failed_ids = [str(item) for item in raw_failed if str(item).strip()]
                if isinstance(previous_failed_ids, list):
                    previous_failed_set = {str(item).strip() for item in previous_failed_ids if str(item).strip()}
                    enrichment_target_entity_ids = [
                        entity_id for entity_id in enrichment_target_entity_ids if entity_id in previous_failed_set
                    ]
            excluded_from_enrichment = len(set(impacted_entity_ids) - set(enrichment_target_entity_ids))
            logger.info(
                "architect.generate run=%s step=4 start impacted_entities=%d enrichment_targets=%d excluded_no_scene_or_milestone_link=%d target_ids_sample=%s",
                run_id,
                len(impacted_entity_ids),
                len(enrichment_target_entity_ids),
                excluded_from_enrichment,
                enrichment_target_entity_ids[:20],
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

            generation_model = settings.model_architect_entity_generation
            runtime_config = await fetch_shreckllm_runtime(settings)
            enrichment_concurrency = resolve_effective_architect_concurrency(
                runtime_config,
                provider_id=generation_model.provider,
            )
            model_policy = ModelPolicy(
                default_model=generation_model,
                architect_extract_model=generation_model,
            )
            llm_client = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.shreckllm_request_timeout_s, max_retries=settings.shreckllm_max_retries)
            generator = EntityGenerator(
                llm_client,
                model_policy,
                concurrent_extractions=enrichment_concurrency,
            )
            entity_scene_refs: dict[str, set[str]] = defaultdict(set)
            for scene_ref, related_ids in scene_ref_to_entities.items():
                for related_id in related_ids:
                    entity_scene_refs[related_id].add(scene_ref)
            enrichment_retryable_failed = 0
            enrichment_terminal_failed = 0
            try:
                usage_start = llm_client.get_usage_event_count()
                try:
                    enrichment_stats = await _apply_enrichment_updates(
                        graph_session=graph_session,
                        generator=generator,
                        debug_job_id=job_id,
                        target_entity_ids=enrichment_target_entity_ids,
                        entity_definitions_map=entity_definitions_map,
                        existing_entities_map=current_entities_map,
                        alias_to_entity_id=alias_to_entity_id,
                        scene_proposals=approved_scenes,
                        milestone_groups=milestones_per_scene,
                        scene_ref_to_entities=scene_ref_to_entities,
                        proposal_scene_refs=proposal_scene_refs,
                        entity_scene_refs=entity_scene_refs,
                        author_id=author_id,
                        extraction_concurrency=enrichment_concurrency,
                    )
                except Exception as exc:
                    message = str(exc).lower()
                    retryable = "429" in message or "overload" in message or "timeout" in message
                    if retryable:
                        enrichment_retryable_failed = len(enrichment_target_entity_ids)
                        enrichment_stats = {
                            "scanned_entities": len(enrichment_target_entity_ids),
                            "processed_entities": 0,
                            "fallback_entities": 0,
                            "failed_entities": len(enrichment_target_entity_ids),
                            "summary_updates": 0,
                            "property_updates": 0,
                            "relationship_creates": 0,
                            "relationship_updates": 0,
                            "failed_entity_ids": list(enrichment_target_entity_ids),
                        }
                        logger.warning("architect.generate run=%s enrichment retryable failure: %s", run_id, exc)
                    else:
                        enrichment_terminal_failed = len(enrichment_target_entity_ids)
                        raise
                step_usage = llm_client.get_usage_summary_since(usage_start)
                logger.info(
                    "architect_generation_llm_usage_step run_id=%s step=%s totals=%s by_model=%s by_tag=%s",
                    run_id,
                    "enrichment",
                    step_usage.get("totals"),
                    step_usage.get("by_model"),
                    step_usage.get("by_tag"),
                )
                usage_summary = llm_client.get_usage_summary()
                _log_run_llm_usage_summary(run_id=run_id, usage_summary=usage_summary)
            finally:
                await llm_client.aclose()

            logger.info(
                "architect.generate run=%s step=4 done elapsed=%ss scanned=%d processed=%d fallback=%d failed=%d summary_updates=%d property_updates=%d relationship_creates=%d relationship_updates=%d",
                run_id,
                _elapsed_seconds(step4_started_at),
                enrichment_stats["scanned_entities"],
                enrichment_stats["processed_entities"],
                enrichment_stats["fallback_entities"],
                enrichment_stats["failed_entities"],
                enrichment_stats["summary_updates"],
                enrichment_stats["property_updates"],
                enrichment_stats["relationship_creates"],
                enrichment_stats["relationship_updates"],
            )

            await update_job_progress(job_id, 0.9, {"status": "Triggering linking and embedding jobs"})
            step5_started_at = perf_counter()
            logger.info(
                "architect.generate run=%s step=post_jobs start impacted_entities=%d",
                run_id,
                len(impacted_entity_ids),
            )

            batch_embed_node_ids = sorted(
                set(impacted_entity_ids)
                | set(created_scene_ids)
                | set(created_milestone_ids)
            )

            if batch_embed_node_ids:
                expires_seconds = max(60, int(settings.celery_expires_reconciliation_seconds))
                link_instance_task.apply_async(
                    args=[run.ontology_instance_id],
                    kwargs={"author_type": "agent", "author_id": author_id},
                    expires=expires_seconds,
                )
                embed_reconciliation_task.apply_async(
                    kwargs={
                        "ontology_id": ontology_id,
                        "instance_id": None,
                        "node_ids": batch_embed_node_ids,
                        "author_type": "agent",
                        "author_id": author_id,
                    },
                    expires=expires_seconds,
                )

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
            final_status = (
                ArchitectRunStatus.COMPLETED_WITH_WARNINGS
                if enrichment_retryable_failed > 0
                else ArchitectRunStatus.COMPLETED
            )
            await repo.update_run_status(run_id, status=final_status)
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
                "embedding_nodes_requested": len(batch_embed_node_ids),
                "core_success": True,
                "enrichment_total": len(enrichment_target_entity_ids),
                "enrichment_succeeded": max(0, len(enrichment_target_entity_ids) - enrichment_stats["failed_entities"]),
                "enrichment_retryable_failed": enrichment_retryable_failed,
                "enrichment_terminal_failed": enrichment_terminal_failed,
                "enrichment_failed_entity_ids": enrichment_stats.get("failed_entity_ids", []),
                "generation_metadata": {
                    "reviewed_pipeline_output": reviewed_pipeline_output,
                    "retry_enrichment_only": retry_enrichment_only,
                },
                # Frontend reconciliation: real persisted IDs keyed by proposal/ref.
                "entity_reconciliation": [
                    {"proposal_index": proposal_index, "entity_instance_id": entity_instance_id}
                    for proposal_index, entity_instance_id in sorted(proposal_to_entity_id.items())
                ],
                "scene_reconciliation": [
                    {"scene_ref": scene_ref, "scene_id": scene_id}
                    for scene_ref, scene_id in scene_ref_to_scene_id.items()
                ],
                "milestone_reconciliation": [
                    {"milestone_ref": milestone_ref, "milestone_id": milestone_id}
                    for milestone_ref, milestone_id in milestone_ref_to_milestone_id.items()
                ],
            }


_ENRICHMENT_CONCURRENCY = 10


async def _apply_enrichment_updates(
    *,
    graph_session: Any,
    generator: EntityGenerator,
    debug_job_id: int | None,
    target_entity_ids: list[str],
    entity_definitions_map: dict[int, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
    alias_to_entity_id: dict[str, str],
    scene_proposals: list[dict[str, Any]],
    milestone_groups: list[dict[str, Any]],
    scene_ref_to_entities: dict[str, set[str]],
    proposal_scene_refs: dict[str, list[str]],
    entity_scene_refs: dict[str, set[str]],
    author_id: str,
    extraction_concurrency: int | None = None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "scanned_entities": 0,
        "processed_entities": 0,
        "fallback_entities": 0,
        "failed_entities": 0,
        "summary_updates": 0,
        "property_updates": 0,
        "relationship_creates": 0,
        "relationship_updates": 0,
        "failed_entity_ids": [],
    }

    # ------------------------------------------------------------------
    missing_entity_ids = [
        entity_id
        for entity_id in target_entity_ids
        if entity_id not in existing_entities_map
    ]
    if missing_entity_ids:
        hydration_result = await graph_session.run(
            """
            UNWIND $entity_ids AS entity_id
            MATCH (e:EntityInstance {entity_instance_id: entity_id})
            RETURN
                e.entity_instance_id AS entity_id,
                e.alias AS alias,
                e.entity_definition_id AS definition_id,
                coalesce(e.text, '') AS text,
                coalesce(e.autogenerated_text, '') AS autogenerated_text,
                coalesce(e.properties, '{}') AS properties
            """,
            entity_ids=missing_entity_ids,
        )
        hydrated_rows = await hydration_result.data()
        hydrated_ids: set[str] = set()
        for row in hydrated_rows:
            entity_id = str(row.get("entity_id") or "").strip()
            if not entity_id:
                continue
            hydrated_ids.add(entity_id)
            raw_properties = row.get("properties")
            parsed_properties: list[dict[str, Any]] = []
            if isinstance(raw_properties, str):
                try:
                    decoded = json.loads(raw_properties)
                except Exception:
                    decoded = {}
            elif isinstance(raw_properties, dict):
                decoded = raw_properties
            else:
                decoded = {}
            if isinstance(decoded, dict):
                parsed_properties = [
                    {"definition_id": int(k), "value": v}
                    for k, v in decoded.items()
                    if str(k).isdigit()
                ]
            existing_entities_map[entity_id] = {
                "alias": str(row.get("alias") or "").strip(),
                "definition_id": row.get("definition_id"),
                "properties": parsed_properties,
                "relationships": [],
                "text": str(row.get("text") or ""),
                "autogenerated_text": str(row.get("autogenerated_text") or ""),
            }
        unresolved_ids = sorted(set(missing_entity_ids) - hydrated_ids)
        logger.info(
            "architect.generate enrichment_hydration requested=%d hydrated=%d unresolved=%d unresolved_ids=%s",
            len(missing_entity_ids),
            len(hydrated_ids),
            len(unresolved_ids),
            unresolved_ids[:20],
        )

    # ------------------------------------------------------------------
    # Phase 1: Prepare per-entity context and run LLM extraction in
    # parallel (max _ENRICHMENT_CONCURRENCY concurrent calls).  Graph
    # writes happen in Phase 2 sequentially so the Neo4j session is
    # never accessed concurrently.
    # ------------------------------------------------------------------

    effective_concurrency = (
        max(1, int(extraction_concurrency))
        if extraction_concurrency is not None
        else _ENRICHMENT_CONCURRENCY
    )
    semaphore = asyncio.Semaphore(effective_concurrency)

    async def _prepare_and_extract(entity_id: str):
        """Prepare enrichment inputs and run extraction for one entity."""
        entity_data = existing_entities_map.get(entity_id)
        anomalies: list[str] = []
        if not entity_data:
            anomalies.append("missing_entity_data")
            entity_data = {
                "alias": entity_id,
                "definition_id": None,
                "properties": [],
                "relationships": [],
                "text": "",
                "autogenerated_text": "",
            }

        alias = entity_data.get("alias") or ""
        scene_refs = set(proposal_scene_refs.get(_norm(alias), []))
        scene_refs |= set(entity_scene_refs.get(entity_id, set()))
        for scene in scene_proposals:
            related = _effective_scene_related(scene)
            for rel in related:
                rel_id = rel.get("entity_instance_id") or _resolve_alias(
                    [rel.get("alias"), rel.get("canonical")], alias_to_entity_id
                )
                if rel_id == entity_id:
                    scene_refs.add(str(scene.get("scene_ref") or ""))

        chunks: list[str] = []
        scenes_for_context: list[dict[str, Any]] = []
        allowed_targets: set[str] = set()
        def _entity_ref(entity_instance_id: str) -> dict[str, str]:
            target_data = existing_entities_map.get(entity_instance_id) or {}
            target_def = entity_definitions_map.get(target_data.get("definition_id")) or {}
            return {
                "name": str(target_data.get("alias") or entity_instance_id),
                "id": str(entity_instance_id),
                "type": str(target_def.get("name") or "Entity"),
            }

        for scene in scene_proposals:
            scene_ref = str(scene.get("scene_ref") or "")
            if scene_ref in scene_refs:
                # Architect scene proposals provide scene_text/scene_description.
                context_parts = [
                    str(scene.get("scene_text") or "").strip(),
                    str(scene.get("scene_description") or "").strip(),
                ]
                context = "\n".join(part for part in context_parts if part)
                if context:
                    chunks.append(context)
                allowed_targets |= scene_ref_to_entities.get(scene_ref, set())
                scenes_for_context.append(
                    {
                        "scene_ref": scene_ref,
                        "title": str(scene.get("effective_name") or scene.get("scene_name") or "Scene"),
                        "description": str(scene.get("scene_description") or scene.get("scene_text") or "").strip(),
                        "related_entities": [
                            _entity_ref(target_id)
                            for target_id in sorted(scene_ref_to_entities.get(scene_ref, set()))
                        ],
                    }
                )

        milestones_for_context: list[dict[str, Any]] = []
        milestone_refs = {item.get("scene_ref") for item in scenes_for_context}
        for group in milestone_groups:
            scene_ref = str(group.get("scene_ref") or "")
            if scene_ref not in milestone_refs:
                continue
            for milestone in (group.get("milestones") or []):
                related = [
                    str(
                        _resolve_related_target_entity_id(
                            related=rel,
                            proposal_to_entity_id={},
                            alias_to_entity_id=alias_to_entity_id,
                            alias_candidates=[rel.get("entity"), rel.get("alias"), rel.get("canonical")],
                            fallback_entity_instance_id=rel.get("entity_instance_id"),
                            valid_entity_ids=set(existing_entities_map.keys()) | set(target_entity_ids),
                        )
                        or rel.get("entity_instance_id")
                        or rel.get("entity")
                        or rel.get("alias")
                        or ""
                    ).strip()
                    for rel in (milestone.get("effective_related_to") or [])
                ]
                milestones_for_context.append(
                    {
                        "scene_ref": scene_ref,
                        "milestone_ref": str(milestone.get("milestone_ref") or ""),
                        "title": str(milestone.get("title") or milestone.get("label") or "Milestone"),
                        "description": str(milestone.get("description") or "").strip(),
                        "related_entities": [
                            _entity_ref(item)
                            for item in related
                            if item and (item in existing_entities_map)
                        ],
                    }
                )

        if not chunks:
            anomalies.append("no_context")
            chunks = [
                "No scene text context was linked to this entity in step 4. "
                "Use existing entity state and global text context to infer safe updates."
            ]

        definition_id = entity_data.get("definition_id")
        entity_def = entity_definitions_map.get(definition_id) if definition_id is not None else None
        if not entity_def:
            anomalies.append("missing_definition")
            entity_def = {
                "id": definition_id if isinstance(definition_id, int) else -1,
                "name": "Unknown Entity",
                "properties": [],
                "relationships": [],
            }
            if definition_id is None:
                definition_id = -1
        related_entities_for_prompt: list[dict[str, Any]] = []
        compatible_destiny_ids = {
            int(rel.get("destiny_entity_id"))
            for rel in (entity_def.get("relationships") or [])
            if rel.get("destiny_entity_id") is not None
            and str(rel.get("destiny_entity_id")).isdigit()
        }
        for target_id in sorted(allowed_targets):
            target_data = existing_entities_map.get(target_id) or {}
            target_def_id = target_data.get("definition_id")
            try:
                target_def_id_int = int(target_def_id) if target_def_id is not None else None
            except (TypeError, ValueError):
                target_def_id_int = None
            if compatible_destiny_ids and target_def_id_int not in compatible_destiny_ids:
                continue
            target_def = entity_definitions_map.get(target_def_id) or {}
            related_entities_for_prompt.append(
                {
                    "entity_instance_id": target_id,
                    "name": target_data.get("alias") or target_id,
                    "entity_type_name": target_def.get("name") or "Entity",
                }
            )
        try:
            definition_id_int = int(definition_id)
        except (TypeError, ValueError):
            definition_id_int = -1
            if "invalid_definition_id" not in anomalies:
                anomalies.append("invalid_definition_id")

        context_package = {
            "entity_id": entity_id,
            "entity_alias": alias or entity_id,
            "allowed_relationship_targets": sorted(allowed_targets),
            "related_entities": related_entities_for_prompt,
            "scenes": scenes_for_context,
            "milestones": milestones_for_context,
        }

        async with semaphore:
            extracted = await generator._extract_properties_and_relationships(
                entity_definition_id=definition_id_int,
                entity_alias=alias or entity_id,
                entity_type_name=entity_def.get("name") or "Entity",
                properties_catalog=entity_def.get("properties") or [],
                relationships_catalog=entity_def.get("relationships") or [],
                chunks=chunks,
                related_entities=related_entities_for_prompt,
                is_update=True,
                existing_text=entity_data.get("text") or "",
                existing_autogenerated_text=entity_data.get("autogenerated_text") or "",
                existing_properties=entity_data.get("properties") or [],
                existing_relationships=entity_data.get("relationships") or [],
                debug_job_id=debug_job_id,
                debug_entity_id=entity_id,
                debug_anomalies=anomalies,
                debug_context_package=context_package,
                update_response_mode="strict_name_based",
            )

        return {
            "entity_id": entity_id,
            "entity_data": entity_data,
            "entity_def": entity_def,
            "allowed_targets": allowed_targets,
            "extracted": extracted,
            "anomalies": anomalies,
            "context_package": context_package,
        }

    stats["scanned_entities"] = len(target_entity_ids)
    extraction_tasks = [_prepare_and_extract(eid) for eid in target_entity_ids]
    extraction_results = await asyncio.gather(*extraction_tasks)
    extraction_by_target = dict(zip(target_entity_ids, extraction_results, strict=False))

    # ------------------------------------------------------------------
    # Phase 2: Apply graph writes sequentially.
    # ------------------------------------------------------------------
    def _snippet(value: Any, *, limit: int = 50) -> str:
        text = str(value or "").replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    for target_entity_id in target_entity_ids:
        result_item = extraction_by_target.get(target_entity_id)
        if result_item is None:
            stats["failed_entities"] += 1
            stats["failed_entity_ids"].append(target_entity_id)
            logger.info(
                "architect.generate enrichment entity_id=%s status=not_updated reason=processing_failed auto_text_50= attributes_50= relationships_added=0 summary_updated=false properties_updated=false",
                target_entity_id,
            )
            continue

        entity_id = result_item["entity_id"]
        entity_data = result_item["entity_data"]
        entity_def = result_item["entity_def"]
        allowed_targets = result_item["allowed_targets"]
        extracted = result_item["extracted"]
        anomalies = result_item.get("anomalies") or []
        entity_alias = str(entity_data.get("alias") or "").strip() or entity_id
        stats["processed_entities"] += 1
        if anomalies:
            stats["fallback_entities"] += 1

        existing_summary = (entity_data.get("autogenerated_text") or "").strip()
        candidate_summary = (extracted.updated_autogenerated_summary or "").strip()
        summary_updated = False
        properties_updated = False
        relationships_added = 0
        relationships_updated = 0
        if candidate_summary and candidate_summary != existing_summary:
            merged_summary = candidate_summary
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
            summary_updated = True
        else:
            merged_summary = existing_summary

        # Update properties only when evidence indicates a change/new value.
        prop_result = await graph_session.run(
            """
            MATCH (e:EntityInstance {entity_instance_id: $entity_id})
            RETURN e.properties AS properties
            """,
            entity_id=entity_id,
        )
        record = await prop_result.single()
        prop_map: dict[str, Any] = {}
        if record and record.get("properties"):
            raw_props = record.get("properties")
            if isinstance(raw_props, str):
                try:
                    prop_map = json.loads(raw_props)
                except Exception:
                    prop_map = {}

        properties_changed = False
        prop_defs_by_name: dict[str, int] = {}
        for prop_def in (entity_def.get("properties") or []):
            name_key = _norm(prop_def.get("name"))
            if name_key and name_key not in prop_defs_by_name:
                prop_defs_by_name[name_key] = int(prop_def.get("id"))

        for prop in getattr(extracted, "properties_update", []):
            def_id = prop_defs_by_name.get(_norm(prop.property_name))
            if def_id is None:
                continue
            key = str(def_id)
            old_value = prop_map.get(key)
            if old_value is None or old_value != prop.property_value:
                prop_map[key] = prop.property_value
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
            properties_updated = True

        # Strict relationship rule: only create relationships to entities
        # linked to the same scene context (entity-to-entity only).
        rel_defs_by_name: dict[str, dict[str, Any]] = {}
        for rel_def in (entity_def.get("relationships") or []):
            name_key = _norm(rel_def.get("name"))
            if name_key and name_key not in rel_defs_by_name:
                rel_defs_by_name[name_key] = rel_def

        for rel in getattr(extracted, "relationships_update", []):
            rel_def = rel_defs_by_name.get(_norm(rel.relationship_name))
            if not rel_def:
                continue
            target_id = str(rel.relationship_target or "").strip()
            if not target_id or target_id not in allowed_targets:
                continue
            target_data = existing_entities_map.get(target_id) or {}
            target_def_id = target_data.get("definition_id")
            destiny_def_id = rel_def.get("destiny_entity_id")
            if destiny_def_id is not None and target_def_id is not None:
                try:
                    if int(target_def_id) != int(destiny_def_id):
                        continue
                except (TypeError, ValueError):
                    continue

            rel_data = json.dumps({"justification": ""})
            rel_check_result = await graph_session.run(
                """
                MATCH (source:EntityInstance {entity_instance_id: $source_id})
                MATCH (target:EntityInstance {entity_instance_id: $target_id})
                OPTIONAL MATCH (source)-[existing:RELATES_TO {
                    relationship_definition_id: $definition_id
                }]->(target)
                RETURN existing.data AS existing_data
                LIMIT 1
                """,
                source_id=entity_id,
                target_id=target_id,
                definition_id=rel_def.get("id"),
            )
            rel_check_row = await rel_check_result.single()
            existing_data = (rel_check_row or {}).get("existing_data")

            await graph_session.run(
                """
                MATCH (source:EntityInstance {entity_instance_id: $source_id})
                MATCH (target:EntityInstance {entity_instance_id: $target_id})
                MERGE (source)-[rel:RELATES_TO {
                    relationship_definition_id: $definition_id
                }]->(target)
                ON CREATE SET
                    rel.relationship_instance_id = $relationship_id,
                    rel.destiny_entity_definition_id = $destiny_entity_definition_id,
                    rel.created_at = datetime(),
                    rel.data = $data
                SET rel.updated_at = datetime(),
                    rel.data = $data
                """,
                source_id=entity_id,
                target_id=target_id,
                relationship_id=str(uuid4()),
                definition_id=rel_def.get("id"),
                destiny_entity_definition_id=rel_def.get("destiny_entity_id"),
                data=rel_data,
            )
            if existing_data is None:
                stats["relationship_creates"] += 1
                relationships_added += 1
            elif existing_data != rel_data:
                stats["relationship_updates"] += 1
                relationships_updated += 1

        was_updated = summary_updated or properties_updated or relationships_added > 0
        logger.info(
            "architect.generate enrichment entity=%s entity_id=%s status=%s mode=%s fallback_reasons=%s auto_text_50=%s attributes_50=%s relationships_added=%d relationships_updated=%d summary_updated=%s properties_updated=%s",
            entity_alias,
            entity_id,
            "updated" if was_updated else "not_updated",
            "processed_with_fallback_context" if anomalies else "processed",
            ",".join(anomalies) if anomalies else "",
            _snippet(merged_summary, limit=50),
            _snippet(json.dumps(prop_map, ensure_ascii=False), limit=50),
            relationships_added,
            relationships_updated,
            summary_updated,
            properties_updated,
        )

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
    entity_in_instance_updates = updates.get("entityInInstance")
    if not isinstance(entity_in_instance_updates, dict):
        entity_in_instance_updates = {}
    entity_in_instance_proposal = proposal.get("entityInInstance")
    if not isinstance(entity_in_instance_proposal, dict):
        entity_in_instance_proposal = {}

    value = (
        updates.get("corrected_entity_instance_id")
        or updates.get("correctedEntityInstanceId")
        or updates.get("entity_instance_id")
        or updates.get("entityInstanceId")
        or entity_in_instance_updates.get("entity_instance_id")
        or entity_in_instance_updates.get("entityInstanceId")
        or proposal.get("corrected_entity_instance_id")
        or proposal.get("correctedEntityInstanceId")
        or proposal.get("entity_instance_id")
        or proposal.get("entityInstanceId")
        or entity_in_instance_proposal.get("entity_instance_id")
        or entity_in_instance_proposal.get("entityInstanceId")
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


def _extract_merge_update(proposal: dict[str, Any]) -> dict[str, Any] | None:
    updates = proposal.get("updates") or {}
    merge = updates.get("merge")
    if isinstance(merge, dict):
        return merge
    return None


def _parse_analysis_entity_index(value: Any) -> int | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    direct = re.fullmatch(r"\d+", raw)
    if direct:
        return int(raw)
    match = re.search(r"(?:analysis-entity-|entity-)(\d+)$", raw)
    if not match:
        return None
    return int(match.group(1))


def _resolve_maintained_entity_id(
    *,
    merge_update: dict[str, Any],
    alias_to_entity_id: dict[str, str],
    proposal_to_entity_id: dict[int, str],
) -> str | None:
    maintained_alias = str(merge_update.get("maintained_alias") or "").strip()
    if maintained_alias:
        resolved = alias_to_entity_id.get(_norm(maintained_alias))
        if resolved:
            return resolved

    explicit_id = str(
        merge_update.get("maintained_entity_instance_id")
        or merge_update.get("maintained_entity_id")
        or ""
    ).strip()
    if explicit_id:
        return explicit_id

    proposal_index_candidates: list[int] = []
    for key in [
        "maintained_proposal_index",
        "merged_into_proposal_index",
    ]:
        raw = merge_update.get(key)
        if isinstance(raw, int):
            proposal_index_candidates.append(raw)

    for key in [
        "maintained_proposal_id",
        "merged_into_proposal_id",
        "maintained_proposal",
        "merged_into_proposal",
    ]:
        parsed = _parse_analysis_entity_index(merge_update.get(key))
        if parsed is not None:
            proposal_index_candidates.append(parsed)

    for proposal_index in proposal_index_candidates:
        mapped = proposal_to_entity_id.get(proposal_index)
        if mapped:
            return mapped

    return None


def _merge_ref_lists(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for ref in [*existing, *incoming]:
        ref_str = str(ref or "").strip()
        if not ref_str:
            continue
        if ref_str not in merged:
            merged.append(ref_str)
    return merged


def _resolve_related_target_entity_id(
    *,
    related: dict[str, Any],
    proposal_to_entity_id: dict[int, str],
    alias_to_entity_id: dict[str, str],
    alias_candidates: list[Any],
    fallback_entity_instance_id: Any,
    valid_entity_ids: set[str],
) -> str | None:
    proposal_index = related.get("proposal_index")
    parsed_index: int | None = None
    if isinstance(proposal_index, int):
        parsed_index = proposal_index
    elif isinstance(proposal_index, str) and proposal_index.strip().isdigit():
        parsed_index = int(proposal_index.strip())

    if parsed_index is not None:
        mapped = proposal_to_entity_id.get(parsed_index)
        if mapped:
            return mapped

    resolved_alias = _resolve_alias(alias_candidates, alias_to_entity_id)
    if resolved_alias:
        return resolved_alias

    fallback = str(fallback_entity_instance_id or "").strip()
    if fallback and fallback in valid_entity_ids:
        return fallback
    return None


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


def _canonicalize_entity_proposals(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for item in raw:
        updates = item.get("updates") or {}
        if not isinstance(updates, dict):
            updates = {}
        effective_name = (
            str(updates.get("name") or item.get("name") or "").strip() or "Unnamed"
        )
        effective_scene_refs = _merge_ref_lists([], item.get("scene_refs") or [])
        effective_ontology = (
            str(updates.get("ontology") or item.get("ontology") or "").strip()
        )
        canonical.append(
            {
                **item,
                "updates": updates,
                "effective_status": _norm(item.get("status")),
                "effective_name": effective_name,
                "effective_scene_refs": effective_scene_refs,
                "effective_ontology": effective_ontology,
                "effective_proposal_type": _effective_proposal_type(item),
                "effective_entity_instance_id": _extract_effective_entity_instance_id(item),
                "effective_definition_id": _extract_effective_definition_id(item),
                "effective_merge": _extract_merge_update(item),
            }
        )
    return canonical


def _canonicalize_scene_proposals(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for item in raw:
        updates = item.get("updates") or {}
        if not isinstance(updates, dict):
            updates = {}
        canonical.append(
            {
                **item,
                "updates": updates,
                "effective_status": _norm(item.get("status")),
                "effective_name": (
                    str(updates.get("name") or item.get("scene_name") or "Scene")
                    if str(updates.get("name") or item.get("scene_name") or "Scene").strip()
                    else "Scene"
                ),
                "effective_related_to": _effective_scene_related({**item, "updates": updates}),
            }
        )
    return canonical


def _canonicalize_milestone_groups(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical_groups: list[dict[str, Any]] = []
    for group in raw:
        milestones = group.get("milestones") or []
        canonical_milestones: list[dict[str, Any]] = []
        for milestone in milestones:
            if not isinstance(milestone, dict):
                continue
            updates = milestone.get("updates") or {}
            if not isinstance(updates, dict):
                updates = {}
            canonical_milestones.append(
                {
                    **milestone,
                    "updates": updates,
                    "effective_status": _norm(milestone.get("status")),
                    "effective_name": (
                        str(milestone.get("title") or milestone.get("label") or "Milestone")
                        if str(milestone.get("title") or milestone.get("label") or "Milestone").strip()
                        else "Milestone"
                    ),
                    "effective_related_to": _effective_milestone_related(
                        {**milestone, "updates": updates}
                    ),
                }
            )
        canonical_groups.append(
            {
                **group,
                "milestones": canonical_milestones,
            }
        )
    return canonical_groups


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


def _proposal_alias_keys(proposal: dict[str, Any]) -> list[str]:
    candidates: list[Any] = []
    updates = proposal.get("updates") or {}
    if isinstance(updates, dict):
        candidates.extend(
            [
                updates.get("name"),
                updates.get("corrected_alias"),
            ]
        )
    candidates.extend(
        [
            proposal.get("effective_name"),
            proposal.get("name"),
            proposal.get("corrected_alias"),
            proposal.get("canonical"),
        ]
    )

    keys: list[str] = []
    for candidate in candidates:
        normalized = _norm(candidate)
        if not normalized:
            continue
        if normalized not in keys:
            keys.append(normalized)
    return keys


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


def _collect_scene_linked_entity_ids(scene_ref_to_entities: dict[str, set[str]]) -> set[str]:
    linked_ids: set[str] = set()
    for entity_ids in scene_ref_to_entities.values():
        linked_ids |= set(entity_ids or set())
    return linked_ids
