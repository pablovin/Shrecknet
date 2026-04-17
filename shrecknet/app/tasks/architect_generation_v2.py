"""Background task for Architect step 2 (generation) - streamlined v2 pipeline."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from typing import Any, Iterable, Optional, Tuple
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.models.architect import ArchitectProposalStatus, ArchitectProposalType
from app.models.background_job import AuthorType, JobType
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.ontology_repository import OntologyRepository
from app.schemas.architect import RevisedSuggestion
from app.schemas.ontology_instance import (
    OntologyInstanceEntityCreate,
    OntologyInstancePropertyValue,
    OntologyInstanceRelationshipCreate,
)
from app.services.ontology_instance_service import OntologyInstanceService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)

# Regex pattern to remove markdown code block delimiters (e.g., ```html, ```javascript, ```)
MARKDOWN_CODE_BLOCK_PATTERN = r"```[a-zA-Z]*\s*\n?(.*?)\n?```"


def _normalize_alias(alias: Optional[str]) -> str:
    return (alias or "").strip().lower()


def _chunk_text(text: str, *, chunk_size: int, overlap: int) -> Iterable[str]:
    words = text.split()
    total = len(words)
    if total <= chunk_size:
        yield text
        return

    start = 0
    while start < total:
        end = min(start + chunk_size, total)
        yield " ".join(words[start:end])
        if end >= total:
            break
        start = max(0, end - overlap)


@dataclass
class NormalizedSuggestion:
    suggestion_id: str
    mode: str  # "new" or "update"
    alias: str
    entity_definition_id: int
    entity_instance_id: str | None
    chunk_indices: list[int] = field(default_factory=list)
    alias_variants: set[str] = field(default_factory=set)
    merged_from: list[str] = field(default_factory=list)


@celery_app.task(name="architect.generate_entities")
def generate_entities(
    run_id: str,
    revised_suggestions: Optional[list[dict[str, Any]]] = None,
    validated_proposals: Optional[list[dict[str, Any]]] = None,
    *,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Entry point for Architect generation (v2)."""

    description = f"Architect entity generation (v2) for run {run_id}"
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.ARCHITECT_GENERATION,
            description=description,
            celery_task_id=generate_entities.request.id,
            details={
                "run_id": run_id,
                "suggestion_count": len(
                    revised_suggestions or validated_proposals or []
                ),
            },
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(_attach_generation_job_to_run(run_id, job_id))
        run_async(
            update_job_progress(
                job_id, 0.05, {"status": "Preparing architect generation (v2)"}
            )
        )

        result = run_async(
            _execute_generation(
                run_id=run_id,
                revised_suggestions=revised_suggestions or [],
                validated_proposals=validated_proposals or [],
                job_id=job_id,
                author_type=author_type,
                author_id=author_id,
            )
        )

        run_async(
            mark_job_done(
                job_id,
                {
                    "run_id": run_id,
                    "created_entities": len(result.get("created_entity_ids", [])),
                    "updated_entities": len(result.get("updated_entity_ids", [])),
                    "status": "completed",
                },
            )
        )
        return {"job_id": job_id, "status": "success", "run_id": run_id, **result}
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(
            "architect_generation_v2 failed for run %s: %s", run_id, exc, exc_info=True
        )
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
    revised_suggestions: list[dict[str, Any]],
    validated_proposals: list[dict[str, Any]],
    job_id: int,
    author_type: str,
    author_id: str,
) -> dict[str, Any]:
    settings = get_settings()
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI API key not configured")
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        run = await repo.get_run(run_id, with_proposals=False)
        if not run:
            raise ValueError("Architect run not found")

        # Load proposals so we can map suggestion ids back to metadata stored in DB
        all_proposals = await repo.get_proposals_by_run(run_id)
        proposals_by_id = {p.id: p for p in all_proposals}

        suggestions_payload = revised_suggestions

        if not suggestions_payload:
            suggestions_payload = _convert_validated_to_revised(
                validated_proposals, proposals_by_id
            )
        if not suggestions_payload:
            logger.info("No revised suggestions provided for run %s", run_id)
            return {"created_entity_ids": [], "updated_entity_ids": []}

        await update_job_progress(job_id, 0.12, {"status": "Loading ontology data"})

        driver = get_driver()

        async with driver.session(database=settings.neo4j_database) as graph_session:
            instance_service = OntologyInstanceService(session, graph_session)
            ontology_instance = await instance_service.get_instance(
                run.ontology_instance_id
            )

            # Always trust the ontology attached to the actual instance to avoid mismatches.
            ontology_id = ontology_instance.ontology_id
            if not ontology_id:
                raise ValueError("Ontology instance does not specify an ontology")
            if run.ontology_id != ontology_id:
                run.ontology_id = ontology_id
                await session.flush()

            onto_repo = OntologyRepository(session)
            entity_defs = await onto_repo.list_entities(ontology_id)
            entity_definitions_map = _build_entity_definitions_map(entity_defs)
            auto_generatable_definition_ids = {
                entity_def.id
                for entity_def in entity_defs
                if getattr(entity_def, "auto_generatable", False)
            }

            existing_alias_map, existing_entities_map = (
                await _load_existing_entity_catalog(
                    graph_session=graph_session,
                    ontology_id=ontology_id,
                    auto_generatable_definition_ids=auto_generatable_definition_ids,
                )
            )

            original_text = _collect_original_text(ontology_instance)
            language_code, language_name = _detect_language(original_text)
            language_context = {"code": language_code, "label": language_name}

            chunk_size = int((run.settings or {}).get("chunk_size") or 1000)
            chunk_overlap = 100
            max_chunks = (run.settings or {}).get("max_chunks") or None
            chunk_texts = _chunk_instance_text(
                ontology_instance,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                max_chunks=max_chunks,
            )

            normalized_suggestions = _normalize_suggestions(
                suggestions_payload, proposals_by_id, chunk_texts
            )
            if not normalized_suggestions:
                return {"created_entity_ids": [], "updated_entity_ids": []}

            _synchronize_revised_metadata(normalized_suggestions, proposals_by_id)
            suggestion_lookup = {s.suggestion_id: s for s in normalized_suggestions}

            alias_variants = _collect_alias_variants(
                normalized_suggestions, existing_alias_map
            )

            logger.info(
                "architect_generation_v2: detected language %s (%s) for run %s",
                language_context["code"],
                language_context["label"],
                run_id,
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
                api_key=settings.openai_api_key, timeout=60, max_retries=3
            )

            created_entity_ids: list[str] = []
            updated_entity_ids: list[str] = []
            created_entity_definition_map: dict[str, int] = {}
            pending_relationships: list[dict[str, Any]] = []
            generated_instance_ids: list[str] = []
            succeeded_proposal_ids: set[str] = set()
            suggestion_results: list[dict[str, Any]] = []

            try:
                # --- Chunk-first extraction to minimize LLM calls ---
                await update_job_progress(
                    job_id, 0.30, {"status": "Extracting entities per chunk"}
                )
                suggestion_enrichment = await _extract_per_chunk(
                    llm_client=llm_client,
                    model_policy=model_policy,
                    chunk_texts=chunk_texts,
                    suggestions=normalized_suggestions,
                    entity_definitions_map=entity_definitions_map,
                    language_name=language_name,
                )

                await _compose_autogenerated_summaries(
                    llm_client=llm_client,
                    model_policy=model_policy,
                    suggestions=normalized_suggestions,
                    enrichment=suggestion_enrichment,
                    existing_entities_map=existing_entities_map,
                    entity_definitions_map=entity_definitions_map,
                    language_name=language_name,
                )

                # Build create/update payloads from aggregated chunk data
                new_entities_map, new_relationships_map, update_results = (
                    _build_payloads_from_chunk_data(
                        suggestions=normalized_suggestions,
                        enrichment=suggestion_enrichment,
                        entity_definitions=entity_definitions_map,
                        author_type=author_type,
                        author_id=author_id,
                        existing_entities_map=existing_entities_map,
                    )
                )
                timeline_plans = _build_timeline_plans(
                    normalized_suggestions,
                    suggestion_enrichment,
                )
                logger.info(
                    "architect_generation_v2: extracted timeline plans for %d/%d suggestions",
                    len(timeline_plans),
                    len(normalized_suggestions),
                )

                creation_result: dict[str, Any] = {
                    "proposal_to_entity_id": {},
                    "proposal_to_instance_id": {},
                    "instance_ids": [],
                }

                # --- Persist new entities, each in its own sibling ontology instance ---
                if new_entities_map:
                    await update_job_progress(
                        job_id,
                        0.60,
                        {"status": "Creating ontology instances for new entities"},
                    )
                    creation_result = await _create_entities_per_instance(
                        graph_session=graph_session,
                        ontology_id=ontology_id,
                        base_instance=ontology_instance,
                        entities_by_proposal=new_entities_map,
                        definitions=entity_definitions_map,
                        alias_variants=alias_variants,
                    )
                    generated_instance_ids.extend(creation_result["instance_ids"])
                    created_entity_ids.extend(creation_result["created_entity_ids"])
                    created_entity_definition_map.update(
                        creation_result["definition_map"]
                    )
                    for suggestion in normalized_suggestions:
                        entity_id = creation_result["proposal_to_entity_id"].get(
                            suggestion.suggestion_id
                        )
                        if not entity_id:
                            continue
                        succeeded_proposal_ids.add(suggestion.suggestion_id)
                        suggestion_results.append(
                            {
                                "suggestion_id": suggestion.suggestion_id,
                                "action": suggestion.mode,
                                "result": "created",
                                "entity_instance_id": entity_id,
                                "entity_definition_id": suggestion.entity_definition_id,
                            }
                        )
                        # Persist the generated entity id on the proposal for frontend retrieval
                        await repo.update_proposal_generated_entity(
                            suggestion.suggestion_id, entity_id
                        )
                        for variant in suggestion.alias_variants or {
                            _normalize_alias(suggestion.alias)
                        }:
                            if not variant:
                                continue
                            current = alias_variants.get(variant)
                            if current and current.get("entity_instance_id"):
                                continue
                            alias_variants[variant] = {
                                "entity_instance_id": entity_id,
                                "definition_id": suggestion.entity_definition_id,
                            }
                    pending_relationships.extend(
                        _prepare_relationship_payloads(
                            creation_result["proposal_to_entity_id"],
                            new_relationships_map,
                            normalized_suggestions,
                        )
                    )

                # --- Apply updates (properties + relationships) ---
                if update_results:
                    await update_job_progress(
                        job_id, 0.75, {"status": "Updating existing entities"}
                    )
                    updated_ids = await _apply_updates(
                        graph_session=graph_session,
                        update_payloads=update_results,
                        entity_definitions=entity_definitions_map,
                        existing_entities_map=existing_entities_map,
                        alias_variants=alias_variants,
                        created_definitions=created_entity_definition_map,
                    )
                    updated_entity_ids.extend(updated_ids)
                    for payload in update_results:
                        proposal_id = payload.get("proposal_id")
                        entity_id = payload.get("entity_instance_id")
                        if not proposal_id or not entity_id:
                            continue
                        suggestion = suggestion_lookup.get(proposal_id)
                        suggestion_results.append(
                            {
                                "suggestion_id": proposal_id,
                                "action": suggestion.mode if suggestion else "update",
                                "result": "updated",
                                "entity_instance_id": entity_id,
                                "entity_definition_id": (
                                    suggestion.entity_definition_id
                                    if suggestion
                                    else existing_entities_map.get(entity_id, {}).get(
                                        "definition_id"
                                    )
                                ),
                            }
                        )
                    succeeded_proposal_ids.update(
                        {
                            payload["proposal_id"]
                            for payload in update_results
                            if payload.get("proposal_id")
                        }
                    )

                # --- Add relationships after all nodes exist ---
                if pending_relationships:
                    await update_job_progress(
                        job_id, 0.85, {"status": "Creating relationships"}
                    )
                    await _create_relationships(
                        graph_session=graph_session,
                        relationships=pending_relationships,
                        entity_definitions=entity_definitions_map,
                        alias_variants=alias_variants,
                        created_definitions=created_entity_definition_map,
                        existing_entities_map=existing_entities_map,
                    )

                suggestion_instance_map: dict[str, str] = {}
                proposal_to_instance = creation_result.get(
                    "proposal_to_instance_id", {}
                )
                for suggestion in normalized_suggestions:
                    target_instance_id: str | None = None
                    if suggestion.mode == "new":
                        target_instance_id = proposal_to_instance.get(
                            suggestion.suggestion_id
                        )
                    else:
                        existing_info = existing_entities_map.get(
                            suggestion.entity_instance_id or ""
                        )
                        if existing_info:
                            target_instance_id = existing_info.get("instance_id")
                    if target_instance_id:
                        suggestion_instance_map[suggestion.suggestion_id] = (
                            target_instance_id
                        )

                if timeline_plans:
                    await update_job_progress(
                        job_id, 0.88, {"status": "Recording timeline events"}
                    )
                    await _apply_timeline_events(
                        graph_session=graph_session,
                        ontology_id=ontology_id,
                        source_instance_id=getattr(
                            ontology_instance, "instance_id", None
                        ),
                        timeline_plans=timeline_plans,
                        suggestion_lookup=suggestion_lookup,
                        suggestion_instance_map=suggestion_instance_map,
                        new_entity_ids=creation_result.get("proposal_to_entity_id", {}),
                        alias_variants=alias_variants,
                        existing_entities_map=existing_entities_map,
                    )

                merged_ids: set[str] = set()
                for suggestion in normalized_suggestions:
                    merged_ids.update(suggestion.merged_from or [])
                normalized_ids = {s.suggestion_id for s in normalized_suggestions}
                failed_ids = normalized_ids - succeeded_proposal_ids
                remaining_ids = (
                    set(proposals_by_id.keys()) - normalized_ids - merged_ids
                )

                if succeeded_proposal_ids:
                    await repo.update_proposal_states(
                        list(succeeded_proposal_ids),
                        status=ArchitectProposalStatus.APPROVED,
                    )
                if merged_ids:
                    await repo.update_proposal_states(
                        list(merged_ids), status=ArchitectProposalStatus.MERGED
                    )
                combined_rejected = failed_ids | remaining_ids
                if combined_rejected:
                    await repo.update_proposal_states(
                        list(combined_rejected), status=ArchitectProposalStatus.REJECTED
                    )

                await session.commit()
                await update_job_progress(
                    job_id, 0.95, {"status": "Entity generation completed"}
                )

                from app.tasks.ontology_links import link_instance as link_instance_task
                from app.tasks.neo4j_embedding import (
                    embed_ontology as embed_ontology_task,
                )

                if created_entity_ids or updated_entity_ids:
                    for inst_id in generated_instance_ids:
                        link_instance_task.delay(inst_id)
                    link_instance_task.delay(run.ontology_instance_id)
                    embed_ontology_task.delay(
                        ontology_id=ontology_id,
                        author_type=author_type,
                        author_id=author_id,
                    )

                return {
                    "created_entity_ids": created_entity_ids,
                    "updated_entity_ids": updated_entity_ids,
                    "suggestion_results": suggestion_results,
                    "language": language_context,
                }
            finally:
                await llm_client.aclose()


