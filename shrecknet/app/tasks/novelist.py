"""Celery task for Novelist draft generation (step 1)."""

from __future__ import annotations

import json
import logging
import math
from types import SimpleNamespace
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import LLMModelTarget, get_settings, is_shreckllm_configured
from app.db.session import AsyncSessionMaker
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.runtime_control import fetch_shreckllm_runtime, resolve_provider_default_target
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.graph.neo4j import get_driver
from app.jobs.elder.elder import ElderOrchestrator
from app.jobs.elder.schemas import ElderQueryRequest
from app.jobs.novelist.novelist import NovelistOrchestrator
from app.models.agent import Agent
from app.models.background_job import AuthorType, JobType
from app.models.novelist import NovelistRunStatus, NovelistStage
from app.repositories.agent_repository import AgentRepository
from app.repositories.ontology_repository import OntologyRepository
from app.repositories.novelist_repository import NovelistRepository
from app.schemas.novelist import NovelistRunCreate
from app.tasks.architect_analysis import (
    _build_allowed_ontology_map,
    _format_ontology_definitions_from_entities,
    _load_existing_nodes,
    _run_entity_proposal_phase,
    _run_scene_chunking_phase,
    _run_scene_proposal_phase,
    initialize_architect_concurrency,
)
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


def _build_frontend_llm_usage_summary(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    by_tag = (usage_summary or {}).get("by_tag") if isinstance(usage_summary, dict) else {}
    by_tag = by_tag if isinstance(by_tag, dict) else {}
    totals = (usage_summary or {}).get("totals") if isinstance(usage_summary, dict) else {}
    totals = totals if isinstance(totals, dict) else {}
    return {
        "totals": {
            "calls": int(totals.get("calls") or 0),
            "input_tokens_est": int(totals.get("input_tokens_est") or 0),
            "output_tokens": int(totals.get("output_tokens") or 0),
            "total_tokens": int(totals.get("total_tokens") or 0),
            "estimated_cost_usd": float(totals.get("estimated_cost_usd") or 0.0),
        },
        "by_model": usage_summary.get("by_model") if isinstance(usage_summary, dict) else {},
        "by_tag": by_tag,
    }


def _derive_novelist_runtime_controls(runtime_config: dict[str, Any], provider_id: str) -> dict[str, int]:
    global_max = int(runtime_config.get("max_concurrent_requests") or 0)
    if global_max <= 0:
        raise RuntimeError("Invalid shreckLLM runtime max_concurrent_requests")
    effective_capacity = global_max
    provider_limits = runtime_config.get("provider_limits")
    if isinstance(provider_limits, dict):
        provider_payload = provider_limits.get(provider_id)
        if isinstance(provider_payload, dict):
            provider_max = int(provider_payload.get("max_concurrent") or 0)
            if provider_max > 0:
                effective_capacity = min(effective_capacity, provider_max)
    effective_capacity = max(1, effective_capacity)
    scene_pipeline_max_concurrency = max(1, math.floor(effective_capacity * 0.5))
    scene_pipeline_batch_size = max(1, min(4, scene_pipeline_max_concurrency))
    elder_query_concurrency = 1
    timeout_raw = runtime_config.get("request_timeout_seconds")
    timeout_s = int(float(timeout_raw)) if isinstance(timeout_raw, (int, float)) else 75
    elder_query_timeout_s = min(timeout_s, 75)
    return {
        "scene_pipeline_max_concurrency": scene_pipeline_max_concurrency,
        "scene_pipeline_batch_size": scene_pipeline_batch_size,
        "elder_query_concurrency": elder_query_concurrency,
        "elder_query_timeout_s": elder_query_timeout_s,
        "effective_capacity": effective_capacity,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, LLMModelTarget):
        return value.model_dump()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            return str(value)
    return value


def _clean_optional_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _extract_best_text_from_record(record: Any) -> str:
    if not record:
        return ""
    for key in ("text", "text_linked", "autogenerated_text", "autogenerated_text_linked"):
        candidate = _clean_optional_text(record.get(key))
        if candidate:
            return candidate
    return ""


async def _resolve_previous_session_text(
    previous_session_id: str | None,
) -> tuple[str, str]:
    session_id = _clean_optional_text(previous_session_id)
    if not session_id:
        return "", "missing_id"
    try:
        settings = get_settings()
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as graph_session:
            # 1) Direct match as EntityInstance id.
            entity_result = await graph_session.run(
                """
                MATCH (node:EntityInstance {entity_instance_id: $session_id})
                RETURN node.text AS text,
                       node.text_linked AS text_linked,
                       node.autogenerated_text AS autogenerated_text,
                       node.autogenerated_text_linked AS autogenerated_text_linked
                LIMIT 1
                """,
                session_id=session_id,
            )
            entity_record = await entity_result.single()

            # 2) Match as OntologyInstance id and read candidate entity texts.
            instance_entities_result = await graph_session.run(
                """
                MATCH (:OntologyInstance {instance_id: $session_id})-[:HAS_ENTITY]->(node:EntityInstance)
                RETURN node.text AS text,
                       node.text_linked AS text_linked,
                       node.autogenerated_text AS autogenerated_text,
                       node.autogenerated_text_linked AS autogenerated_text_linked
                ORDER BY size(coalesce(node.text, '')) DESC,
                         size(coalesce(node.autogenerated_text, '')) DESC
                LIMIT 20
                """,
                session_id=session_id,
            )
            instance_entity_records = await instance_entities_result.data()

    except Exception:
        logger.warning(
            "Could not resolve previous session text for session_id=%s",
            session_id,
            exc_info=True,
        )
        return "", "lookup_error"

    entity_text = _extract_best_text_from_record(entity_record)
    if entity_text:
        return entity_text, "matched_entity_instance_id"

    for record in instance_entity_records:
        text = _extract_best_text_from_record(record)
        if text:
            return text, "matched_ontology_instance_entities"

    return "", "no_text_found"


async def _execute_run(
    *,
    run_id: str,
    request_payload: dict[str, Any],
    job_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise RuntimeError("shreckLLM is not configured")
    async with AsyncSessionMaker() as session:
        repo = NovelistRepository(session)
        agent_repo = AgentRepository(session)

        run = await repo.get_run(run_id)
        if not run:
            raise ValueError("Novelist run not found")

        novelist_agent = await agent_repo.get_by_id(run.agent_id)
        if not novelist_agent:
            raise ValueError("Agent not found")

        llm_client = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.shreckllm_request_timeout_s, max_retries=settings.shreckllm_max_retries)
        try:
            await update_job_progress(job_id, 0.05, {"status": "preparing orchestrator"})
            runtime_config = await fetch_shreckllm_runtime(settings)
            configured_novelist_planning_target = settings.model_novelist_planning
            configured_novelist_prose_target = settings.model_novelist_prose
            configured_novelist_critic_target = settings.model_novelist_critic
            configured_architect_target = settings.model_architect_scene_chunking
            configured_repair_json_target = getattr(settings, "model_agents_repair_json", configured_architect_target) or configured_architect_target
            try:
                runtime_default_target = resolve_provider_default_target(
                    runtime_config,
                    configured_novelist_prose_target.provider,
                )
                default_target = LLMModelTarget(
                    provider=configured_novelist_prose_target.provider,
                    name=configured_novelist_prose_target.name or runtime_default_target.name,
                )
            except Exception:
                # Fallback to runtime defaults only when the configured provider is unavailable.
                default_target = resolve_provider_default_target(runtime_config)
            runtime_controls = _derive_novelist_runtime_controls(runtime_config, default_target.provider)
            initialize_architect_concurrency(
                concurrency=runtime_controls["effective_capacity"]
            )
            model_policy = ModelPolicy(
                default_model=default_target,
                architect_extract_model=configured_architect_target,
            )
            # Attach novelist-specific model preferences for orchestrator
            setattr(model_policy, "model_novelist_planning", configured_novelist_planning_target)
            setattr(model_policy, "model_novelist_prose", configured_novelist_prose_target)
            setattr(model_policy, "model_novelist_critic", configured_novelist_critic_target)
            setattr(model_policy, "model_elder", default_target)
            setattr(model_policy, "model_agents_repair_json", configured_repair_json_target)

            async def elder_query_runner(agent: Agent, query: str) -> list[dict[str, Any]]:
                # Elder context is an optional flavor-only layer: never plot authority.
                ontology_ids = [ontology.id for ontology in (agent.ontologies or [])]
                if not ontology_ids:
                    return []
                try:
                    driver = get_driver()
                    async with driver.session(database=settings.neo4j_database) as graph_session:
                        retriever = Neo4jGraphRetriever(graph_session)
                        elder_orchestrator = ElderOrchestrator(
                            llm_client=llm_client,
                            model_policy=model_policy,
                            graph_retriever=retriever,
                            default_top_k=getattr(settings, "default_top_k", 8),
                        )
                        response = await elder_orchestrator.execute(
                            agent,
                            ElderQueryRequest(
                                query=query,
                                mode="context",
                                top_k=6,
                                include_trace=False,
                                fast=True,
                                route="fast",
                            ),
                        )
                except Exception:
                    logger.warning(
                        "Elder lookup failed for novelist enhancement query=%s",
                        query,
                        exc_info=True,
                    )
                    return []
                results: list[dict[str, Any]] = []
                for source in (response.sources or []):
                    if not hasattr(source, "model_dump"):
                        continue
                    payload = source.model_dump()
                    if not str(payload.get("text") or "").strip():
                        # Keep a context-like shape expected by downstream compactors.
                        payload["text"] = "; ".join(
                            [
                                str((chunk or {}).get("text") or "")
                                for chunk in payload.get("evidence_chunks", [])
                                if isinstance(chunk, dict)
                            ]
                        )
                    results.append(payload)
                return results

            async def architect_scaffolding_runner(
                agent: Agent,
                unstructured_text: str,
                instructions: str,
                _conversation_id: str | None,
            ) -> dict[str, Any]:
                ontology_repo = OntologyRepository(session)
                agent_ontology_ids = [ontology.id for ontology in (agent.ontologies or [])]
                ontology_entities: list[Any] = []
                for ontology_id in agent_ontology_ids:
                    ontology_entities.extend(await ontology_repo.list_entities(ontology_id))
                ontology_entities = [
                    definition
                    for definition in ontology_entities
                    if bool(getattr(definition, "auto_generatable", True))
                ]
                allowed_ontology_map = _build_allowed_ontology_map(ontology_entities)
                allowed_ontology_names = {
                    str(value).strip()
                    for value in allowed_ontology_map.values()
                    if str(value).strip()
                }

                ontology_definitions = _format_ontology_definitions_from_entities(
                    ontology_entities
                )

                driver = get_driver()
                async with driver.session(database=settings.neo4j_database) as graph_session:
                    retriever = Neo4jGraphRetriever(graph_session)
                    existing_nodes = await _load_existing_nodes(
                        retriever,
                        agent_ontology_ids,
                    )

                source_entity = SimpleNamespace(
                    entity_instance_id=request_payload.get("previous_session_id") or "novelist-source",
                    alias="novelist-source",
                    text=unstructured_text,
                    autogenerated_text=None,
                )
                pseudo_instance = SimpleNamespace(entities=[source_entity])

                scene_chunking_model = configured_architect_target
                architect_model = configured_architect_target
                chunking_phase = await _run_scene_chunking_phase(
                    run_id=run_id,
                    ontology_instance=pseudo_instance,
                    llm_client=llm_client,
                    model=scene_chunking_model,
                    repair_model=configured_repair_json_target,
                    instructions=instructions,
                )
                entity_phase = await _run_entity_proposal_phase(
                    run_id=run_id,
                    llm_client=llm_client,
                    model=architect_model,
                    repair_model=configured_repair_json_target,
                    ontology_definitions=ontology_definitions,
                    allowed_ontology_names=allowed_ontology_map,
                    existing_nodes=existing_nodes,
                    chunk_results=chunking_phase["chunk_results"],
                    instructions=instructions,
                )
                scene_phase = _run_scene_proposal_phase(
                    run_id=run_id,
                    scene_inputs=entity_phase["scene_inputs"],
                    proposed_entities=entity_phase["proposed_entities"],
                    author_id=agent.id,
                )
                scenes: list[dict[str, Any]] = []
                for order, scene in enumerate(scene_phase.get("proposed_scenes") or [], start=1):
                    scene_ref = str(scene.get("scene_ref") or f"scene_{order}")
                    related_entities = [
                        str(item.get("alias") or item.get("canonical") or "").strip()
                        for item in (scene.get("related_to") or [])
                        if isinstance(item, dict)
                        and str(item.get("ontology") or "").strip() in allowed_ontology_names
                        and str(item.get("alias") or item.get("canonical") or "").strip()
                    ]
                    # Preserve order and de-duplicate aliases.
                    dedup_related: list[str] = []
                    seen_related: set[str] = set()
                    for alias in related_entities:
                        key = alias.lower()
                        if key in seen_related:
                            continue
                        seen_related.add(key)
                        dedup_related.append(alias)

                    scenes.append(
                        {
                            "scene_id": f"scene-{order:03d}",
                            "name": str(scene.get("scene_name") or f"Scene {order}"),
                            "scene_summary": str(scene.get("scene_description") or "").strip(),
                            "raw_scene_text": str(scene.get("scene_text") or "").strip(),
                            "source_paragraphs": [],
                            "source_anchors": [scene_ref],
                            "milestones": [],
                            "related_entities": dedup_related,
                            "new_or_update": "new",
                        }
                    )

                return {
                    "models": {
                        "scene_chunking": scene_chunking_model,
                        "architect": architect_model,
                    },
                    "scene_count": len(scenes),
                    "scene_chunking": {
                        "chunk_count": chunking_phase.get("chunk_count", 0),
                        "scene_count": chunking_phase.get("scene_count", 0),
                        "elapsed_seconds": chunking_phase.get("elapsed_seconds", 0.0),
                    },
                    "entity_phase": {
                        "updated_count": entity_phase.get("updated_count", 0),
                        "new_count": entity_phase.get("new_count", 0),
                        "elapsed_seconds": entity_phase.get("elapsed_seconds", 0.0),
                    },
                    "scenes": scenes,
                }

            orchestrator = NovelistOrchestrator(
                llm_client=llm_client,
                model_policy=model_policy,
                max_concurrency=runtime_controls["scene_pipeline_max_concurrency"],
                scene_pipeline_batch_size=runtime_controls["scene_pipeline_batch_size"],
                elder_query_concurrency=runtime_controls["elder_query_concurrency"],
                elder_query_timeout_s=runtime_controls["elder_query_timeout_s"],
                elder_query_runner=elder_query_runner,
                architect_scaffolding_runner=architect_scaffolding_runner,
            )

            await repo.update_status(
                run_id,
                status=NovelistRunStatus.RUNNING,
                stage=NovelistStage.INGEST,
            )
            await session.commit()

            await update_job_progress(job_id, 0.1, {"status": "preparing sources"})
            previous_session_text, previous_session_lookup_status = await _resolve_previous_session_text(
                request_payload.get("previous_session_id")
            )
            request_payload_enriched = dict(request_payload)
            if previous_session_text:
                request_payload_enriched["previous_session_text"] = previous_session_text

            await repo.update_status(
                run_id,
                artifacts=_json_safe({
                    "inputs": {
                        "previous_session_id": request_payload.get("previous_session_id"),
                        "previous_session_lookup_status": previous_session_lookup_status,
                    }
                }),
            )
            await session.commit()

            stage_progress = {
                NovelistStage.SCAFFOLDING: (0.2, "Building scene scaffolding"),
                NovelistStage.SCENE_PACKAGE: (0.35, "Building scene packages"),
                NovelistStage.RETRIEVAL: (0.5, "Retrieving scene context"),
                NovelistStage.INTENT_DRAFTING: (0.65, "Drafting scene intent"),
                NovelistStage.PROSE_GENERATION: (0.8, "Generating scene prose"),
                NovelistStage.CRITIC: (0.9, "Critic review"),
                NovelistStage.REVISION: (0.95, "Applying targeted revisions"),
                NovelistStage.MERGING: (0.98, "Merging final chapter"),
            }

            async def stage_callback(
                stage: NovelistStage, payload: dict[str, Any] | None = None
            ) -> None:
                payload = payload or {}
                update_kwargs: dict[str, Any] = {"stage": stage}
                if "artifacts" in payload:
                    update_kwargs["artifacts"] = _json_safe(payload["artifacts"])
                if "draft_text" in payload:
                    update_kwargs["draft_text"] = payload["draft_text"]
                if "critic_notes" in payload:
                    critic_notes = payload["critic_notes"]
                    if critic_notes is None:
                        update_kwargs["critic_notes"] = None
                    elif isinstance(critic_notes, str):
                        update_kwargs["critic_notes"] = critic_notes
                    else:
                        update_kwargs["critic_notes"] = json.dumps(
                            critic_notes, ensure_ascii=True
                        )
                await repo.update_status(run_id, **update_kwargs)
                await session.commit()
                progress_info = stage_progress.get(stage)
                if progress_info:
                    progress_value, progress_status = progress_info
                    detail_payload: dict[str, Any] = {"status": progress_status}
                    scene_count = payload.get("scene_count")
                    if isinstance(scene_count, int):
                        detail_payload["scene_count"] = scene_count
                    timing_summary = payload.get("timing_summary")
                    if isinstance(timing_summary, dict):
                        detail_payload["timing_summary"] = timing_summary
                    if isinstance(payload.get("scene_results"), list):
                        detail_payload["scene_results_count"] = len(payload["scene_results"])
                    await update_job_progress(
                        job_id,
                        progress_value,
                        detail_payload,
                    )

            result = await orchestrator.execute(
                agent=novelist_agent,
                payload=NovelistRunCreate.model_validate(request_payload_enriched),
                conversation_id=f"novelist_run:{run_id}",
                stage_callback=stage_callback,
            )
            result_artifacts = result.get("artifacts")
            update_kwargs: dict[str, Any] = {
                "status": NovelistRunStatus.COMPLETED,
                "stage": NovelistStage.DONE,
                "draft_text": result.get("final_text_html"),
                "critic_notes": json.dumps(result.get("critic_remarks", {}), ensure_ascii=True),
            }
            if isinstance(result_artifacts, dict):
                inputs_artifact = result_artifacts.get("inputs")
                if not isinstance(inputs_artifact, dict):
                    inputs_artifact = {}
                inputs_artifact.setdefault(
                    "previous_session_id", request_payload.get("previous_session_id")
                )
                inputs_artifact["previous_session_lookup_status"] = previous_session_lookup_status
                result_artifacts["inputs"] = inputs_artifact
                result["artifacts"] = result_artifacts
                update_kwargs["artifacts"] = _json_safe(result_artifacts)

            usage_summary = llm_client.get_usage_summary()
            llm_usage_for_frontend = _build_frontend_llm_usage_summary(usage_summary)
            if isinstance(result_artifacts, dict):
                result_artifacts["llm_usage_summary"] = llm_usage_for_frontend
                result["artifacts"] = result_artifacts
                update_kwargs["artifacts"] = _json_safe(result_artifacts)
            result["llm_usage_summary"] = llm_usage_for_frontend

            await repo.update_status(run_id, **update_kwargs)
            await session.commit()
            await update_job_progress(
                job_id,
                1.0,
                {"status": "completed", "llm_usage_summary": llm_usage_for_frontend},
            )
            await mark_job_done(
                job_id,
                {
                    "run_id": run_id,
                    "status": "completed",
                    "llm_usage_summary": llm_usage_for_frontend,
                },
            )
            logger.info(
                "novelist_llm_usage run_id=%s totals=%s by_model=%s",
                run_id,
                usage_summary.get("totals"),
                usage_summary.get("by_model"),
            )
            return _json_safe(result)
        except Exception as exc:
            logger.error("Novelist run %s failed: %s", run_id, exc, exc_info=True)
            await session.rollback()
            await repo.update_status(
                run_id,
                status=NovelistRunStatus.FAILED,
                error_message=str(exc),
            )
            await session.commit()
            await mark_job_failed(job_id, str(exc))
            raise
        finally:
            await llm_client.aclose()


@celery_app.task(name="novelist.generate_draft")
def generate_draft(
    run_id: str,
    request_payload: dict[str, Any],
    *,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Entry-point Celery task for novelist draft generation (step 1)."""
    description = f"Novelist draft generation for run {run_id}"
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.NOVELIST_DRAFT,
            description=description,
            celery_task_id=generate_draft.request.id,
            details={"run_id": run_id},
        )
    )

    try:
        run_async(mark_job_running(job_id))

        async def _attach() -> None:
            async with AsyncSessionMaker() as session:
                repo = NovelistRepository(session)
                await repo.attach_background_job(run_id, job_id)
                await session.commit()

        run_async(_attach())
        result = run_async(
            _execute_run(run_id=run_id, request_payload=request_payload, job_id=job_id)
        )
        return _json_safe({"job_id": job_id, "status": "success", "run_id": run_id, **result})
    except Exception as exc:
        run_async(mark_job_failed(job_id, str(exc)))
        raise