def _convert_validated_to_revised(
    validated: list[dict[str, Any]], proposals: dict[str, Any]
) -> list[dict[str, Any]]:
    """Best-effort compatibility: convert validated proposals into the new suggestion shape."""
    translated: list[dict[str, Any]] = []
    for item in validated:
        proposal_id = item.get("proposal_id")
        status = item.get("status")
        if status not in {
            ArchitectProposalStatus.APPROVED,
            ArchitectProposalStatus.MERGED,
        }:
            continue
        base = proposals.get(proposal_id)
        if not base:
            continue

        base_type = getattr(base, "proposal_type", None)
        if base_type not in {
            ArchitectProposalType.NEW_INSTANCE,
            ArchitectProposalType.UPDATE_INSTANCE,
        }:
            continue

        corrected_type = item.get("corrected_proposal_type")
        if corrected_type and not isinstance(corrected_type, ArchitectProposalType):
            corrected_type = ArchitectProposalType(corrected_type)
        base_corrected_type = getattr(base, "corrected_proposal_type", None)
        base_proposal_type = getattr(base, "proposal_type", None)
        effective_type = corrected_type or base_corrected_type or base_proposal_type

        incoming_corrected_instance_id = item.get("corrected_entity_instance_id")
        stored_corrected_instance_id = getattr(
            base, "corrected_entity_instance_id", None
        )
        corrected_instance_id = incoming_corrected_instance_id
        if corrected_instance_id is None:
            corrected_instance_id = stored_corrected_instance_id
        base_instance_id = getattr(base, "entity_instance_id", None)

        action = "new"
        effective_instance_id: str | None = None
        if status == ArchitectProposalStatus.MERGED:
            action = "merged"
        else:
            should_update = False
            if effective_type == ArchitectProposalType.UPDATE_INSTANCE:
                should_update = True
            elif incoming_corrected_instance_id is not None:
                should_update = True
            elif (
                stored_corrected_instance_id is not None
                and base_corrected_type == ArchitectProposalType.UPDATE_INSTANCE
            ):
                should_update = True

            if should_update:
                action = "updated"
                effective_instance_id = corrected_instance_id or base_instance_id

        translated.append(
            {
                "suggestion_id": proposal_id,
                "action": action,
                "alias": item.get("corrected_alias")
                or base.corrected_alias
                or base.alias,
                "entity_definition_id": item.get("corrected_entity_definition_id")
                or base.corrected_entity_definition_id
                or base.entity_definition_id,
                "entity_instance_id": effective_instance_id,
                "merged_suggestion_ids": (
                    [item.get("merged_into_proposal_id")]
                    if item.get("merged_into_proposal_id")
                    else None
                ),
            }
        )
    return translated


async def _extract_per_chunk(
    *,
    llm_client: OpenAIClient,
    model_policy: ModelPolicy,
    chunk_texts: list[tuple[int, str]],
    suggestions: list[NormalizedSuggestion],
    entity_definitions_map: dict[int, dict[str, Any]],
    language_name: str,
) -> dict[str, dict[str, Any]]:
    """
    Run one LLM call per chunk to extract properties/relationships for all suggestions tied to that chunk.
    Returns a mapping suggestion_id -> aggregated data.
    """
    enrichment: dict[str, dict[str, Any]] = {
        s.suggestion_id: {
            "properties": [],
            "relationships": [],
            "summaries": [],
            "timeline_events": [],
        }
        for s in suggestions
    }
    chunk_map = {idx: text for idx, text in chunk_texts}
    suggestion_by_chunk: dict[int, list[NormalizedSuggestion]] = {}
    for suggestion in suggestions:
        target_indices = suggestion.chunk_indices or list(chunk_map.keys())
        for idx in target_indices:
            if idx not in chunk_map:
                continue
            suggestion_by_chunk.setdefault(idx, []).append(suggestion)

    extract_model = model_policy.get_model(LLMTask.ARCHITECT_EXTRACT)

    for chunk_idx, chunk_suggestions in suggestion_by_chunk.items():
        chunk_text = chunk_map.get(chunk_idx)
        if not chunk_text:
            continue
        target_payload = []
        for s in chunk_suggestions:
            definition = entity_definitions_map.get(s.entity_definition_id)
            if not definition:
                continue
            target_payload.append(
                {
                    "suggestion_id": s.suggestion_id,
                    "alias": s.alias,
                    "entity_definition_id": s.entity_definition_id,
                    "entity_name": definition["name"],
                    "entity_description": definition.get("description") or "",
                    "properties": definition["properties"],
                    "relationships": definition["relationships"],
                    "property_prompts": definition.get("property_prompts", []),
                    "relationship_prompts": definition.get("relationship_prompts", []),
                }
            )
        if not target_payload:
            continue

        prompt = _build_chunk_prompt(
            chunk_text, target_payload, language_name=language_name
        )
        try:
            response = await llm_client.chat(
                model=extract_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"You are an information extraction specialist working in {language_name}. "
                            f"CRITICAL REQUIREMENT: Every piece of generated text MUST be written in {language_name}. "
                            "Return only the requested JSON format."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            parsed = _parse_chunk_response(response)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "architect_generation_v2 chunk %s extraction failed: %s",
                chunk_idx,
                exc,
                exc_info=True,
            )
            parsed = []

        for entry in parsed:
            suggestion_id = entry.get("suggestion_id")
            if suggestion_id not in enrichment:
                continue

            # Strip markdown code blocks from properties if they contain text values
            properties = entry.get("properties", [])
            for prop in properties:
                if isinstance(prop.get("value"), str):
                    prop["value"] = _strip_markdown_code_blocks(prop["value"])
            enrichment[suggestion_id]["properties"].extend(properties)

            # Strip markdown code blocks from relationship justifications
            relationships = entry.get("relationships", [])
            for rel in relationships:
                if isinstance(rel.get("justification"), str):
                    rel["justification"] = _strip_markdown_code_blocks(
                        rel["justification"]
                    )
            enrichment[suggestion_id]["relationships"].extend(relationships)

            summary = entry.get("summary")
            if summary:
                # Strip markdown code blocks from summaries
                enrichment[suggestion_id]["summaries"].append(
                    _strip_markdown_code_blocks(summary)
                )
            raw_events = entry.get("timeline_events", []) or []
            per_chunk_events: list[dict[str, Any]] = []
            for event_index, timeline_event in enumerate(raw_events, start=1):
                normalized_event = _normalize_timeline_event_entry(
                    timeline_event,
                    chunk_index=chunk_idx,
                    fallback_order=event_index,
                )
                if normalized_event:
                    per_chunk_events.append(normalized_event)
            # Do not limit per chunk; limit after merging all chunks
            enrichment[suggestion_id]["timeline_events"].extend(per_chunk_events)

    return enrichment


async def _compose_autogenerated_summaries(
    *,
    llm_client: OpenAIClient,
    model_policy: ModelPolicy,
    suggestions: list[NormalizedSuggestion],
    enrichment: dict[str, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
    entity_definitions_map: dict[int, dict[str, Any]],
    language_name: str,
) -> None:
    """Use the synthesis model to blend existing summaries with new findings."""
    synthesis_model = model_policy.get_model(LLMTask.SYNTHESIS)
    style_model = model_policy.get_model(LLMTask.STYLE)
    for suggestion in suggestions:
        entry = enrichment.get(suggestion.suggestion_id)
        if not entry:
            continue
        raw_notes = [
            note.strip() for note in entry.get("summaries", []) if note and note.strip()
        ]
        existing_summary = ""
        if suggestion.mode == "update" and suggestion.entity_instance_id:
            existing_summary = (
                existing_entities_map.get(suggestion.entity_instance_id, {}).get(
                    "autogenerated_text"
                )
                or ""
            )
        if not raw_notes:
            continue
        if not existing_summary and len(raw_notes) == 1:
            entry["summaries"] = [_format_plaintext_as_html(raw_notes[0])]
            continue

        prompt = _build_summary_prompt(
            alias=suggestion.alias,
            entity_name=entity_definitions_map.get(
                suggestion.entity_definition_id, {}
            ).get("name", ""),
            existing_summary=existing_summary,
            new_points=raw_notes,
            language_name=language_name,
        )
        try:
            response = await llm_client.chat(
                model=synthesis_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"You are an encyclopedia editor working exclusively in {language_name}. "
                            f"CRITICAL: ALL your responses MUST be in {language_name}. "
                            "Combine an existing summary with new findings, emphasizing what changed. "
                            "Return 2-3 concise sentences without repeating ideas verbatim."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
            refined = _strip_markdown_code_blocks(response.strip())
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "architect_generation_v2 summary synthesis failed for suggestion %s: %s",
                suggestion.suggestion_id,
                exc,
            )
            refined = ""
        if not refined:
            refined = " ".join(raw_notes).strip()
        if refined:
            styled = await _maybe_structure_summary_html(
                llm_client=llm_client,
                style_model=style_model,
                summary=refined,
                alias=suggestion.alias,
                entity_name=entity_definitions_map.get(
                    suggestion.entity_definition_id, {}
                ).get("name", ""),
                language_name=language_name,
            )
            entry["summaries"] = [styled]


def _build_summary_prompt(
    alias: str,
    entity_name: str,
    existing_summary: str,
    new_points: list[str],
    language_name: str,
) -> str:
    lines = [
        f"IMPORTANT: Write your response ONLY in {language_name}.",
        f"Entity: {alias or entity_name}",
        f"Type: {entity_name or 'Unknown'}",
    ]
    if existing_summary.strip():
        lines.append("Existing summary:")
        lines.append(existing_summary.strip())
    lines.append("New findings to incorporate:")
    for point in new_points:
        lines.append(f"- {point}")
    lines.append(
        "Write 2-3 sentences that integrate the key new findings while preserving useful context from the existing summary. "
        "Avoid repeating the same idea and prefer clear, narrative tone. "
        f"YOU MUST respond entirely in {language_name}."
    )
    return "\n".join(lines)


async def _maybe_structure_summary_html(
    *,
    llm_client: OpenAIClient,
    style_model: str,
    summary: str,
    alias: str,
    entity_name: str,
    language_name: str,
) -> str:
    stripped = summary.strip()
    if not stripped:
        return ""
    cleaned = _strip_markdown_markup(stripped)
    if _looks_like_html(cleaned):
        return cleaned
    if len(cleaned) < 400:
        return _format_plaintext_as_html(cleaned)
    try:
        response = await llm_client.chat(
            model=style_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are an HTML formatter working exclusively in {language_name}. "
                        f"CRITICAL: You MUST preserve the exact language of the input text ({language_name}). "
                        "If text is long or contains multiple themes, rewrite it as concise HTML with headings and paragraphs. "
                        "Keep tone neutral. DO NOT translate or change the language."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Entity: {alias or entity_name}\n"
                        f"CRITICAL: Keep ALL text in {language_name}. Do NOT translate.\n"
                        "Transform the following into HTML with meaningful headings and short paragraphs. "
                        "Preserve the facts without adding new information and keep the language exactly as-written.\n"
                        f"TEXT:\n{stripped}"
                    ),
                },
            ],
            temperature=0.3,
        )
        candidate = _strip_markdown_code_blocks(response.strip())
        if _looks_like_html(candidate):
            return candidate
        return _format_plaintext_as_html(cleaned)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("architect_generation_v2 summary styling failed: %s", exc)
        return _format_plaintext_as_html(cleaned)


def _looks_like_html(text: str) -> bool:
    candidate = text.strip().lower()
    return bool(candidate) and any(
        tag in candidate
        for tag in ("<p", "<h1", "<h2", "<h3", "<ul", "<ol", "<article", "<section")
    )


def _format_plaintext_as_html(text: str) -> str:
    """Wrap plain text summaries into lightweight semantic HTML."""
    stripped = _strip_markdown_markup(text)
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in stripped.splitlines():
        normalized = line.strip()
        if not normalized:
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            continue
        buffer.append(normalized)
    if buffer:
        paragraphs.append(" ".join(buffer))

    html_parts: list[str] = ["<article>"]
    heading_used = False
    for idx, paragraph in enumerate(paragraphs):
        if idx == 0 and _looks_like_heading(paragraph):
            html_parts.append(f"<h2>{escape(paragraph)}</h2>")
            heading_used = True
            continue
        if _looks_like_heading(paragraph) and heading_used:
            html_parts.append(f"<h3>{escape(paragraph)}</h3>")
            continue
        html_parts.append(f"<p>{escape(paragraph)}</p>")
    if not html_parts[-1].endswith("</article>"):
        html_parts.append("</article>")
    return "".join(html_parts)


def _looks_like_heading(text: str) -> bool:
    """Treat short, punctuation-free phrases as headings."""
    tokens = text.split()
    if len(tokens) == 0 or len(tokens) > 8:
        return False
    if any(ch in text for ch in ".!?"):
        return False
    # avoid pronouns/verbs as headings
    restricted = {"he", "she", "they", "it", "i", "you", "we", "and"}
    return tokens[0][0].isupper() and tokens[0].lower() not in restricted


def _build_chunk_prompt(
    chunk_text: str, targets: list[dict[str, Any]], *, language_name: str
) -> str:
    """Construct an LLM prompt to extract data for multiple entities in one call."""
    lines = [
        f"CRITICAL: You MUST write ALL generated text in {language_name}. This is NON-NEGOTIABLE.",
        "You are the Architect Agent. Read the CHUNK and extract data for each TARGET.",
        "Emit concise JSON only. Respect each relationship contract `source -> relation -> target_type`.",
        "Skip any relationship if the candidate target is not clearly the required target_type.",
        "For every target, also list meaningful timeline events that describe changes or actions affecting that entity.",
        "Timeline events must be written in chronological order and include the involved participants.",
        f"REMINDER: All text in properties, relationships justifications, summaries, and timeline events MUST be in {language_name}.",
    ]
    lines.append('\nCHUNK:\n"""\n' + chunk_text + '\n"""')
    lines.append("\nTARGETS:")
    for t in targets:
        desc = (t.get("entity_description") or "").strip()
        props = t.get("property_prompts") or []
        rels = t.get("relationship_prompts") or []
        prop_text = (
            "\n".join([f"    - {p}" for p in props]) if props else "    - (none)"
        )
        rel_text = "\n".join([f"    - {r}" for r in rels]) if rels else "    - (none)"
        lines.append(
            f"* suggestion_id={t['suggestion_id']}; alias={t['alias']}; entity={t['entity_name']} (def_id={t['entity_definition_id']})"
        )
        if desc:
            lines.append(f"  description: {desc}")
        lines.append("  properties:\n" + prop_text)
        lines.append("  relationships:\n" + rel_text)
    lines.append(
        f"""
Return JSON array (ALL text values MUST be in {language_name}):
[
  {{
    "suggestion_id": "...",
    "properties": [{{"definition_id": 123, "value": "text in {language_name}"}}],
    "relationships": [{{"definition_id": 456, "target_alias": "alias in chunk", "justification": "justification in {language_name}"}}],
    "summary": "2-3 sentence summary in {language_name}",
    "timeline_events": [
      {{
        "title": "event title in {language_name}",
        "description": "description in {language_name}",
        "source_alias": "alias that proves this event (optional)",
        "related_aliases": ["alias_a", "alias_b"],
        "order": 1
      }}
    ]
  }}
]

Rules:
- ALL TEXT MUST BE IN {language_name} - this includes property values, relationship justifications, summaries, timeline titles and descriptions.
- Only extract information present in the chunk.
- One property value per property definition id.
- One relationship per relationship definition id; use target_alias, not ids.
- Skip the relationship if the candidate target alias does not clearly match the required target type.
- If no data for a target, return empty arrays for that target.
- Use the provided entity definitions; do not invent properties or relationships.
- Produce at least one timeline event per target entity. If multiple events exist, assign ascending integer `order` values (1 = earliest in this chunk).
- Timeline events must be output in chronological order and clearly reference any other aliases involved.
"""
    )
    return "\n".join(lines)


def _parse_chunk_response(raw: str) -> list[dict[str, Any]]:
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON array found")
        payload = json.loads(raw[start : end + 1])
        if isinstance(payload, list):
            return payload
    except Exception as exc:  # pragma: no cover - lenient
        logger.warning("architect_generation_v2 parse chunk response failed: %s", exc)
    return []


def _normalize_timeline_event_entry(
    entry: dict[str, Any],
    *,
    chunk_index: int | None = None,
    fallback_order: int | None = None,
) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    title = _strip_markdown_code_blocks((entry.get("title") or "").strip())
    description = _strip_markdown_code_blocks((entry.get("description") or "").strip())
    if not title or not description:
        return None
    source_alias = (entry.get("source_alias") or "").strip() or None
    related_aliases_raw = entry.get("related_aliases")
    related_aliases: list[str] = []
    if isinstance(related_aliases_raw, str):
        related_aliases_raw = [related_aliases_raw]
    if isinstance(related_aliases_raw, list):
        for alias in related_aliases_raw:
            alias_text = (alias or "").strip()
            if alias_text:
                related_aliases.append(alias_text)
    order = entry.get("order")
    try:
        order_value = float(order) if order is not None else None
    except (TypeError, ValueError):
        order_value = None
    chunk_order = fallback_order if fallback_order is not None else order_value
    return {
        "title": title,
        "description": description,
        "source_alias": source_alias,
        "related_aliases": related_aliases,
        "order": order_value,
        "chunk_index": chunk_index,
        "chunk_order": chunk_order,
        "temporal_hint": _detect_temporal_hint(description),
    }


def _build_payloads_from_chunk_data(
    *,
    suggestions: list[NormalizedSuggestion],
    enrichment: dict[str, dict[str, Any]],
    entity_definitions: dict[int, dict[str, Any]],
    author_type: str,
    author_id: str,
    existing_entities_map: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, OntologyInstanceEntityCreate],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
]:
    new_entities: dict[str, OntologyInstanceEntityCreate] = {}
    new_relationships: dict[str, list[dict[str, Any]]] = {}
    updates: list[dict[str, Any]] = []

    author_enum = (
        AuthorType.AGENT
        if (author_type or "").lower() in {"agent", "ai", "assistant"}
        else AuthorType.HUMAN
    )

    for suggestion in suggestions:
        enriched = enrichment.get(suggestion.suggestion_id, {})
        properties = _dedup_properties(enriched.get("properties", []))
        relationships = _dedup_relationships(enriched.get("relationships", []))
        summary = " ".join(enriched.get("summaries", [])).strip()
        if not summary:
            summary = None

        if suggestion.mode == "new":
            new_entity = OntologyInstanceEntityCreate(
                definition_id=suggestion.entity_definition_id,
                alias=suggestion.alias,
                text="",
                autogenerated_text=summary or "",
                author_type=author_enum,
                author_id=author_id,
                properties=properties,
                relationships=[],
            )
            new_entities[suggestion.suggestion_id] = new_entity
            new_relationships[suggestion.suggestion_id] = relationships
        else:
            # update
            if not suggestion.entity_instance_id:
                logger.warning(
                    "architect_generation_v2: skip update suggestion %s missing entity_instance_id",
                    suggestion.suggestion_id,
                )
                continue
            if suggestion.entity_instance_id not in existing_entities_map:
                logger.warning(
                    "architect_generation_v2: skip update suggestion %s unknown entity_instance_id %s",
                    suggestion.suggestion_id,
                    suggestion.entity_instance_id,
                )
                continue
            updates.append(
                {
                    "proposal_id": suggestion.suggestion_id,
                    "entity_instance_id": suggestion.entity_instance_id,
                    "new_properties": properties,
                    "new_relationships": relationships,
                    "updated_autogenerated_summary": summary,
                    "author_type": author_enum.value,
                    "author_id": author_id,
                }
            )

    return new_entities, new_relationships, updates


def _build_timeline_plans(
    suggestions: list[NormalizedSuggestion],
    enrichment: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    plans: dict[str, list[dict[str, Any]]] = {}
    for suggestion in suggestions:
        entry = enrichment.get(suggestion.suggestion_id) or {}
        events = entry.get("timeline_events") or []
        deduped = _limit_chunk_timeline_events(_dedup_timeline_events(events))
        if deduped and len(deduped) <= 3:
            plans[suggestion.suggestion_id] = deduped
            logger.info(
                "architect_generation_v2: suggestion %s (%s) produced %d timeline events",
                suggestion.suggestion_id,
                suggestion.alias,
                len(deduped),
            )
        else:
            logger.warning(
                "architect_generation_v2: no timeline events extracted for suggestion %s (alias=%s)",
                suggestion.suggestion_id,
                suggestion.alias,
            )
    return plans


def _dedup_timeline_events(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    fallback_order = 0
    for event in events or []:
        title = (event.get("title") or "").strip()
        description = (event.get("description") or "").strip()
        if not title or not description:
            continue
        normalized = re.sub(r"\s+", " ", title.lower())
        fallback_order += 1
        order_value = event.get("order")
        if order_value is None or not isinstance(order_value, (int, float)):
            order_value = float(fallback_order)
        chunk_index = event.get("chunk_index")
        chunk_order = event.get("chunk_order")
        if chunk_order is None:
            chunk_order = order_value
        temporal_hint = event.get("temporal_hint") or 0.0
        candidate_payload = {
            "title": title,
            "description": description,
            "source_alias": event.get("source_alias"),
            "related_aliases": event.get("related_aliases") or [],
            "order": order_value,
            "chunk_index": chunk_index,
            "chunk_order": chunk_order,
            "temporal_hint": temporal_hint,
        }
        existing = deduped.get(normalized)
        if existing and _timeline_event_sort_key(existing) <= _timeline_event_sort_key(
            candidate_payload
        ):
            continue
        deduped[normalized] = candidate_payload
    sorted_events = sorted(deduped.values(), key=_timeline_event_sort_key)
    # Enforce min 1, max 3 timeline events per node after merging all chunks
    if len(sorted_events) > 3:
        # Use clustering to select the most representative 3 events
        clustered = _cluster_timeline_events(sorted_events, max_events=3)
        return clustered if clustered else sorted_events[:3]
    return sorted_events


def _timeline_event_sort_key(event: dict[str, Any]) -> tuple[float, float, float, str]:
    chunk_rank = (
        float(event.get("chunk_index"))
        if isinstance(event.get("chunk_index"), (int, float))
        else float("inf")
    )
    primary_order_source = event.get("chunk_order")
    if not isinstance(primary_order_source, (int, float)):
        primary_order_source = event.get("order") or 0
    adjustment = event.get("temporal_hint") or 0.0
    order_value = float(event.get("order") or 0)
    return (
        chunk_rank,
        float(primary_order_source) + adjustment,
        order_value,
        event.get("title", "").lower(),
    )


def _limit_chunk_timeline_events(
    events: list[dict[str, Any]], max_events: int = 3
) -> list[dict[str, Any]]:
    if not events:
        return []
    if len(events) <= max_events:
        return events
    clustered = _cluster_timeline_events(events, max_events=max_events)
    return clustered or events[:max_events]


def _cluster_timeline_events(
    events: list[dict[str, Any]], *, max_events: int = 3
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        key = _event_theme_key(event)
        buckets[key].append(event)
    ordered_buckets = sorted(
        buckets.items(),
        key=lambda item: _timeline_event_sort_key(
            sorted(item[1], key=_timeline_event_sort_key)[0]
        ),
    )
    clustered: list[dict[str, Any]] = []
    for key, bucket in ordered_buckets:
        merged = _combine_event_group(key, bucket)
        clustered.append(merged)
        if len(clustered) >= max_events:
            break
    return clustered


def _event_theme_key(event: dict[str, Any]) -> str:
    basis = f"{event.get('title', '')} {event.get('description', '')}".lower()
    tokens = re.findall(r"[a-z0-9']+", basis)
    keywords = [
        token for token in tokens if len(token) > 3 and token not in _TIMELINE_STOPWORDS
    ]
    if not keywords:
        return "general"
    return " ".join(keywords[:2])


def _combine_event_group(
    theme_key: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    ordered_events = sorted(events, key=_timeline_event_sort_key)
    if len(ordered_events) == 1:
        return ordered_events[0]
    representative = ordered_events[0]
    title = _build_cluster_title(theme_key)
    description_parts = [
        f"{event['title']}: {event['description']}" for event in ordered_events
    ]
    merged_aliases = []
    seen: set[str] = set()
    for event in ordered_events:
        for alias in event.get("related_aliases") or []:
            if alias not in seen:
                seen.add(alias)
                merged_aliases.append(alias)
    return {
        "title": title,
        "description": " ".join(description_parts),
        "source_alias": representative.get("source_alias"),
        "related_aliases": merged_aliases,
        "order": representative.get("order"),
        "chunk_index": representative.get("chunk_index"),
        "chunk_order": representative.get("chunk_order"),
        "temporal_hint": representative.get("temporal_hint"),
    }


def _build_cluster_title(theme_key: str) -> str:
    if theme_key == "general":
        return "Key Developments"
    words = [word.capitalize() for word in theme_key.split()]
    return f"{' '.join(words)} Event"


def _detect_temporal_hint(description: str) -> float:
    lowered = description.lower()
    if any(token in lowered for token in _BEFORE_HINTS):
        return -0.5
    if any(token in lowered for token in _AFTER_HINTS):
        return 0.5
    return 0.0


def _build_entity_definitions_map(
    entity_defs: Iterable[Any],
) -> dict[int, dict[str, Any]]:
    definitions: dict[int, dict[str, Any]] = {}
    for entity_def in entity_defs:
        if not getattr(entity_def, "auto_generatable", False):
            continue
        properties = []
        property_prompts: list[str] = []
        for prop in entity_def.properties:
            if not prop.auto_generatable:
                continue
            prop_payload = {
                "id": prop.id,
                "name": prop.name,
                "description": prop.description,
                "data_type": prop.data_type.value,
                "cardinality": prop.cardinality.value,
            }
            properties.append(prop_payload)
            detail = prop.description or ""
            prompt = (
                f"[{prop.id}] {entity_def.name} -> {prop.name} "
                f"({prop.data_type.value}, {prop.cardinality.value})"
            )
            if detail:
                prompt = f"{prompt} - {detail}"
            property_prompts.append(prompt.strip())
        relationships = [
            {
                "id": rel.id,
                "name": rel.name,
                "description": rel.description,
                "destiny_entity_id": rel.destiny_entity_id,
                "destiny_entity_name": (
                    rel.destiny_entity.name if rel.destiny_entity else None
                ),
                "bi_directional": rel.bi_directional,
            }
            for rel in entity_def.relationships
            if rel.auto_generatable
        ]
        relationship_prompts: list[str] = []
        for rel in entity_def.relationships:
            if not rel.auto_generatable:
                continue
            target_name = (
                rel.destiny_entity.name if rel.destiny_entity else "target entity"
            )
            prompt = f"[{rel.id}] {entity_def.name} -> {rel.name} -> {target_name}"
            if rel.description:
                prompt = f"{prompt} - {rel.description}"
            relationship_prompts.append(prompt.strip())
        rel_map = {rel["id"]: rel for rel in relationships}
        definitions[entity_def.id] = {
            "id": entity_def.id,
            "name": entity_def.name,
            "description": entity_def.description,
            "properties": properties,
            "relationships": relationships,
            "relationships_by_id": rel_map,
            "property_prompts": property_prompts,
            "relationship_prompts": relationship_prompts,
        }
    return definitions


def _collect_original_text(ontology_instance: Any) -> str:
    parts: list[str] = []
    for entity in ontology_instance.entities:
        if entity.text:
            parts.append(entity.text)
        if entity.autogenerated_text:
            parts.append(entity.autogenerated_text)
    return "\n\n".join(parts)


def _detect_language(text: str) -> Tuple[str, str]:
    """Detect the dominant language from the source text and return code + readable name."""
    normalized = (text or "").strip()
    if not normalized:
        return "en", _LANGUAGE_LABELS.get("en", "English")
    normalized_lower = normalized.lower()
    # Prefer langdetect if available, fallback to light heuristics.
    try:  # pragma: no cover - optional dependency
        from langdetect import detect  # type: ignore

        detected_code = detect(normalized)
        if detected_code:
            # Get the language name from our dictionary, or construct a readable name from the code
            language_name = _LANGUAGE_LABELS.get(
                detected_code, f"{detected_code.upper()} (detected language)"
            )
            return detected_code, language_name
    except Exception:  # pragma: no cover - lenient fallback
        pass

    if _looks_portuguese(normalized_lower):
        return "pt", _LANGUAGE_LABELS.get("pt", "Portuguese")

    return "en", _LANGUAGE_LABELS.get("en", "English")


def _looks_portuguese(text: str) -> bool:
    accented = sum(ch in "ãõáéíóúâêôç" for ch in text)
    total = max(len(text), 1)
    if accented / total > 0.01:
        return True
    hits = sum(1 for word in _PORTUGUESE_HINT_WORDS if f" {word} " in f" {text} ")
    return hits >= 3


_LANGUAGE_LABELS = {
    # Common languages - expand as needed for your use case
    # For comprehensive support, consider integrating babel or langcodes library
    "en": "English",
    "pt": "Portuguese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "nl": "Dutch",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "fi": "Finnish",
    "cs": "Czech",
    "el": "Greek",
    "he": "Hebrew",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "hu": "Hungarian",
    "sk": "Slovak",
}

_PORTUGUESE_HINT_WORDS = {
    "não",
    "que",
    "para",
    "com",
    "mais",
    "como",
    "quando",
    "sobre",
    "entre",
    "também",
    "porque",
    "ainda",
}

_TIMELINE_STOPWORDS = {
    "the",
    "and",
    "with",
    "into",
    "from",
    "that",
    "this",
    "have",
    "has",
    "had",
    "were",
    "been",
    "after",
    "before",
    "onto",
    "over",
    "under",
    "upon",
    "amid",
    "amidst",
    "about",
    "event",
}

_BEFORE_HINTS = {
    "before",
    "earlier",
    "prior to",
    "previously",
    "ahead of",
    "leading up",
}
_AFTER_HINTS = {
    "after",
    "later",
    "subsequently",
    "eventually",
    "afterward",
    "following",
}


def _chunk_instance_text(
    ontology_instance: Any,
    *,
    chunk_size: int,
    chunk_overlap: int,
    max_chunks: Optional[int],
) -> list[tuple[int, str]]:
    """Chunk the source instance using the same strategy from the analysis step."""
    chunks: list[tuple[int, str]] = []
    index = 0
    for entity in ontology_instance.entities:
        text_parts: list[str] = []
        if entity.text:
            text_parts.append(entity.text)
        if entity.autogenerated_text:
            text_parts.append(entity.autogenerated_text)
        joined = "\n\n".join([t.strip() for t in text_parts if t and t.strip()])
        if not joined:
            continue
        for chunk_text in _chunk_text(
            joined, chunk_size=chunk_size, overlap=chunk_overlap
        ):
            chunks.append((index, chunk_text))
            index += 1
            if max_chunks and len(chunks) >= max_chunks:
                return chunks
    return chunks


def _normalize_suggestions(
    suggestions: list[dict[str, Any]],
    proposals_by_id: dict[str, Any],
    chunk_texts: list[tuple[int, str]],
) -> list[NormalizedSuggestion]:
    chunk_map = {idx: text for idx, text in chunk_texts}
    normalized: list[NormalizedSuggestion] = []
    for raw in suggestions:
        try:
            suggestion = RevisedSuggestion.model_validate(raw)
        except Exception as exc:
            logger.warning("Skipping invalid suggestion payload %s: %s", raw, exc)
            continue

        # Only honor approved/merged suggestions when status is provided
        if suggestion.status and suggestion.status not in {
            ArchitectProposalStatus.APPROVED,
            ArchitectProposalStatus.MERGED,
        }:
            continue

        base = proposals_by_id.get(suggestion.suggestion_id)
        alias_candidates = [
            suggestion.alias,
            getattr(base, "corrected_alias", None),
            getattr(base, "alias", None),
        ]
        merged_aliases = suggestion.merged_aliases or []
        if suggestion.merged_suggestion_ids:
            for merged_id in suggestion.merged_suggestion_ids:
                merged_base = proposals_by_id.get(merged_id)
                if merged_base and merged_base.alias:
                    merged_aliases.append(merged_base.alias)
        alias_candidates.extend(merged_aliases)
        alias = next(
            (a for a in alias_candidates if a), f"suggestion-{suggestion.suggestion_id}"
        )

        chunk_indices = list(suggestion.chunk_indices or [])
        if not chunk_indices and getattr(base, "proposal_metadata", None):
            meta_indices = (base.proposal_metadata or {}).get("chunk_indices") or []
            chunk_indices.extend(meta_indices)

        entity_definition_id = (
            suggestion.entity_definition_id
            or getattr(base, "corrected_entity_definition_id", None)
            or getattr(base, "entity_definition_id", None)
        )
        if entity_definition_id is None:
            logger.warning(
                "Suggestion %s missing entity_definition_id, skipping",
                suggestion.suggestion_id,
            )
            continue

        entity_instance_id = suggestion.entity_instance_id or getattr(
            base, "entity_instance_id", None
        )
        mode = "update" if suggestion.action == "updated" else "new"
        if mode == "update" and not entity_instance_id:
            logger.warning(
                "Suggestion %s marked updated but missing entity_instance_id",
                suggestion.suggestion_id,
            )
            continue

        normalized.append(
            NormalizedSuggestion(
                suggestion_id=suggestion.suggestion_id,
                mode=mode,
                alias=alias,
                entity_definition_id=entity_definition_id,
                entity_instance_id=entity_instance_id,
                chunk_indices=[idx for idx in chunk_indices if idx in chunk_map],
                alias_variants={_normalize_alias(a) for a in alias_candidates if a},
                merged_from=suggestion.merged_suggestion_ids or [],
            )
        )
    return normalized


def _synchronize_revised_metadata(
    suggestions: list[NormalizedSuggestion], proposals_by_id: dict[str, Any]
) -> None:
    for suggestion in suggestions:
        proposal = proposals_by_id.get(suggestion.suggestion_id)
        if not proposal:
            continue
        proposal.corrected_alias = suggestion.alias
        proposal.corrected_entity_definition_id = suggestion.entity_definition_id
        proposal.corrected_entity_instance_id = (
            suggestion.entity_instance_id if suggestion.mode == "update" else None
        )
        proposal.corrected_proposal_type = (
            ArchitectProposalType.UPDATE_INSTANCE
            if suggestion.mode == "update"
            else ArchitectProposalType.NEW_INSTANCE
        )


def _collect_alias_variants(
    normalized_suggestions: list[NormalizedSuggestion],
    existing_alias_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    alias_variants = {**existing_alias_map}
    for suggestion in normalized_suggestions:
        normalized_aliases = suggestion.alias_variants or {
            _normalize_alias(suggestion.alias)
        }
        for alias in normalized_aliases:
            if alias and alias not in alias_variants:
                alias_variants[alias] = {
                    "entity_instance_id": None,
                    "definition_id": suggestion.entity_definition_id,
                    "suggestion_id": suggestion.suggestion_id,
                }
    return alias_variants


def _build_generator_payloads(
    normalized: list[NormalizedSuggestion],
    chunk_texts: list[tuple[int, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    chunk_map = {idx: text for idx, text in chunk_texts}
    new_payloads: list[dict[str, Any]] = []
    update_payloads: list[dict[str, Any]] = []
    for suggestion in normalized:
        chunks = (
            [chunk_map[idx] for idx in suggestion.chunk_indices]
            if suggestion.chunk_indices
            else list(chunk_map.values())
        )
        proposal_dict = {
            "id": suggestion.suggestion_id,
            "proposal_type": (
                ArchitectProposalType.NEW_INSTANCE.value
                if suggestion.mode == "new"
                else ArchitectProposalType.UPDATE_INSTANCE.value
            ),
            "entity_definition_id": suggestion.entity_definition_id,
            "entity_instance_id": suggestion.entity_instance_id,
            "alias": suggestion.alias,
            "chunks": chunks,
        }
        if suggestion.mode == "new":
            new_payloads.append(proposal_dict)
        else:
            update_payloads.append(proposal_dict)
    return new_payloads, update_payloads


def _dedup_properties(
    props: Iterable[OntologyInstancePropertyValue] | Iterable[dict[str, Any]],
) -> list[OntologyInstancePropertyValue]:
    deduped: dict[int, OntologyInstancePropertyValue] = {}
    for prop in props:
        if isinstance(prop, OntologyInstancePropertyValue):
            definition_id = prop.definition_id
            value = prop.value
        else:
            definition_id = prop.get("definition_id")
            value = prop.get("value")
        if definition_id is None or definition_id in deduped:
            continue
        deduped[int(definition_id)] = OntologyInstancePropertyValue(
            definition_id=int(definition_id), value=value
        )
    return list(deduped.values())


def _dedup_relationships(
    relationships: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: dict[tuple[int, str], dict[str, Any]] = {}
    for rel in relationships or []:
        definition_id = rel.get("definition_id")
        target_alias = _normalize_alias(rel.get("target_alias"))
        target_id = rel.get("target_entity_instance_id")
        key = (
            int(definition_id) if definition_id is not None else None,
            target_alias or target_id or "",
        )
        if key[0] is None or key in deduped:
            continue
        deduped[key] = {
            "definition_id": int(definition_id),
            "target_alias": rel.get("target_alias"),
            "target_entity_instance_id": target_id,
            "justification": rel.get("justification"),
        }
    return list(deduped.values())


async def _create_entities_on_instance(
    *,
    graph_session: Any,
    instance_id: str,
    ontology_id: int,
    entities_by_proposal: dict[str, OntologyInstanceEntityCreate],
    definitions: dict[int, dict[str, Any]],
    alias_variants: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    timestamp = datetime.utcnow().isoformat() + "Z"
    created_ids: list[str] = []
    definition_map: dict[str, int] = {}
    proposal_to_entity_id: dict[str, str] = {}

    alias_to_node: dict[str, str] = {}
    for proposal_id, entity_payload in entities_by_proposal.items():
        node_id = str(uuid4())
        alias_to_node[_normalize_alias(entity_payload.alias)] = node_id
        proposal_to_entity_id[proposal_id] = node_id

    for proposal_id, entity_payload in entities_by_proposal.items():
        node_id = proposal_to_entity_id[proposal_id]
        prop_json = json.dumps(
            {str(prop.definition_id): prop.value for prop in entity_payload.properties}
        )
        create_result = await graph_session.run(
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
            ontology_id=ontology_id,
            entity_instance_id=node_id,
            entity_definition_id=entity_payload.definition_id,
            properties=prop_json,
            text=entity_payload.text,
            node_avatar_url=entity_payload.node_avatar_url,
            autogenerated_text=entity_payload.autogenerated_text,
            text_linked=entity_payload.text,
            autogenerated_text_linked=entity_payload.autogenerated_text,
            created_date=timestamp,
            last_updated_date=timestamp,
            author_type=entity_payload.author_type.value,
            author_id=entity_payload.author_id,
            created_at=timestamp,
            updated_at=timestamp,
            alias=entity_payload.alias,
        )
        # Consume the result to ensure the write is fully committed
        await create_result.consume()
        created_ids.append(node_id)
        definition_map[node_id] = entity_payload.definition_id

        # register alias variants for relationship resolution
        normalized_alias = _normalize_alias(entity_payload.alias)
        alias_variants[normalized_alias] = {
            "entity_instance_id": node_id,
            "definition_id": entity_payload.definition_id,
        }

    return {
        "created_entity_ids": created_ids,
        "definition_map": definition_map,
        "proposal_to_entity_id": proposal_to_entity_id,
    }


async def _create_entities_per_instance(
    *,
    graph_session: Any,
    ontology_id: int,
    base_instance: Any,
    entities_by_proposal: dict[str, OntologyInstanceEntityCreate],
    definitions: dict[int, dict[str, Any]],
    alias_variants: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    created_entity_ids: list[str] = []
    definition_map: dict[str, int] = {}
    proposal_to_entity_id: dict[str, str] = {}
    instance_ids: list[str] = []
    proposal_to_instance_id: dict[str, str] = {}

    for proposal_id, entity_payload in entities_by_proposal.items():
        new_instance_id = str(uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        name = entity_payload.alias or base_instance.name or "Generated Instance"
        create_result = await graph_session.run(
            """
            CREATE (i:OntologyInstance {
                instance_id: $instance_id,
                ontology_id: $ontology_id,
                name: $name,
                created_at: $created_at,
                updated_at: $updated_at
            })
            """,
            instance_id=new_instance_id,
            ontology_id=ontology_id,
            name=name[:255],
            created_at=now,
            updated_at=now,
        )
        # Consume the result to ensure the OntologyInstance is fully committed
        # before creating entities that reference it
        await create_result.consume()
        instance_ids.append(new_instance_id)

        # Create the entity under this new instance
        res = await _create_entities_on_instance(
            graph_session=graph_session,
            instance_id=new_instance_id,
            ontology_id=ontology_id,
            entities_by_proposal={proposal_id: entity_payload},
            definitions=definitions,
            alias_variants=alias_variants,
        )
        created_entity_ids.extend(res["created_entity_ids"])
        definition_map.update(res["definition_map"])
        proposal_to_entity_id.update(res["proposal_to_entity_id"])
        proposal_to_instance_id[proposal_id] = new_instance_id

        # Update instance updated_at
        update_result = await graph_session.run(
            """
            MATCH (i:OntologyInstance {instance_id: $instance_id})
            SET i.updated_at = datetime()
            """,
            instance_id=new_instance_id,
        )
        await update_result.consume()

    return {
        "created_entity_ids": created_entity_ids,
        "definition_map": definition_map,
        "proposal_to_entity_id": proposal_to_entity_id,
        "proposal_to_instance_id": proposal_to_instance_id,
        "instance_ids": instance_ids,
    }


def _prepare_relationship_payloads(
    proposal_to_entity_id: dict[str, str],
    relationships_map: dict[str, list[dict[str, Any]]],
    normalized_suggestions: list[NormalizedSuggestion],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    suggestion_lookup = {s.suggestion_id: s for s in normalized_suggestions}
    for proposal_id, rels in relationships_map.items():
        source_id = proposal_to_entity_id.get(proposal_id)
        if not source_id:
            continue
        suggestion = suggestion_lookup.get(proposal_id)
        alias_variants = suggestion.alias_variants if suggestion else set()
        result.append(
            {
                "source_entity_id": source_id,
                "definition_id": (
                    suggestion.entity_definition_id if suggestion else None
                ),
                "relationships": rels,
                "alias_variants": alias_variants,
            }
        )
    return result


async def _apply_updates(
    *,
    graph_session: Any,
    update_payloads: list[dict[str, Any]],
    entity_definitions: dict[int, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
    alias_variants: dict[str, dict[str, Any]],
    created_definitions: dict[str, int],
) -> list[str]:
    updated_entities: list[str] = []
    relationship_keys: set[tuple[str, int, str]] = set()

    for update in update_payloads:
        entity_id = update.get("entity_instance_id")
        if not entity_id:
            continue
        updated_entities.append(entity_id)

        # update autogenerated summary
        if update.get("updated_autogenerated_summary"):
            await graph_session.run(
                """
                MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                SET e.autogenerated_text = $text,
                    e.autogenerated_text_linked = $text,
                    e.last_updated_date = datetime(),
                    e.author_type = $author_type,
                    e.author_id = $author_id
                """,
                entity_id=entity_id,
                text=update["updated_autogenerated_summary"],
                author_type=update.get("author_type"),
                author_id=update.get("author_id"),
            )

        # add properties
        for prop in update.get("new_properties", []):
            result = await graph_session.run(
                """
                MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                RETURN e.properties as props
                """,
                entity_id=entity_id,
            )
            record = await result.single()
            props = json.loads(record["props"] or "{}") if record else {}
            props[str(prop.definition_id)] = prop.value
            await graph_session.run(
                """
                MATCH (e:EntityInstance {entity_instance_id: $entity_id})
                SET e.properties = $props
                """,
                entity_id=entity_id,
                props=json.dumps(props),
            )

        # add relationships
        entity_definition_id = existing_entities_map.get(entity_id, {}).get(
            "definition_id"
        )
        rel_defs = entity_definitions.get(entity_definition_id, {}).get(
            "relationships_by_id", {}
        )
        for rel in update.get("new_relationships", []):
            rel_def = rel_defs.get(rel.get("definition_id"))
            if not rel_def:
                continue
            target_id = rel.get("target_entity_instance_id")
            if not target_id and rel.get("target_alias"):
                resolved = alias_variants.get(_normalize_alias(rel["target_alias"]))
                target_id = resolved.get("entity_instance_id") if resolved else None
            if not target_id:
                continue
            target_definition_id = created_definitions.get(
                target_id
            ) or existing_entities_map.get(target_id, {}).get("definition_id")
            if rel_def.get("destiny_entity_id") and target_definition_id:
                if rel_def["destiny_entity_id"] != target_definition_id:
                    continue
            key = (entity_id, rel_def["id"], target_id)
            if key in relationship_keys:
                continue
            relationship_keys.add(key)
            rel_data = json.dumps({"justification": rel.get("justification") or ""})
            await graph_session.run(
                """
                MATCH (source:EntityInstance {entity_instance_id: $source_id})
                MATCH (target:EntityInstance {entity_instance_id: $target_id})
                CREATE (source)-[:RELATES_TO {
                    relationship_instance_id: $rel_id,
                    relationship_definition_id: $rel_def_id,
                    destiny_entity_definition_id: $destiny_id,
                    data: $data,
                    created_at: datetime(),
                    updated_at: datetime()
                }]->(target)
                """,
                source_id=entity_id,
                target_id=target_id,
                rel_id=str(uuid4()),
                rel_def_id=rel_def["id"],
                destiny_id=target_definition_id,
                data=rel_data,
            )
            if rel_def.get("bi_directional"):
                reverse_key = (target_id, rel_def["id"], entity_id)
                if reverse_key not in relationship_keys:
                    relationship_keys.add(reverse_key)
                    await graph_session.run(
                        """
                        MATCH (source:EntityInstance {entity_instance_id: $source_id})
                        MATCH (target:EntityInstance {entity_instance_id: $target_id})
                        CREATE (source)-[:RELATES_TO {
                            relationship_instance_id: $rel_id,
                            relationship_definition_id: $rel_def_id,
                            destiny_entity_definition_id: $destiny_id,
                            data: $data,
                            created_at: datetime(),
                            updated_at: datetime()
                        }]->(target)
                        """,
                        source_id=target_id,
                        target_id=entity_id,
                        rel_id=str(uuid4()),
                        rel_def_id=rel_def["id"],
                        destiny_id=entity_definition_id,
                        data=rel_data,
                    )
    return updated_entities


async def _create_relationships(
    *,
    graph_session: Any,
    relationships: list[dict[str, Any]],
    entity_definitions: dict[int, dict[str, Any]],
    alias_variants: dict[str, dict[str, Any]],
    created_definitions: dict[str, int],
    existing_entities_map: dict[str, dict[str, Any]],
) -> None:
    relationship_keys: set[tuple[str, int, str]] = set()
    for rel_group in relationships:
        source_id = rel_group.get("source_entity_id")
        source_def_id = (
            rel_group.get("definition_id")
            or created_definitions.get(source_id)
            or existing_entities_map.get(source_id, {}).get("definition_id")
        )
        rel_defs = entity_definitions.get(source_def_id, {}).get(
            "relationships_by_id", {}
        )
        for rel in rel_group.get("relationships", []):
            rel_def = rel_defs.get(rel.get("definition_id"))
            if not rel_def:
                continue
            target_id = rel.get("target_entity_instance_id")
            if not target_id and rel.get("target_alias"):
                resolved = alias_variants.get(_normalize_alias(rel["target_alias"]))
                target_id = resolved.get("entity_instance_id") if resolved else None
            if not target_id:
                continue
            target_def_id = created_definitions.get(
                target_id
            ) or existing_entities_map.get(target_id, {}).get("definition_id")
            destiny_id = rel_def.get("destiny_entity_id")
            if destiny_id and target_def_id and destiny_id != target_def_id:
                # ensure we don't link to the wrong entity type
                continue

            key = (source_id, rel_def["id"], target_id)
            if key in relationship_keys:
                continue
            relationship_keys.add(key)
            data = json.dumps({"justification": rel.get("justification") or ""})
            await graph_session.run(
                """
                MATCH (source:EntityInstance {entity_instance_id: $source_id})
                MATCH (target:EntityInstance {entity_instance_id: $target_id})
                CREATE (source)-[:RELATES_TO {
                    relationship_instance_id: $rel_id,
                    relationship_definition_id: $rel_def_id,
                    destiny_entity_definition_id: $destiny_id,
                    data: $data,
                    created_at: datetime(),
                    updated_at: datetime()
                }]->(target)
                """,
                source_id=source_id,
                target_id=target_id,
                rel_id=str(uuid4()),
                rel_def_id=rel_def["id"],
                destiny_id=destiny_id or target_def_id,
                data=data,
            )
            if rel_def.get("bi_directional"):
                reverse_key = (target_id, rel_def["id"], source_id)
                if reverse_key not in relationship_keys:
                    relationship_keys.add(reverse_key)
                    await graph_session.run(
                        """
                        MATCH (source:EntityInstance {entity_instance_id: $source_id})
                        MATCH (target:EntityInstance {entity_instance_id: $target_id})
                        CREATE (source)-[:RELATES_TO {
                            relationship_instance_id: $rel_id,
                            relationship_definition_id: $rel_def_id,
                            destiny_entity_definition_id: $destiny_id,
                            data: $data,
                            created_at: datetime(),
                            updated_at: datetime()
                        }]->(target)
                        """,
                        source_id=target_id,
                        target_id=source_id,
                        rel_id=str(uuid4()),
                        rel_def_id=rel_def["id"],
                        destiny_id=source_def_id,
                        data=data,
                    )


async def _load_existing_entity_catalog(
    *,
    graph_session: Any,
    ontology_id: int,
    auto_generatable_definition_ids: set[int],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    query = """
        MATCH (inst:OntologyInstance {ontology_id: $ontology_id})-[:HAS_ENTITY]->(e:EntityInstance)
        OPTIONAL MATCH (e)-[r:RELATES_TO]->(target:EntityInstance)
        RETURN e.entity_instance_id AS entity_instance_id,
               e.entity_definition_id AS definition_id,
               e.alias AS alias,
               e.text AS text,
               e.autogenerated_text AS autogenerated_text,
               e.properties AS properties,
               inst.instance_id AS instance_id,
               collect(
                   CASE
                       WHEN r IS NULL THEN NULL
                       ELSE {
                           relationship_instance_id: r.relationship_instance_id,
                           definition_id: r.relationship_definition_id,
                           target_entity_id: target.entity_instance_id,
                           destiny_entity_definition_id: r.destiny_entity_definition_id,
                           data: r.data
                       }
                   END
               ) AS relationships
    """
    result = await graph_session.run(query, {"ontology_id": ontology_id})
    records = await result.data()

    alias_map: dict[str, dict[str, Any]] = {}
    entities_map: dict[str, dict[str, Any]] = {}
    for record in records:
        entity_id = record.get("entity_instance_id")
        if not entity_id:
            continue
        definition_id = record.get("definition_id")
        alias = record.get("alias")
        normalized = _normalize_alias(alias)
        if normalized and definition_id in auto_generatable_definition_ids:
            alias_map[normalized] = {
                "entity_instance_id": entity_id,
                "definition_id": definition_id,
            }

        properties_payload = record.get("properties") or {}
        if isinstance(properties_payload, str):
            try:
                properties_payload = json.loads(properties_payload)
            except json.JSONDecodeError:
                properties_payload = {}

        properties = [
            {"definition_id": int(prop_id), "value": value}
            for prop_id, value in (properties_payload or {}).items()
        ]
        relationships = [
            rel
            for rel in (record.get("relationships") or [])
            if rel and rel.get("target_entity_id")
        ]

        entities_map[entity_id] = {
            "alias": alias,
            "definition_id": definition_id,
            "properties": properties,
            "relationships": relationships,
            "text": record.get("text") or "",
            "autogenerated_text": record.get("autogenerated_text") or "",
            "instance_id": record.get("instance_id"),
        }

    return alias_map, entities_map


async def _fetch_instance_timeline_events(
    graph_session: Any, instance_id: str
) -> list[dict[str, Any]]:
    result = await graph_session.run(
        """
        MATCH (:OntologyInstance {instance_id: $instance_id})-[:HAS_EVENT]->(event:Event)
        RETURN event
        """,
        {"instance_id": instance_id},
    )
    records = await result.data()
    events: list[dict[str, Any]] = []
    for record in records:
        event_node = record.get("event")
        if not event_node:
            continue
        props = dict(event_node)
        created_at = props.get("created_at")
        if created_at is not None:
            try:
                created_at_value = created_at.isoformat()  # type: ignore[union-attr]
            except AttributeError:
                created_at_value = str(created_at)
        else:
            created_at_value = None
        events.append(
            {
                "event_id": props.get("event_id"),
                "title": props.get("title"),
                "before_event_id": props.get("before_event_id"),
                "after_event_id": props.get("after_event_id"),
                "created_at": created_at_value,
            }
        )
    return events


def _order_timeline_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events_by_id: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = event.get("event_id")
        if event_id:
            events_by_id[event_id] = event
    if not events_by_id:
        return []
    next_links: dict[str, str] = {}
    has_previous: set[str] = set()
    for event_id, event in events_by_id.items():
        after_id = event.get("after_event_id")
        if after_id and after_id in events_by_id:
            next_links[event_id] = after_id
            has_previous.add(after_id)
    heads = [event_id for event_id in events_by_id if event_id not in has_previous]
    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()
    for head in heads:
        current = head
        while current and current not in visited:
            visited.add(current)
            ordered.append(events_by_id[current])
            current = next_links.get(current)
    if len(visited) != len(events_by_id):
        remaining = [
            events_by_id[event_id]
            for event_id in events_by_id
            if event_id not in visited
        ]
        ordered.extend(
            sorted(
                remaining,
                key=lambda payload: (
                    payload.get("created_at") or "",
                    payload.get("event_id") or "",
                ),
            )
        )
    return ordered


async def _attach_timeline_entities(
    graph_session: Any,
    *,
    instance_id: str,
    event_id: str,
    source_entity_id: str | None,
    related_entity_ids: list[str],
) -> None:
    """
    Create SOURCE/INVOLVES relationships for a timeline event.

    Timeline events are stored on the source instance (ontology_instance) but can
    reference entities from any instance, allowing cross-instance entity relationships.
    This enables timeline events to track entities that were created on different instances
    during the generation process.
    """
    if source_entity_id:
        await graph_session.run(
            """
            MATCH (event:Event {event_id: $event_id})
            MATCH (entity:EntityInstance {entity_instance_id: $source_entity_id})
            WHERE event.instance_id = $instance_id
            MERGE (event)-[:SOURCE_ENTITY]->(entity)
            """,
            {
                "event_id": event_id,
                "source_entity_id": source_entity_id,
                "instance_id": instance_id,
            },
        )
    valid_related = [rid for rid in related_entity_ids if rid]
    if valid_related:
        # Note: No instance constraint on entities - allows cross-instance entity involvement
        # Timeline events are stored on source instance but can reference entities anywhere
        await graph_session.run(
            """
            MATCH (event:Event {event_id: $event_id})
            WHERE event.instance_id = $instance_id
            WITH event
            UNWIND $related_ids AS related_id
            MATCH (entity:EntityInstance {entity_instance_id: related_id})
            MERGE (event)-[:INVOLVES_ENTITY]->(entity)
            """,
            {
                "event_id": event_id,
                "instance_id": instance_id,
                "related_ids": valid_related,
            },
        )


async def _link_source_generation_instance(
    graph_session: Any, *, event_id: str, source_instance_id: str | None
) -> None:
    """Link timeline events to the ontology instance that supplied their evidence."""
    if not source_instance_id:
        return
    await graph_session.run(
        """
        MATCH (event:Event {event_id: $event_id})
        MATCH (source:OntologyInstance {instance_id: $source_instance_id})
        MERGE (event)-[:REFERENCES_SOURCE_INSTANCE]->(source)
        """,
        {
            "event_id": event_id,
            "source_instance_id": source_instance_id,
        },
    )


async def _link_timeline_order(
    graph_session: Any,
    *,
    instance_id: str,
    event_id: str,
    before_event_id: str | None,
    after_event_id: str | None,
) -> None:
    """Create temporal AFTER/BEFORE edges so retrieval honors chronology."""
    if before_event_id:
        await graph_session.run(
            """
            MATCH (current:Event {event_id: $event_id})
            MATCH (previous:Event {event_id: $before_event_id})
            WHERE current.instance_id = $instance_id AND previous.instance_id = $instance_id
            MERGE (current)-[:AFTER]->(previous)
            """,
            {
                "event_id": event_id,
                "before_event_id": before_event_id,
                "instance_id": instance_id,
            },
        )
    if after_event_id:
        await graph_session.run(
            """
            MATCH (current:Event {event_id: $event_id})
            MATCH (next:Event {event_id: $after_event_id})
            WHERE current.instance_id = $instance_id AND next.instance_id = $instance_id
            MERGE (current)-[:BEFORE]->(next)
            """,
            {
                "event_id": event_id,
                "after_event_id": after_event_id,
                "instance_id": instance_id,
            },
        )


def _find_tail_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    for event in events:
        if not event.get("after_event_id"):
            return event
    return events[-1]


def _compose_timeline_text(
    title: str,
    description: str,
    *,
    source_label: str | None,
    related_labels: list[str],
    after_title: str | None,
) -> str:
    lines = [f"Timeline Event: {title}", description]
    if source_label:
        lines.append(f"Source: {source_label}")
    if related_labels:
        lines.append(f"Related: {', '.join(related_labels)}")
    if after_title:
        lines.append(f"Follows: {after_title}")
    return "\n".join(lines)


def _resolve_alias_to_entity_id(
    alias: str | None,
    alias_variants: dict[str, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
) -> str | None:
    if not alias:
        return None
    normalized = _normalize_alias(alias)
    if normalized:
        lookup = alias_variants.get(normalized)
        if lookup and lookup.get("entity_instance_id"):
            return lookup["entity_instance_id"]
    for entity_id, payload in existing_entities_map.items():
        entity_alias = payload.get("alias")
        if entity_alias and _normalize_alias(entity_alias) == normalized:
            return entity_id
    return None


def _resolve_aliases_to_ids(
    aliases: list[str],
    alias_variants: dict[str, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for alias in aliases or []:
        entity_id = _resolve_alias_to_entity_id(
            alias, alias_variants, existing_entities_map
        )
        if entity_id and entity_id not in seen:
            seen.add(entity_id)
            resolved.append(entity_id)
    return resolved


async def _apply_timeline_events(
    *,
    graph_session: Any,
    ontology_id: int,
    source_instance_id: str | None,
    timeline_plans: dict[str, list[dict[str, Any]]],
    suggestion_lookup: dict[str, NormalizedSuggestion],
    suggestion_instance_map: dict[str, str],
    new_entity_ids: dict[str, str],
    alias_variants: dict[str, dict[str, Any]],
    existing_entities_map: dict[str, dict[str, Any]],
) -> list[str]:
    if not timeline_plans:
        logger.info("architect_generation_v2: no timeline events to apply")
        return []

    if not source_instance_id:
        logger.error(
            "architect_generation_v2: source_instance_id is required for timeline events - "
            "no timeline events will be created without a valid source instance"
        )
        return []

    instance_event_cache: dict[str, dict[str, Any]] = {}
    created_event_ids: list[str] = []

    entity_instance_lookup: dict[str, str] = {}
    for entity_id, payload in existing_entities_map.items():
        instance_id = payload.get("instance_id")
        if entity_id and instance_id:
            entity_instance_lookup[entity_id] = instance_id
    for suggestion_id, entity_id in new_entity_ids.items():
        instance_id = suggestion_instance_map.get(suggestion_id)
        if entity_id and instance_id:
            entity_instance_lookup[entity_id] = instance_id

    for suggestion_id, events in timeline_plans.items():
        limited_events = _limit_chunk_timeline_events(events, max_events=3)
        if not limited_events:
            continue
        suggestion = suggestion_lookup.get(suggestion_id)
        if not suggestion:
            continue
        if suggestion.mode == "new":
            entity_id = new_entity_ids.get(suggestion_id)
        else:
            entity_id = suggestion.entity_instance_id
        if not entity_id:
            logger.warning(
                "architect_generation_v2: cannot attach timeline events for suggestion %s (missing entity id)",
                suggestion_id,
            )
            continue

        owning_instance_id = entity_instance_lookup.get(entity_id)
        if not owning_instance_id:
            logger.warning(
                "architect_generation_v2: cannot attach timeline events for suggestion %s (missing owning instance for entity %s)",
                suggestion_id,
                entity_id,
            )
            continue

        if owning_instance_id not in instance_event_cache:
            existing_events = await _fetch_instance_timeline_events(
                graph_session, owning_instance_id
            )
            ordered_events = _order_timeline_events(existing_events)
            chain_state = {
                "events": ordered_events,
                "by_id": {
                    event.get("event_id"): event
                    for event in ordered_events
                    if event.get("event_id")
                },
            }
            tail_event = _find_tail_event(ordered_events)
            if tail_event:
                chain_state["tail_event_id"] = tail_event.get("event_id")
                chain_state["tail_event_title"] = tail_event.get("title")
            else:
                chain_state["tail_event_id"] = None
                chain_state["tail_event_title"] = None
            instance_event_cache[owning_instance_id] = chain_state
            logger.info(
                "architect_generation_v2: loaded %d existing timeline events for target instance %s",
                len(existing_events),
                owning_instance_id,
            )
        chain_state = instance_event_cache[owning_instance_id]
        previous_event_id = chain_state.get("tail_event_id")
        previous_event_title = chain_state.get("tail_event_title")
        logger.info(
            "architect_generation_v2: applying %d timeline events for suggestion %s -> entity %s (instance=%s current_tail=%s provenance_instance=%s)",
            len(limited_events),
            suggestion_id,
            entity_id,
            owning_instance_id,
            previous_event_id,
            source_instance_id,
        )

        for event in limited_events:
            # Timeline events are always stored on the source instance (ontology_instance)
            # and reference related entities via related_entity_ids
            source_entity_id = None
            resolved_related = _resolve_aliases_to_ids(
                event.get("related_aliases") or [],
                alias_variants,
                existing_entities_map,
            )
            related_ids = [rid for rid in resolved_related if rid]
            dedup_related = []
            seen_related: set[str] = set()
            for rid in related_ids:
                if rid and rid not in seen_related:
                    seen_related.add(rid)
                    dedup_related.append(rid)
            related_instance_ids: list[str] = []
            seen_pages: set[str] = set()
            for rid in dedup_related:
                parent_instance = entity_instance_lookup.get(rid)
                if (
                    not parent_instance
                    or parent_instance == owning_instance_id
                    or parent_instance == source_instance_id
                    or parent_instance in seen_pages
                ):
                    continue
                seen_pages.add(parent_instance)
                related_instance_ids.append(parent_instance)

            event_id = str(uuid4())
            timestamp = datetime.utcnow().isoformat() + "Z"
            text = _compose_timeline_text(
                event["title"],
                event["description"],
                source_label=event.get("source_alias"),
                related_labels=event.get("related_aliases") or [],
                after_title=previous_event_title,
            )

            await graph_session.run(
                """
                MATCH (inst:OntologyInstance {instance_id: $instance_id})
                CREATE (inst)-[:HAS_EVENT]->(event:Event {
                    event_id: $event_id,
                    entity_instance_id: $event_id,
                    instance_id: $instance_id,
                    ontology_id: $ontology_id,
                    name: $title,
                    alias: $title,
                    title: $title,
                    description: $description,
                    created_from_instance_id: $created_from_instance_id,
                    created_from_entity_id: $created_from_entity_id,
                    source_instance_id: $created_from_instance_id,
                    source_entity_id: $created_from_entity_id,
                    related_instance_ids: $related_instance_ids,
                    related_entity_ids: $related_entity_ids,
                    before_event_id: $before_event_id,
                    after_event_id: $after_event_id,
                    created_at: $timestamp,
                    updated_at: $timestamp,
                    last_updated_date: $timestamp,
                    text: $text,
                    autogenerated_text: $text,
                    is_embedded: false,
                    last_embedded_date: null
                })
                """,
                {
                    "instance_id": owning_instance_id,
                    "ontology_id": ontology_id,
                    "event_id": event_id,
                    "title": event["title"],
                    "description": event["description"],
                    "created_from_instance_id": source_instance_id,
                    "created_from_entity_id": source_entity_id,
                    "related_instance_ids": related_instance_ids,
                    "related_entity_ids": dedup_related,
                    "before_event_id": previous_event_id,
                    "after_event_id": None,
                    "timestamp": timestamp,
                    "text": text,
                },
            )
            await _attach_timeline_entities(
                graph_session,
                instance_id=owning_instance_id,
                event_id=event_id,
                source_entity_id=source_entity_id,
                related_entity_ids=dedup_related,
            )
            await _link_source_generation_instance(
                graph_session,
                event_id=event_id,
                source_instance_id=source_instance_id,
            )
            await _link_timeline_order(
                graph_session,
                instance_id=owning_instance_id,
                event_id=event_id,
                before_event_id=previous_event_id,
                after_event_id=None,
            )

            if previous_event_id:
                await graph_session.run(
                    """
                    MATCH (event:Event {event_id: $event_id})
                    SET event.after_event_id = $after_event_id,
                        event.updated_at = datetime(),
                        event.last_updated_date = datetime(),
                        event.is_embedded = false
                    """,
                    {"event_id": previous_event_id, "after_event_id": event_id},
                )
                await _link_timeline_order(
                    graph_session,
                    instance_id=owning_instance_id,
                    event_id=previous_event_id,
                    before_event_id=None,
                    after_event_id=event_id,
                )
                prev_payload = chain_state["by_id"].get(previous_event_id)
                if prev_payload:
                    prev_payload["after_event_id"] = event_id

            new_event_payload = {
                "event_id": event_id,
                "title": event["title"],
                "before_event_id": previous_event_id,
                "after_event_id": None,
                "created_at": timestamp,
            }
            chain_state["events"].append(new_event_payload)
            chain_state["by_id"][event_id] = new_event_payload
            previous_event_id = event_id
            previous_event_title = event["title"]
            chain_state["tail_event_id"] = event_id
            chain_state["tail_event_title"] = event["title"]
            created_event_ids.append(event_id)
            logger.info(
                "architect_generation_v2: created timeline event %s for entity %s via suggestion %s (instance=%s provenance_instance=%s title=%s)",
                event_id,
                entity_id,
                suggestion_id,
                owning_instance_id,
                source_instance_id,
                event["title"],
            )

    return created_event_ids


def _strip_markdown_code_blocks(text: str | None) -> str:
    """
    Remove markdown code block delimiters from text.

    This is needed because LLMs sometimes wrap HTML/text content in markdown code blocks,
    which makes them unparsable on the frontend.

    Handles code blocks with any language identifier (e.g., ```html, ```javascript, ```css)
    or no identifier (e.g., ```).

    Args:
        text: The text to clean, may be None

    Returns:
        Clean text without markdown code block delimiters
    """
    if not text:
        return ""
    # Remove markdown code blocks with any optional language identifier
    # Use DOTALL flag to match across newlines
    cleaned = re.sub(
        MARKDOWN_CODE_BLOCK_PATTERN, r"\1", text, flags=re.DOTALL | re.IGNORECASE
    )
    return cleaned.strip()


def _strip_markdown_markup(text: str) -> str:
    """Remove simple Markdown markers so downstream HTML is clean."""
    if not text:
        return ""
    cleaned = text
    heading_pattern = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
    cleaned = heading_pattern.sub("", cleaned)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
    cleaned = re.sub(r"_(.*?)_", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()
