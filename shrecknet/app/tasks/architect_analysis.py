from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import logging
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import get_settings, is_openai_configured
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.jobs.architect.prompts import (
    ARCHITECT_ENTITY_PROPOSAL_PROMPT,
    ARCHITECT_ENTITY_RECONCILATION_PROMPT,
    ARECHITECT_MILESTONE_PROPOSAL_PROMPT,
)
from app.jobs.architect.schemas import ChunkExtractionResponse, ReconciliationResponse
from app.jobs.architect.scene_centric_chunking import (
    build_scene_chunks,
    extract_paragraphs_from_sources,
    segment_chunk_into_scenes,
)
from app.models.architect import ArchitectProposalType, ArchitectRunStatus
from app.models.background_job import AuthorType, JobType
from app.repositories.agent_repository import AgentRepository
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.ontology_repository import OntologyRepository
from app.services.ontology_instance_service import OntologyInstanceService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)
from app.db.session import AsyncSessionMaker

logger = logging.getLogger(__name__)

SCENE_CHUNKING_TOKEN_LIMIT = 16_000
SCENE_ENTITY_EXTRACTION_CONCURRENCY = 10
MILESTONE_EXTRACTION_CONCURRENCY = 10
MIN_TOKEN_OVERLAP_RATIO = 0.5


def _resolve_local_tests_output_dir(job_name: str) -> Path:
    """Resolve writable local_tests path for analysis artifact dumps."""
    data_root = os.getenv("SHRECKNET_DATA_DIR", "/data")
    module_root = Path(__file__).resolve().parents[2]
    candidates = [
        Path(data_root) / "local_tests" / "arhictect" / "Analyses" / str(job_name),
        module_root / "databases" / "local_tests" / "arhictect" / "Analyses" / str(job_name),
        Path.cwd() / "shrecknet" / "databases" / "local_tests" / "arhictect" / "Analyses" / str(job_name),
        Path.cwd() / "databases" / "local_tests" / "arhictect" / "Analyses" / str(job_name),
    ]

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue

    fallback = Path("local_tests") / "arhictect" / "Analyses" / str(job_name)
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _write_local_json_artifact(
    *,
    output_dir: Path,
    filename: str,
    payload: dict[str, Any],
) -> str | None:
    """Write a JSON artifact to local_tests, returning path on success."""
    # Debug artifact writing is intentionally disabled for architect/analyse.
    # Keep the implementation below commented for quick re-enable when needed.
    return None

    # output_path = output_dir / filename
    # try:
    #     output_path.write_text(
    #         json.dumps(payload, ensure_ascii=False, indent=2),
    #         encoding="utf-8",
    #     )
    #     return str(output_path)
    # except OSError as exc:
    #     logger.warning(
    #         "analysis_local_artifact_write_error: file=%s error=%s",
    #         output_path,
    #         exc,
    #     )
    #     return None


def _extract_json_block(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response")
    return raw[start : end + 1]


def _parse_chunk_extraction(response_text: str, scene_ref: str) -> ChunkExtractionResponse:
    try:
        payload = json.loads(_extract_json_block(response_text))
        return ChunkExtractionResponse.model_validate(payload)
    except Exception as exc:
        logger.warning(
            "scene_entity_parse_error: scene_ref=%s error=%s",
            scene_ref,
            exc,
        )
        return ChunkExtractionResponse(entities=[])


def _normalize_ontology_name(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", raw)


def _build_allowed_ontology_map(entity_defs: list[Any]) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for definition in entity_defs:
        if not bool(getattr(definition, "auto_generatable", True)):
            continue
        name = (getattr(definition, "name", "") or "").strip()
        if not name:
            continue
        allowed[_normalize_ontology_name(name)] = name
    return allowed


def _canonical_alias(alias: str | None) -> str:
    if not alias:
        return ""
    value = alias.strip().lower().strip("\"'")
    value = re.sub(r"\([^()]*\)", " ", value)
    if ":" in value:
        parts = [part.strip() for part in value.split(":") if part.strip()]
        if parts:
            value = parts[-1]
    if "," in value:
        value = value.split(",", 1)[0].strip()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _aliases_equivalent(alias_a: str | None, alias_b: str | None) -> bool:
    if not alias_a or not alias_b:
        return False
    if alias_a == alias_b:
        return True

    tokens_a = alias_a.split()
    tokens_b = alias_b.split()
    if not tokens_a or not tokens_b:
        return False

    def _one_token_matches_first_or_last(
        single_tokens: list[str], multi_tokens: list[str]
    ) -> bool:
        token = single_tokens[0]
        if token == multi_tokens[-1]:
            return True
        if len(multi_tokens) <= 2 and token == multi_tokens[0]:
            return True
        return False

    if len(tokens_a) == 1 and len(tokens_b) > 1:
        return _one_token_matches_first_or_last(tokens_a, tokens_b)
    if len(tokens_b) == 1 and len(tokens_a) > 1:
        return _one_token_matches_first_or_last(tokens_b, tokens_a)
    return False


def _find_existing_match(
    canonical_name: str,
    existing_by_canonical: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if canonical_name in existing_by_canonical:
        return existing_by_canonical[canonical_name]
    for existing_key, node in existing_by_canonical.items():
        if _aliases_equivalent(canonical_name, existing_key):
            return node
    return None


def _find_matching_canonical_key(
    canonical_name: str,
    deduped: dict[str, dict[str, Any]],
) -> str | None:
    if canonical_name in deduped:
        return canonical_name
    for existing_key in deduped.keys():
        if _aliases_equivalent(canonical_name, existing_key):
            return existing_key
    return None


def _prefilter_node_catalogue(
    deduped: dict[str, dict[str, Any]],
    existing_nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not deduped or not existing_nodes:
        return [], {}

    existing_by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in existing_nodes:
        canonical = _canonical_alias(node.get("alias"))
        if canonical:
            existing_by_canonical[canonical].append(node)

    filtered_nodes: dict[str, dict[str, Any]] = {}
    exact_matches: dict[str, dict[str, Any]] = {}

    for canonical_proposed in deduped.keys():
        matched_this_entity = False

        if canonical_proposed in existing_by_canonical:
            for node in existing_by_canonical[canonical_proposed]:
                node_id = str(node.get("node_id") or "")
                if node_id:
                    filtered_nodes[node_id] = node
                    if canonical_proposed not in exact_matches:
                        exact_matches[canonical_proposed] = node
                    matched_this_entity = True

        proposed_tokens = set(canonical_proposed.split())
        for existing_canonical, nodes in existing_by_canonical.items():
            if _aliases_equivalent(canonical_proposed, existing_canonical):
                for node in nodes:
                    node_id = str(node.get("node_id") or "")
                    if node_id:
                        filtered_nodes[node_id] = node
                continue

            existing_tokens = set(existing_canonical.split())
            shared_tokens = proposed_tokens & existing_tokens
            if not shared_tokens:
                continue

            min_tokens = min(len(proposed_tokens), len(existing_tokens))
            if min_tokens <= 0:
                continue

            overlap_ratio = len(shared_tokens) / min_tokens
            if overlap_ratio >= MIN_TOKEN_OVERLAP_RATIO:
                for node in nodes:
                    node_id = str(node.get("node_id") or "")
                    if node_id:
                        filtered_nodes[node_id] = node

        if matched_this_entity:
            continue

    logger.info(
        "scene_entity_prefilter: proposed=%d catalogue=%d filtered=%d exact=%d",
        len(deduped),
        len(existing_nodes),
        len(filtered_nodes),
        len(exact_matches),
    )
    return list(filtered_nodes.values()), exact_matches


def _parse_reconciliation(response_text: str) -> ReconciliationResponse:
    try:
        payload = json.loads(_extract_json_block(response_text))
        return ReconciliationResponse.model_validate(payload)
    except Exception as exc:
        logger.warning("scene_entity_reconciliation_parse_error: %s", exc)
        return ReconciliationResponse(existing=[], new=[])


async def _reconcile_with_existing(
    *,
    llm_client: OpenAIClient,
    model: str,
    deduped: dict[str, dict[str, Any]],
    existing_nodes: list[dict[str, Any]],
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
) -> dict[str, Any]:
    if not deduped:
        return {"existing": [], "new": []}

    filtered_catalogue, exact_matches = _prefilter_node_catalogue(deduped, existing_nodes)

    existing_results: list[dict[str, Any]] = []
    entities_needing_llm: list[dict[str, str]] = []

    for canonical, entry in deduped.items():
        if canonical in exact_matches:
            matched = exact_matches[canonical]
            existing_results.append(
                {
                    "proposed_name": entry.get("name") or "",
                    "matched_node_id": matched.get("node_id"),
                    "ontology": entry.get("ontology") or "",
                }
            )
        else:
            entities_needing_llm.append(
                {
                    "name": entry.get("name") or "",
                    "ontology": entry.get("ontology") or "",
                }
            )

    if not entities_needing_llm:
        return {"existing": existing_results, "new": []}

    if not filtered_catalogue:
        return {
            "existing": existing_results,
            "new": entities_needing_llm,
        }

    existing_list = [
        {
            "node_id": str(node.get("node_id") or ""),
            "alias": str(node.get("alias") or ""),
            "ontology": str(node.get("ontology") or ""),
        }
        for node in filtered_catalogue
    ]

    prompt = ARCHITECT_ENTITY_RECONCILATION_PROMPT.format(
        ontology_definitions=ontology_definitions,
        proposed_entities=json.dumps(entities_needing_llm, indent=2),
        existing_entities=json.dumps(existing_list, indent=2),
    )

    try:
        response_text = await llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        parsed = _parse_reconciliation(response_text)
        valid_ids = {
            str(node.get("node_id") or "")
            for node in filtered_catalogue
            if str(node.get("node_id") or "")
        }
        deduped_by_canonical = {
            _canonical_alias(entry.get("name")): entry for entry in entities_needing_llm
        }

        for entry in parsed.existing:
            if entry.matched_node_id and entry.matched_node_id in valid_ids:
                canonical = _canonical_alias(entry.proposed_name)
                fallback_ontology = (
                    (deduped_by_canonical.get(canonical) or {}).get("ontology") or ""
                )
                selected_ontology = entry.ontology or fallback_ontology
                mapped = allowed_ontology_names.get(
                    _normalize_ontology_name(selected_ontology)
                )
                if not mapped:
                    logger.warning(
                        "scene_entity_reconcile_invalid_ontology_existing: proposed=%s ontology=%s",
                        entry.proposed_name,
                        selected_ontology,
                    )
                    continue
                existing_results.append(
                    {
                        "proposed_name": entry.proposed_name,
                        "matched_node_id": entry.matched_node_id,
                        "ontology": mapped,
                    }
                )
            else:
                logger.warning(
                    "scene_entity_reconcile_invalid_match: proposed=%s matched_id=%s",
                    entry.proposed_name,
                    entry.matched_node_id,
                )

        new_results: list[dict[str, Any]] = []
        for item in parsed.new:
            mapped = allowed_ontology_names.get(_normalize_ontology_name(item.ontology))
            if not mapped:
                logger.warning(
                    "scene_entity_reconcile_invalid_ontology_new: name=%s ontology=%s",
                    item.name,
                    item.ontology,
                )
                continue
            new_results.append({"name": item.name, "ontology": mapped})

        return {
            "existing": existing_results,
            "new": new_results,
        }
    except Exception as exc:
        logger.warning("scene_entity_reconcile_fallback_new: %s", exc)
        return {
            "existing": existing_results,
            "new": entities_needing_llm,
        }


async def _classify_entities_with_reconciliation(
    *,
    llm_client: OpenAIClient,
    model: str,
    scene_results: list[dict[str, Any]],
    existing_nodes: list[dict[str, Any]],
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
) -> dict[str, Any]:
    deduped: dict[str, dict[str, Any]] = {}
    for scene in scene_results:
        for entity in scene.get("entities", []):
            canonical = _canonical_alias(entity.get("name"))
            if not canonical:
                continue
            ontology_value = str(entity.get("ontology") or "").strip()
            mapped_ontology = allowed_ontology_names.get(
                _normalize_ontology_name(ontology_value)
            )
            if not mapped_ontology:
                logger.warning(
                    "scene_entity_invalid_ontology_filtered: name=%s ontology=%s canonical=%s",
                    entity.get("name"),
                    ontology_value,
                    canonical,
                )
                continue

            dedup_key = _find_matching_canonical_key(canonical, deduped) or canonical
            if dedup_key not in deduped:
                deduped[dedup_key] = {
                    "canonical": dedup_key,
                    "name": entity.get("name") or "",
                    "ontology": mapped_ontology,
                    "confidence_values": [],
                    "whys": [],
                    "scene_refs": [],
                    "chunk_indices": [],
                }

            entry = deduped[dedup_key]
            name = entity.get("name") or ""
            if len(name) > len(entry["name"]):
                entry["name"] = name
            entry["ontology"] = mapped_ontology

            confidence = entity.get("confidence")
            if isinstance(confidence, (int, float)):
                entry["confidence_values"].append(float(confidence))

            why = (entity.get("why") or "").strip()
            if why and why not in entry["whys"]:
                entry["whys"].append(why)

            scene_ref = scene.get("scene_ref")
            if scene_ref and scene_ref not in entry["scene_refs"]:
                entry["scene_refs"].append(scene_ref)

            chunk_index = scene.get("chunk_index")
            if isinstance(chunk_index, int) and chunk_index not in entry["chunk_indices"]:
                entry["chunk_indices"].append(chunk_index)

    reconciled = await _reconcile_with_existing(
        llm_client=llm_client,
        model=model,
        deduped=deduped,
        existing_nodes=existing_nodes,
        ontology_definitions=ontology_definitions,
        allowed_ontology_names=allowed_ontology_names,
    )

    existing_map: dict[str, dict[str, Any]] = {}
    for item in reconciled.get("existing", []):
        key = _canonical_alias(item.get("proposed_name"))
        matched_id = item.get("matched_node_id")
        if key and matched_id:
            existing_map[key] = item

    new_set = {
        _canonical_alias(item.get("name"))
        for item in reconciled.get("new", [])
        if _canonical_alias(item.get("name"))
    }

    proposed_entities: list[dict[str, Any]] = []
    status_by_canonical: dict[str, dict[str, Any]] = {}
    status_ontology_counts: dict[str, dict[str, int]] = {
        "new": defaultdict(int),
        "updated": defaultdict(int),
    }

    for canonical, entry in deduped.items():
        ontology = entry.get("ontology") or ""
        if not ontology:
            logger.warning(
                "scene_entity_missing_ontology_filtered: canonical=%s name=%s",
                canonical,
                entry.get("name"),
            )
            continue
        avg_confidence = 0.0
        if entry["confidence_values"]:
            avg_confidence = round(
                sum(entry["confidence_values"]) / len(entry["confidence_values"]),
                4,
            )

        matched = existing_map.get(canonical)
        is_existing = bool(matched and matched.get("matched_node_id"))
        if not is_existing and canonical not in new_set and matched:
            # Defensive fallback if reconciliation shape is imperfect.
            is_existing = bool(matched.get("matched_node_id"))

        resolved_status = "existing" if is_existing else "new"
        status = "updated" if is_existing else "new"
        status_ontology_counts[status][ontology] += 1

        matched_node_id = matched.get("matched_node_id") if is_existing else None
        proposal_type = (
            ArchitectProposalType.UPDATE_INSTANCE.value
            if is_existing
            else ArchitectProposalType.NEW_INSTANCE.value
        )

        chunk_indices = sorted(entry.get("chunk_indices", []))
        proposal_metadata = {
            "resolved_status": resolved_status,
            "mention_count": len(entry.get("scene_refs", [])),
            "chunk_indices": chunk_indices,
            "ontology_name": ontology,
        }

        resolved = {
            "name": entry["name"],
            "canonical": canonical,
            "ontology": ontology,
            "confidence": avg_confidence,
            "why": entry["whys"][0] if entry["whys"] else "",
            "status": status,
            "matched_node_id": matched_node_id,
            "scene_refs": entry["scene_refs"],
            "proposal_type": proposal_type,
            "entity_instance_id": matched_node_id,
            "proposal_metadata": proposal_metadata,
        }
        status_by_canonical[canonical] = resolved
        proposed_entities.append(resolved)

    for scene in scene_results:
        enriched_entities: list[dict[str, Any]] = []
        for entity in scene.get("entities", []):
            canonical = _canonical_alias(entity.get("name"))
            resolved = status_by_canonical.get(canonical)
            enriched_entities.append(
                {
                    **entity,
                    "status": (resolved or {}).get("status", "new"),
                    "matched_node_id": (resolved or {}).get("matched_node_id"),
                }
            )
        scene["entities"] = enriched_entities

    return {
        "proposed_entities": proposed_entities,
        "updated_count": sum(1 for item in proposed_entities if item["status"] == "updated"),
        "new_count": sum(1 for item in proposed_entities if item["status"] == "new"),
        "status_ontology_counts": {
            status_name: dict(ontology_counts)
            for status_name, ontology_counts in status_ontology_counts.items()
        },
        "scene_results": scene_results,
    }


def _format_ontology_definitions_from_entities(entity_defs: list[Any]) -> str:
    lines: list[str] = []
    for definition in entity_defs:
        if not bool(getattr(definition, "auto_generatable", True)):
            continue
        name = (getattr(definition, "name", "") or "").strip()
        if not name:
            continue
        desc = (getattr(definition, "description", "") or "").strip().replace("\n", " ")
        if len(desc) > 240:
            desc = desc[:240] + "..."
        lines.append(f"- {name}: {desc or 'No description'}")
    return "\n".join(lines) if lines else "(no ontology definitions)"


async def _load_existing_nodes(
    retriever: Neo4jGraphRetriever,
    ontology_ids: list[int],
    *,
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    fetch_method = getattr(retriever, "list_entities_by_ontology", None)

    if callable(fetch_method):
        for ontology_id in ontology_ids:
            skip = 0
            while True:
                try:
                    batch = await fetch_method(
                        ontology_id=ontology_id,
                        skip=skip,
                        limit=batch_size,
                    )
                except Exception as exc:
                    logger.warning(
                        "scene_entity_node_catalogue_batch_error: ontology=%s skip=%s error=%s",
                        ontology_id,
                        skip,
                        exc,
                    )
                    break

                if not batch:
                    break

                for item in batch:
                    node_id = str(item.get("node_id") or "").strip()
                    alias = str(item.get("alias") or "").strip()
                    ontology = str(item.get("ontology") or "").strip()
                    if not node_id or not alias:
                        continue
                    nodes_by_id[node_id] = {
                        "node_id": node_id,
                        "alias": alias,
                        "ontology": ontology,
                    }

                if len(batch) < batch_size:
                    break
                skip += batch_size
    else:
        try:
            results = await retriever.search_aliases(
                query="",
                ontology_ids=ontology_ids,
                top_k=batch_size,
            )
            for result in results:
                node_id = str(result.node_id or "").strip()
                alias = str(result.node_alias or "").strip()
                if not node_id or not alias:
                    continue
                nodes_by_id[node_id] = {
                    "node_id": node_id,
                    "alias": alias,
                    "ontology": str(result.source or "").strip(),
                }
        except Exception as exc:
            logger.warning("scene_entity_node_catalogue_error: %s", exc)

    nodes = list(nodes_by_id.values())
    logger.info("scene_entity_node_catalogue_loaded: count=%d", len(nodes))
    return nodes


def _existing_nodes_from_instance(ontology_instance: Any) -> list[dict[str, Any]]:
    """Build reconciliation catalogue from entities in the target ontology instance only."""
    nodes: list[dict[str, Any]] = []
    for entity in getattr(ontology_instance, "entities", []) or []:
        node_id = str(getattr(entity, "entity_instance_id", "") or "").strip()
        alias = str(getattr(entity, "alias", "") or "").strip()
        if not node_id or not alias:
            continue
        nodes.append(
            {
                "node_id": node_id,
                "alias": alias,
                "ontology": str(getattr(entity, "definition_id", "") or "").strip(),
            }
        )
    return nodes


def _flatten_scene_inputs(chunk_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []
    for chunk in chunk_results:
        if chunk.get("status") != "ok":
            continue
        chunk_index = chunk.get("chunk_index")
        entity_instance_id = chunk.get("entity_instance_id")
        entity_alias = chunk.get("entity_alias")
        for idx, scene in enumerate(chunk.get("scenes", [])):
            scene_id = scene.get("scene_id", idx)
            scenes.append(
                {
                    "scene_ref": f"chunk_{chunk_index}_scene_{scene_id}",
                    "chunk_index": chunk_index,
                    "source_entity_instance_id": entity_instance_id,
                    "source_entity_alias": entity_alias,
                    "scene_id": scene_id,
                    "scene_name": scene.get("name") or "",
                    "scene_description": scene.get("description") or "",
                    "scene_text": scene.get("text") or "",
                }
            )
    return scenes


async def _extract_scene_entities(
    *,
    run_id: str,
    llm_client: OpenAIClient,
    model: str,
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
    scenes: list[dict[str, Any]],
    instructions: str | None = None,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(SCENE_ENTITY_EXTRACTION_CONCURRENCY)

    async def _process_scene(scene_input: dict[str, Any]) -> dict[str, Any]:
        scene_ref = scene_input["scene_ref"]
        scene_name = scene_input.get("scene_name", "")
        scene_description = scene_input.get("scene_description", "")
        scene_text = scene_input.get("scene_text", "")

        try:
            async with semaphore:
                prompt = ARCHITECT_ENTITY_PROPOSAL_PROMPT.format(
                    ontology_definitions=ontology_definitions,
                    scene_name=scene_name,
                    scene_description=scene_description,
                    scene_text=scene_text,
                    # Backward compatibility if the template still references {chunk_text}.
                    chunk_text=scene_text,
                )
                instructions_text = str(instructions or "").strip()
                if instructions_text:
                    prompt = (
                        f"{prompt}\n\nFrontend instructions (authoritative constraints):\n"
                        f"{instructions_text}"
                    )
                response_text = await llm_client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )

            parsed = _parse_chunk_extraction(response_text, scene_ref)
            entities: list[dict[str, Any]] = []
            for entity in parsed.entities:
                mapped = allowed_ontology_names.get(
                    _normalize_ontology_name(entity.ontology)
                )
                if not mapped:
                    logger.warning(
                        "scene_entity_invalid_ontology_dropped: run_id=%s scene_ref=%s name=%s ontology=%s",
                        run_id,
                        scene_ref,
                        entity.name,
                        entity.ontology,
                    )
                    continue
                payload = entity.model_dump()
                payload["ontology"] = mapped
                entities.append(payload)
            logger.info(
                "scene_entity_extraction_scene_done: run_id=%s scene_ref=%s entity_count=%d",
                run_id,
                scene_ref,
                len(entities),
            )
            return {
                **scene_input,
                "status": "ok",
                "entities": entities,
            }
        except Exception as exc:
            logger.warning(
                "scene_entity_extraction_scene_error: run_id=%s scene_ref=%s error=%s",
                run_id,
                scene_ref,
                exc,
            )
            return {
                **scene_input,
                "status": "error",
                "error": str(exc),
                "entities": [],
            }

    return await asyncio.gather(*(_process_scene(scene) for scene in scenes))


def _build_scene_entity_index(
    proposed_entities: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proposal_index, entity in enumerate(proposed_entities):
        for scene_ref in entity.get("scene_refs", []):
            by_scene[scene_ref].append(
                {
                    "proposal_index": proposal_index,
                    "canonical": entity.get("canonical"),
                    "alias": entity.get("name"),
                    "status": entity.get("status"),
                    "proposal_type": entity.get("proposal_type"),
                    "entity_instance_id": entity.get("entity_instance_id"),
                }
            )

    for scene_ref, items in by_scene.items():
        items.sort(key=lambda item: item.get("proposal_index", 0))
        by_scene[scene_ref] = items
    return by_scene


def _build_scene_proposals(
    scene_inputs: list[dict[str, Any]],
    proposed_entities: list[dict[str, Any]],
    author_id: str,
) -> list[dict[str, Any]]:
    scene_entity_index = _build_scene_entity_index(proposed_entities)
    base_rows: list[dict[str, Any]] = []

    for scene_order, scene in enumerate(scene_inputs, start=1):
        scene_ref = str(scene.get("scene_ref") or f"scene_{scene_order}")
        scene_uuid = str(uuid4())
        base_rows.append(
            {
                "scene_order": scene_order,
                "scene_ref": scene_ref,
                "scene_id": scene_uuid,
                "chunk_index": scene.get("chunk_index"),
                "source_entity_instance_id": scene.get("source_entity_instance_id"),
                "source_entity_alias": scene.get("source_entity_alias"),
                "scene_index": scene.get("scene_id"),
                "scene_name": scene.get("scene_name") or "",
                "scene_description": scene.get("scene_description") or "",
                "scene_text": scene.get("scene_text") or "",
                "related_to": scene_entity_index.get(scene_ref, []),
                "author": {
                    "created_by_type": "agent",
                    "created_by_author": author_id,
                },
                "derived_from": {
                    "entity_instance_id": scene.get("source_entity_instance_id"),
                },
            }
        )

    for idx, row in enumerate(base_rows):
        prev_row = base_rows[idx - 1] if idx > 0 else None
        next_row = base_rows[idx + 1] if idx + 1 < len(base_rows) else None
        row["preceded_by"] = (
            {
                "scene_ref": prev_row["scene_ref"],
                "scene_id": prev_row["scene_id"],
            }
            if prev_row
            else None
        )
        row["followed_by"] = (
            {
                "scene_ref": next_row["scene_ref"],
                "scene_id": next_row["scene_id"],
            }
            if next_row
            else None
        )

    return base_rows


async def _run_scene_chunking_phase(
    *,
    run_id: str,
    ontology_instance: Any,
    llm_client: OpenAIClient,
    model: str,
    instructions: str | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    all_chunk_results: list[dict[str, Any]] = []
    total_chunks = 0
    total_paragraphs = 0
    total_scenes = 0

    for entity in ontology_instance.entities:
        paragraphs = extract_paragraphs_from_sources(
            getattr(entity, "text", None),
            getattr(entity, "autogenerated_text", None),
        )
        if not paragraphs:
            continue

        chunks = build_scene_chunks(
            paragraphs,
            token_limit=SCENE_CHUNKING_TOKEN_LIMIT,
        )

        for chunk in chunks:
            logger.info(
                "scene_chunking_chunk_start: run_id=%s entity_id=%s chunk_index=%d paragraph_count=%d token_count=%d",
                run_id,
                getattr(entity, "entity_instance_id", ""),
                chunk.chunk_index,
                chunk.paragraph_count,
                chunk.token_count,
            )
            total_chunks += 1
            total_paragraphs += chunk.paragraph_count

            try:
                scenes = await segment_chunk_into_scenes(
                    llm_client=llm_client,
                    model=model,
                    marked_paragraphs=chunk.marked_paragraphs,
                    paragraph_count=chunk.paragraph_count,
                    paragraphs=chunk.paragraphs,
                    instructions=instructions,
                )
                total_scenes += len(scenes)
                logger.info(
                    "scene_chunking_chunk_done: run_id=%s entity_id=%s chunk_index=%d scenes_found=%d",
                    run_id,
                    getattr(entity, "entity_instance_id", ""),
                    chunk.chunk_index,
                    len(scenes),
                )
                all_chunk_results.append(
                    {
                        "status": "ok",
                        "entity_instance_id": getattr(entity, "entity_instance_id", None),
                        "entity_alias": getattr(entity, "alias", None),
                        "chunk_index": chunk.chunk_index,
                        "paragraph_count": chunk.paragraph_count,
                        "token_count": chunk.token_count,
                        "paragraph_start": chunk.paragraph_start,
                        "paragraph_end": chunk.paragraph_end,
                        "marked_paragraphs": chunk.marked_paragraphs,
                        "scenes": scenes,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "scene_chunking_chunk_error: run_id=%s entity_id=%s chunk_index=%d error=%s",
                    run_id,
                    getattr(entity, "entity_instance_id", ""),
                    chunk.chunk_index,
                    exc,
                )
                all_chunk_results.append(
                    {
                        "status": "error",
                        "error": str(exc),
                        "entity_instance_id": getattr(entity, "entity_instance_id", None),
                        "entity_alias": getattr(entity, "alias", None),
                        "chunk_index": chunk.chunk_index,
                        "paragraph_count": chunk.paragraph_count,
                        "token_count": chunk.token_count,
                        "paragraph_start": chunk.paragraph_start,
                        "paragraph_end": chunk.paragraph_end,
                        "marked_paragraphs": chunk.marked_paragraphs,
                        "scenes": [],
                    }
                )

    elapsed_seconds = round(perf_counter() - started, 3)
    logger.info(
        "scene_chunking_total: run_id=%s chunk_count=%d paragraph_count=%d scene_count=%d elapsed_seconds=%.3f",
        run_id,
        total_chunks,
        total_paragraphs,
        total_scenes,
        elapsed_seconds,
    )

    return {
        "chunk_results": all_chunk_results,
        "chunk_count": total_chunks,
        "paragraph_count": total_paragraphs,
        "scene_count": total_scenes,
        "elapsed_seconds": elapsed_seconds,
    }


async def _run_entity_proposal_phase(
    *,
    run_id: str,
    llm_client: OpenAIClient,
    model: str,
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
    existing_nodes: list[dict[str, Any]],
    chunk_results: list[dict[str, Any]],
    instructions: str | None = None,
) -> dict[str, Any]:
    scene_inputs = _flatten_scene_inputs(chunk_results)
    logger.info(
        "scene_entity_extraction_start: run_id=%s scene_count=%d concurrency=%d",
        run_id,
        len(scene_inputs),
        SCENE_ENTITY_EXTRACTION_CONCURRENCY,
    )

    started = perf_counter()
    scene_entity_results = await _extract_scene_entities(
        run_id=run_id,
        llm_client=llm_client,
        model=model,
        ontology_definitions=ontology_definitions,
        allowed_ontology_names=allowed_ontology_names,
        scenes=scene_inputs,
        instructions=instructions,
    )

    classified = await _classify_entities_with_reconciliation(
        llm_client=llm_client,
        model=model,
        scene_results=scene_entity_results,
        existing_nodes=existing_nodes,
        ontology_definitions=ontology_definitions,
        allowed_ontology_names=allowed_ontology_names,
    )

    elapsed_seconds = round(perf_counter() - started, 3)
    proposed_entities = classified["proposed_entities"]
    updated_count = classified["updated_count"]
    new_count = classified["new_count"]
    status_ontology_counts = classified["status_ontology_counts"]

    logger.info(
        "scene_entity_extraction_total: run_id=%s deduped_proposals=%d updated=%d new=%d elapsed_seconds=%.3f",
        run_id,
        len(proposed_entities),
        updated_count,
        new_count,
        elapsed_seconds,
    )
    logger.info(
        "scene_entity_discovery_summary: run_id=%s proposed_entities=%s updated_by_ontology=%s new_by_ontology=%s elapsed_seconds=%.3f",
        run_id,
        [
            {
                "name": item.get("name"),
                "ontology": item.get("ontology"),
                "status": item.get("status"),
            }
            for item in proposed_entities
        ],
        status_ontology_counts.get("updated", {}),
        status_ontology_counts.get("new", {}),
        elapsed_seconds,
    )

    return {
        "scene_inputs": scene_inputs,
        "scene_entity_results": scene_entity_results,
        "proposed_entities": proposed_entities,
        "updated_count": updated_count,
        "new_count": new_count,
        "status_ontology_counts": status_ontology_counts,
        "elapsed_seconds": elapsed_seconds,
    }


def _run_scene_proposal_phase(
    *,
    run_id: str,
    scene_inputs: list[dict[str, Any]],
    proposed_entities: list[dict[str, Any]],
    author_id: str,
) -> dict[str, Any]:
    started = perf_counter()
    logger.info(
        "scene_proposal_start: run_id=%s scene_count=%d",
        run_id,
        len(scene_inputs),
    )
    proposed_scenes = _build_scene_proposals(
        scene_inputs,
        proposed_entities,
        author_id,
    )
    elapsed_seconds = round(perf_counter() - started, 3)
    logger.info(
        "scene_proposal_total: run_id=%s proposed_scene_count=%d elapsed_seconds=%.3f",
        run_id,
        len(proposed_scenes),
        elapsed_seconds,
    )
    return {
        "proposed_scenes": proposed_scenes,
        "elapsed_seconds": elapsed_seconds,
    }


def _safe_json_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _coerce_milestone_title(
    *,
    raw_title: Any,
    description: str,
    boundary_type: str,
    index: int,
) -> str:
    title = _safe_json_text(raw_title)
    if title and not re.fullmatch(r"milestone\s*\d+", title, flags=re.IGNORECASE):
        return " ".join(title.split()[:6])

    # Derive a short descriptive title from the milestone description.
    words = re.findall(r"[A-Za-z0-9']+", description)
    if words:
        return " ".join(words[:6])

    if boundary_type == "begin":
        return "Scene opening beat"
    if boundary_type == "end":
        return "Scene closing beat"
    return f"Key narrative shift {index}"


def _normalize_boundary_type(value: Any) -> str:
    normalized = str(value or "none").strip().lower()
    if normalized in {"begin", "start"}:
        return "begin"
    if normalized in {"end", "finish", "stop"}:
        return "end"
    return "none"


def _normalize_related_to_items(
    raw_items: Any,
    scene_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []

    scene_keys: set[str] = set()
    for item in scene_entities:
        canonical = _canonical_alias(item.get("canonical"))
        alias = _canonical_alias(item.get("alias"))
        if canonical:
            scene_keys.add(canonical)
        if alias:
            scene_keys.add(alias)

    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entity_value = _safe_json_text(raw.get("entity") or raw.get("alias") or raw.get("canonical"))
        entity_key = _canonical_alias(entity_value)
        if not entity_key or entity_key not in scene_keys:
            continue

        relationship_label = _safe_json_text(raw.get("relationship_label"), "related")
        relationship_label = relationship_label.split()[0].lower() if relationship_label else "related"
        relationship_description = _safe_json_text(raw.get("relationship_description"))

        normalized.append(
            {
                "entity": entity_value,
                "relationship_label": relationship_label,
                "relationship_description": relationship_description,
            }
        )
    return normalized


def _ensure_scene_milestone_boundaries(
    milestones: list[dict[str, Any]],
    *,
    scene_ref: str,
    scene_id: str,
    source_entity_instance_id: str | None,
    author_id: str,
) -> list[dict[str, Any]]:
    if not milestones:
        milestones = [
            {
                "milestone_ref": f"milestone-{uuid4()}",
                "scene_ref": scene_ref,
                "scene_id": scene_id,
                "title": "Scene opening beat",
                "label": "Scene begins",
                "description": "The scene begins.",
                "boundary_type": "begin",
                "mentions": [],
                "adjacent_to": [],
                "related_to": [],
                "milestone_order": 1,
                "author": {
                    "created_by_type": "agent",
                    "created_by_author": author_id,
                },
                "derived_from": {
                    "entity_instance_id": source_entity_instance_id,
                },
            },
            {
                "milestone_ref": f"milestone-{uuid4()}",
                "scene_ref": scene_ref,
                "scene_id": scene_id,
                "title": "Scene closing beat",
                "label": "Scene ends",
                "description": "The scene ends.",
                "boundary_type": "end",
                "mentions": [],
                "adjacent_to": [],
                "related_to": [],
                "milestone_order": 2,
                "author": {
                    "created_by_type": "agent",
                    "created_by_author": author_id,
                },
                "derived_from": {
                    "entity_instance_id": source_entity_instance_id,
                },
            },
        ]

    if len(milestones) == 1:
        only_item = milestones[0]
        first = {**only_item, "boundary_type": "begin", "milestone_order": 1}
        second = {
            **only_item,
            "milestone_ref": f"milestone-{uuid4()}",
            "boundary_type": "end",
            "milestone_order": 2,
        }
        milestones = [first, second]

    has_begin = any(item.get("boundary_type") == "begin" for item in milestones)
    has_end = any(item.get("boundary_type") == "end" for item in milestones)

    if not has_begin:
        milestones[0]["boundary_type"] = "begin"
    if not has_end:
        milestones[-1]["boundary_type"] = "end"

    for idx, item in enumerate(milestones, start=1):
        boundary_type = _normalize_boundary_type(item.get("boundary_type"))
        item["boundary_type"] = boundary_type
        title = _coerce_milestone_title(
            raw_title=item.get("title") or item.get("name") or item.get("label"),
            description=_safe_json_text(item.get("description")),
            boundary_type=boundary_type,
            index=idx,
        )
        item["title"] = title
        # Keep legacy key for older consumers while standardizing title.
        item["label"] = title
        item["milestone_order"] = idx

    return milestones


def _parse_milestone_extraction(response_text: str, scene_ref: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_extract_json_block(response_text))
        milestones = payload.get("milestones")
        if isinstance(milestones, list):
            return [item for item in milestones if isinstance(item, dict)]
    except Exception as exc:
        logger.warning(
            "milestone_parse_error: scene_ref=%s error=%s",
            scene_ref,
            exc,
        )
    return []


async def _run_milestone_proposal_phase(
    *,
    run_id: str,
    llm_client: OpenAIClient,
    model: str,
    proposed_scenes: list[dict[str, Any]],
    author_id: str,
    instructions: str | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    semaphore = asyncio.Semaphore(MILESTONE_EXTRACTION_CONCURRENCY)

    logger.info(
        "milestone_proposal_start: run_id=%s scene_count=%d concurrency=%d",
        run_id,
        len(proposed_scenes),
        MILESTONE_EXTRACTION_CONCURRENCY,
    )

    async def _process_scene(scene: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            scene_ref = str(scene.get("scene_ref") or "")
            scene_id = str(scene.get("scene_id") or "")
            scene_entities = list(scene.get("related_to") or [])

            aliases = [
                item.get("alias") or item.get("canonical")
                for item in scene_entities
                if isinstance(item, dict)
            ]
            aliases = [alias for alias in aliases if alias]

            prompt = ARECHITECT_MILESTONE_PROPOSAL_PROMPT.format(
                scene_ref=scene_ref,
                scene_name=_safe_json_text(scene.get("scene_name"), "Unnamed Scene"),
                scene_description=_safe_json_text(scene.get("scene_description")),
                scene_text=_safe_json_text(scene.get("scene_text")),
                scene_entities=json.dumps(aliases, ensure_ascii=False),
            )
            instructions_text = str(instructions or "").strip()
            if instructions_text:
                prompt = (
                    f"{prompt}\n\nFrontend instructions (authoritative constraints):\n"
                    f"{instructions_text}"
                )

            try:
                response = await llm_client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
            except Exception as exc:
                logger.warning(
                    "milestone_proposal_scene_error: run_id=%s scene_ref=%s error=%s",
                    run_id,
                    scene_ref,
                    exc,
                )
                response = "{}"

            raw_items = _parse_milestone_extraction(response, scene_ref)
            milestones: list[dict[str, Any]] = []

            for raw_idx, item in enumerate(raw_items, start=1):
                description = _safe_json_text(item.get("description"), "")
                boundary_type = _normalize_boundary_type(item.get("boundary_type"))
                title = _coerce_milestone_title(
                    raw_title=item.get("title") or item.get("name") or item.get("label"),
                    description=description,
                    boundary_type=boundary_type,
                    index=raw_idx,
                )

                mentions = item.get("mentions") if isinstance(item.get("mentions"), list) else []
                mentions = [str(value).strip() for value in mentions if str(value).strip()]

                adjacent_to = item.get("adjacent_to") if isinstance(item.get("adjacent_to"), list) else []
                adjacent_to = [str(value).strip() for value in adjacent_to if str(value).strip()]

                related_to = _normalize_related_to_items(item.get("related_to"), scene_entities)

                milestones.append(
                    {
                        "milestone_ref": f"milestone-{uuid4()}",
                        "scene_ref": scene_ref,
                        "scene_id": scene_id,
                        "title": title,
                        "label": title,
                        "description": description,
                        "boundary_type": boundary_type,
                        "mentions": mentions,
                        "adjacent_to": adjacent_to,
                        "related_to": related_to,
                        "milestone_order": raw_idx,
                        "author": {
                            "created_by_type": "agent",
                            "created_by_author": author_id,
                        },
                        "derived_from": {
                            "entity_instance_id": scene.get("source_entity_instance_id"),
                        },
                    }
                )

            milestones = _ensure_scene_milestone_boundaries(
                milestones,
                scene_ref=scene_ref,
                scene_id=scene_id,
                source_entity_instance_id=scene.get("source_entity_instance_id"),
                author_id=author_id,
            )

            logger.info(
                "milestone_proposal_scene_total: run_id=%s scene_ref=%s milestone_count=%d",
                run_id,
                scene_ref,
                len(milestones),
            )

            return {
                "scene_ref": scene_ref,
                "scene_id": scene_id,
                "milestones": milestones,
            }

    by_scene = await asyncio.gather(*(_process_scene(scene) for scene in proposed_scenes))
    # Milestones are always coerced to at least begin/end per scene.
    removed_scene_refs: list[str] = []
    kept_scene_refs = [row.get("scene_ref") for row in by_scene if row.get("scene_ref")]

    all_milestones: list[dict[str, Any]] = []
    for row in by_scene:
        all_milestones.extend(row.get("milestones", []))

    elapsed_seconds = round(perf_counter() - started, 3)
    logger.info(
        "milestone_proposal_total: run_id=%s milestone_count=%d elapsed_seconds=%.3f",
        run_id,
        len(all_milestones),
        elapsed_seconds,
    )

    return {
        "proposed_milestones": all_milestones,
        "per_scene": by_scene,
        "scene_refs_with_milestones": kept_scene_refs,
        "removed_scene_refs": removed_scene_refs,
        "removed_scene_count": len(removed_scene_refs),
        "elapsed_seconds": elapsed_seconds,
    }


def _classify_entities(
    scene_results: list[dict[str, Any]],
    existing_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    deduped: dict[str, dict[str, Any]] = {}
    existing_by_canonical: dict[str, dict[str, Any]] = {}
    for node in existing_nodes:
        key = _canonical_alias(node.get("alias"))
        if key and key not in existing_by_canonical:
            existing_by_canonical[key] = node

    for scene in scene_results:
        for entity in scene.get("entities", []):
            canonical = _canonical_alias(entity.get("name"))
            if not canonical:
                continue
            if canonical not in deduped:
                deduped[canonical] = {
                    "canonical": canonical,
                    "name": entity.get("name") or "",
                    "ontology": entity.get("ontology") or "",
                    "confidence_values": [],
                    "whys": [],
                    "scene_refs": [],
                }
            entry = deduped[canonical]
            name = entity.get("name") or ""
            if len(name) > len(entry["name"]):
                entry["name"] = name
            if entity.get("ontology"):
                entry["ontology"] = entity["ontology"]
            confidence = entity.get("confidence")
            if isinstance(confidence, (int, float)):
                entry["confidence_values"].append(float(confidence))
            why = (entity.get("why") or "").strip()
            if why and why not in entry["whys"]:
                entry["whys"].append(why)
            scene_ref = scene.get("scene_ref")
            if scene_ref and scene_ref not in entry["scene_refs"]:
                entry["scene_refs"].append(scene_ref)

    proposed_entities: list[dict[str, Any]] = []
    status_ontology_counts: dict[str, dict[str, int]] = {
        "new": defaultdict(int),
        "updated": defaultdict(int),
    }

    status_by_canonical: dict[str, dict[str, Any]] = {}
    for canonical, entry in deduped.items():
        match = _find_existing_match(canonical, existing_by_canonical)
        status = "updated" if match else "new"
        ontology = entry.get("ontology") or "Unknown"
        status_ontology_counts[status][ontology] += 1
        avg_confidence = 0.0
        if entry["confidence_values"]:
            avg_confidence = round(
                sum(entry["confidence_values"]) / len(entry["confidence_values"]),
                4,
            )

        resolved = {
            "name": entry["name"],
            "canonical": canonical,
            "ontology": ontology,
            "confidence": avg_confidence,
            "why": entry["whys"][0] if entry["whys"] else "",
            "status": status,
            "matched_node_id": match.get("node_id") if match else None,
            "scene_refs": entry["scene_refs"],
        }
        status_by_canonical[canonical] = resolved
        proposed_entities.append(resolved)

    for scene in scene_results:
        enriched_entities: list[dict[str, Any]] = []
        for entity in scene.get("entities", []):
            canonical = _canonical_alias(entity.get("name"))
            resolved = status_by_canonical.get(canonical)
            enriched_entities.append(
                {
                    **entity,
                    "status": (resolved or {}).get("status", "new"),
                    "matched_node_id": (resolved or {}).get("matched_node_id"),
                }
            )
        scene["entities"] = enriched_entities

    return {
        "proposed_entities": proposed_entities,
        "updated_count": sum(1 for item in proposed_entities if item["status"] == "updated"),
        "new_count": sum(1 for item in proposed_entities if item["status"] == "new"),
        "status_ontology_counts": {
            status: dict(ontology_counts)
            for status, ontology_counts in status_ontology_counts.items()
        },
        "scene_results": scene_results,
    }


@celery_app.task(name="architect.analyze_instance")
def analyze_instance(
    run_id: str,
    agent_id: str,
    request_payload: dict[str, Any],
    *,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Background job entry point for the Architect step-one workflow."""

    description = (
        "Architect analysis for agent "
        f"{agent_id} on instance {request_payload.get('ontology_instance_id')}"
    )
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.ARCHITECT_ANALYSIS,
            description=description,
            celery_task_id=analyze_instance.request.id,
            details={
                "run_id": run_id,
                "agent_id": agent_id,
                "ontology_instance_id": request_payload.get("ontology_instance_id"),
            },
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(_attach_job_to_run(run_id, job_id))
        run_async(
            update_job_progress(
                job_id, 0.05, {"status": "Preparing architect analysis"}
            )
        )

        result = run_async(
            _execute_architect_pipeline(
                run_id=run_id,
                agent_id=agent_id,
                request_payload=request_payload,
                job_id=job_id,
            )
        )

        run_async(
            mark_job_done(
                job_id,
                {
                    "run_id": run_id,
                    "status": "completed",
                    "pipeline": "architect_scene_pipeline",
                    "pipeline_version": result.get("pipeline_version", "scene-centric-job-details-v1"),
                    "pipeline_output_transport": "background_job_details",
                    "pipeline_output": result.get("pipeline_output"),
                    "chunk_count": result.get("chunk_count", 0),
                    "scene_count": result.get("scene_count", 0),
                    "milestone_count": result.get("milestone_count", 0),
                    "updated_count": result.get("updated_count", 0),
                    "new_count": result.get("new_count", 0),
                    "removed_scene_count": result.get("removed_scene_count", 0),
                },
            )
        )
        return {"job_id": job_id, "status": "success", "run_id": run_id}

    except Exception as exc:
        logger.error(
            "Architect analysis failed for run %s: %s", run_id, exc, exc_info=True
        )
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _attach_job_to_run(run_id: str, job_id: int) -> None:
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        await repo.attach_background_job(run_id, job_id)
        await session.commit()


async def _execute_architect_pipeline(
    *,
    run_id: str,
    agent_id: str,
    request_payload: dict[str, Any],
    job_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    if not is_openai_configured(settings):
        raise RuntimeError("OpenAI API key not configured")
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        agent_repo = AgentRepository(session)
        agent = await agent_repo.get_by_id(agent_id)
        if not agent:
            raise ValueError("Agent not found")

        run = await repo.get_run(run_id, with_proposals=False)
        if not run:
            raise ValueError("Architect analysis run not found")

        await repo.update_run_status(run_id, status=ArchitectRunStatus.RUNNING)
        await session.commit()

        ontology_instance_id = request_payload["ontology_instance_id"]
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as graph_session:
            instance_service = OntologyInstanceService(session, graph_session)
            await update_job_progress(job_id, 0.15, {"status": "Loading instance"})

            ontology_instance = await instance_service.get_instance(
                ontology_instance_id
            )
            ontology_id = ontology_instance.ontology_id
            ontology_repo = OntologyRepository(session)
            ontology_entities = await ontology_repo.list_entities(ontology_id)
            ontology_definitions = _format_ontology_definitions_from_entities(
                list(ontology_entities)
            )
            allowed_ontology_names = _build_allowed_ontology_map(list(ontology_entities))
            run.ontology_id = ontology_id
            await session.flush()

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

            existing_nodes = _existing_nodes_from_instance(ontology_instance)
            # Always merge with graph catalogue so reconciliation sees persisted entities
            # beyond whatever happens to be present in the loaded instance payload.
            graph_retriever = Neo4jGraphRetriever(graph_session)
            graph_nodes = await _load_existing_nodes(
                graph_retriever,
                [ontology_id],
            )
            if graph_nodes:
                merged_nodes: dict[str, dict[str, Any]] = {}
                for node in [*existing_nodes, *graph_nodes]:
                    node_id = str(node.get("node_id") or "").strip()
                    alias = str(node.get("alias") or "").strip()
                    if not node_id or not alias:
                        continue
                    merged_nodes[node_id] = node
                existing_nodes = list(merged_nodes.values())

            try:
                result = await _run_scene_centric_chunking_test(
                    run_id=run_id,
                    agent_id=agent_id,
                    job_id=job_id,
                    ontology_instance_id=ontology_instance_id,
                    ontology_instance=ontology_instance,
                    llm_client=llm_client,
                    model=model_policy.get_model(LLMTask.ARCHITECT_EXTRACT),
                    ontology_definitions=ontology_definitions,
                    allowed_ontology_names=allowed_ontology_names,
                    existing_nodes=existing_nodes,
                    celery_task_id=analyze_instance.request.id,
                    author_id=agent_id,
                )
            finally:
                await llm_client.aclose()

        await repo.update_run_status(
            run_id,
            status=ArchitectRunStatus.COMPLETED,
            input_chunk_count=result.get("chunk_count"),
        )
        current_settings = run.settings if isinstance(run.settings, dict) else {}
        run.settings = {
            **current_settings,
            "pipeline": "architect_scene_pipeline",
            "pipeline_version": result.get("pipeline_version", "scene-centric-job-details-v1"),
            "pipeline_output_transport": "background_job_details",
            "pipeline_output_format": "json",
        }
        await session.commit()

        await update_job_progress(
            job_id,
            0.95,
            {
                "status": "Architect analysis completed",
                "chunk_count": result.get("chunk_count", 0),
                "scene_count": result.get("scene_count", 0),
            },
        )
        return result


async def _run_scene_centric_chunking_test(
    *,
    run_id: str,
    agent_id: str,
    job_id: int,
    ontology_instance_id: str,
    ontology_instance: Any,
    llm_client: OpenAIClient,
    model: str,
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
    existing_nodes: list[dict[str, Any]],
    celery_task_id: str | None,
    author_id: str,
) -> dict[str, Any]:
    """Temporary test path for scene-centric chunking with single consolidated output."""
    total_started = perf_counter()
    await update_job_progress(job_id, 0.25, {"status": "Scene-centric chunking"})

    chunking_phase = await _run_scene_chunking_phase(
        run_id=run_id,
        ontology_instance=ontology_instance,
        llm_client=llm_client,
        model=model,
    )
    all_chunk_results = chunking_phase["chunk_results"]
    total_chunks = chunking_phase["chunk_count"]
    total_paragraphs = chunking_phase["paragraph_count"]
    total_scenes = chunking_phase["scene_count"]
    scene_chunking_elapsed_seconds = chunking_phase["elapsed_seconds"]

    await update_job_progress(job_id, 0.7, {"status": "Scene entity discovery"})
    entity_phase = await _run_entity_proposal_phase(
        run_id=run_id,
        llm_client=llm_client,
        model=model,
        ontology_definitions=ontology_definitions,
        allowed_ontology_names=allowed_ontology_names,
        existing_nodes=existing_nodes,
        chunk_results=all_chunk_results,
    )
    scene_inputs = entity_phase["scene_inputs"]
    proposed_entities = entity_phase["proposed_entities"]
    updated_count = entity_phase["updated_count"]
    new_count = entity_phase["new_count"]
    status_ontology_counts = entity_phase["status_ontology_counts"]
    entity_discovery_elapsed_seconds = entity_phase["elapsed_seconds"]

    await update_job_progress(job_id, 0.82, {"status": "Scene proposal"})
    scene_proposal_phase = _run_scene_proposal_phase(
        run_id=run_id,
        scene_inputs=scene_inputs,
        proposed_entities=proposed_entities,
        author_id=author_id,
    )
    proposed_scenes = scene_proposal_phase["proposed_scenes"]
    scene_proposal_elapsed_seconds = scene_proposal_phase["elapsed_seconds"]

    await update_job_progress(job_id, 0.9, {"status": "Milestone proposal"})
    milestone_phase = await _run_milestone_proposal_phase(
        run_id=run_id,
        llm_client=llm_client,
        model=model,
        proposed_scenes=proposed_scenes,
        author_id=author_id,
    )
    proposed_milestones = milestone_phase["proposed_milestones"]
    milestones_per_scene = milestone_phase["per_scene"]
    removed_scene_refs = milestone_phase["removed_scene_refs"]
    removed_scene_count = milestone_phase["removed_scene_count"]
    milestone_proposal_elapsed_seconds = milestone_phase["elapsed_seconds"]

    pipeline_output_payload = {
        "run_id": run_id,
        "agent_id": agent_id,
        "ontology_instance_id": ontology_instance_id,
        "pipeline": "architect_scene_pipeline",
        "pipeline_version": "scene-centric-single-output-v1",
        "summary": {
            "chunk_count": total_chunks,
            "paragraph_count": total_paragraphs,
            "scene_count": len(proposed_scenes),
            "milestone_count": len(proposed_milestones),
            "removed_scene_count": removed_scene_count,
            "removed_scene_refs": removed_scene_refs,
            "updated_count": updated_count,
            "new_count": new_count,
            "discovery_summary": {
                "deduped_proposal_count": len(proposed_entities),
                "updated_count": updated_count,
                "new_count": new_count,
                "updated_by_ontology": status_ontology_counts.get("updated", {}),
                "new_by_ontology": status_ontology_counts.get("new", {}),
            },
        },
        "timings": {
            "scene_chunking_elapsed_seconds": scene_chunking_elapsed_seconds,
            "entity_discovery_elapsed_seconds": entity_discovery_elapsed_seconds,
            "scene_proposal_elapsed_seconds": scene_proposal_elapsed_seconds,
            "milestone_proposal_elapsed_seconds": milestone_proposal_elapsed_seconds,
        },
        "outputs": {
            "entity_proposals": proposed_entities,
            "scenes": proposed_scenes,
            "milestones": proposed_milestones,
            "milestones_per_scene": milestones_per_scene,
        },
        "debug": {
            "chunk_results": all_chunk_results,
        },
    }

    # Persist real run outputs to local_tests for frontend inspection.
    # Use run_id as folder name so host lookup is predictable.
    output_dir = _resolve_local_tests_output_dir(run_id)

    scene_chunk_payload = {
        "run_id": run_id,
        "agent_id": agent_id,
        "ontology_instance_id": ontology_instance_id,
        "chunk_count": total_chunks,
        "paragraph_count": total_paragraphs,
        "scene_count": total_scenes,
        "elapsed_seconds": scene_chunking_elapsed_seconds,
        "chunks": all_chunk_results,
    }
    entity_proposal_payload = {
        "run_id": run_id,
        "agent_id": agent_id,
        "ontology_instance_id": ontology_instance_id,
        "scene_count": len(scene_inputs),
        "entity_discovery_elapsed_seconds": entity_discovery_elapsed_seconds,
        "discovery_summary": {
            "deduped_proposal_count": len(proposed_entities),
            "updated_count": updated_count,
            "new_count": new_count,
            "updated_by_ontology": status_ontology_counts.get("updated", {}),
            "new_by_ontology": status_ontology_counts.get("new", {}),
        },
        "proposed_entities": proposed_entities,
    }
    proposed_scenes_payload = {
        "run_id": run_id,
        "agent_id": agent_id,
        "ontology_instance_id": ontology_instance_id,
        "scene_count": len(proposed_scenes),
        "scene_proposal_elapsed_seconds": scene_proposal_elapsed_seconds,
        "proposed_scenes": proposed_scenes,
    }
    proposed_milestones_payload = {
        "run_id": run_id,
        "agent_id": agent_id,
        "ontology_instance_id": ontology_instance_id,
        "scene_count": len(proposed_scenes),
        "milestone_count": len(proposed_milestones),
        "removed_scene_count": removed_scene_count,
        "removed_scene_refs": removed_scene_refs,
        "milestone_proposal_elapsed_seconds": milestone_proposal_elapsed_seconds,
        "milestones_per_scene": milestones_per_scene,
        "proposed_milestones": proposed_milestones,
    }

    local_artifacts = {
        "scene_chunk": _write_local_json_artifact(
            output_dir=output_dir,
            filename="scene_chunk.json",
            payload=scene_chunk_payload,
        ),
        "entity_proposal": _write_local_json_artifact(
            output_dir=output_dir,
            filename="entity_proposal.json",
            payload=entity_proposal_payload,
        ),
        "proposed_scenes": _write_local_json_artifact(
            output_dir=output_dir,
            filename="proposed_scenes.json",
            payload=proposed_scenes_payload,
        ),
        "proposed_milestones": _write_local_json_artifact(
            output_dir=output_dir,
            filename="proposed_milestones.json",
            payload=proposed_milestones_payload,
        ),
        "pipeline_output": _write_local_json_artifact(
            output_dir=output_dir,
            filename="pipeline_output.json",
            payload=pipeline_output_payload,
        ),
    }

    logger.info(
        "analysis_local_artifacts_written: run_id=%s output_dir=%s files=%s",
        run_id,
        output_dir,
        local_artifacts,
    )

    logger.info(
        "pipeline_output_built: run_id=%s scene_count=%d milestone_count=%d entity_count=%d",
        run_id,
        len(proposed_scenes),
        len(proposed_milestones),
        len(proposed_entities),
    )

    total_elapsed_seconds = round(perf_counter() - total_started, 3)
    logger.info(
        "scene_pipeline_total_timing: run_id=%s scene_chunking=%.3f entity_proposal=%.3f scene_proposal=%.3f milestone_proposal=%.3f total=%.3f",
        run_id,
        scene_chunking_elapsed_seconds,
        entity_discovery_elapsed_seconds,
        scene_proposal_elapsed_seconds,
        milestone_proposal_elapsed_seconds,
        total_elapsed_seconds,
    )

    return {
        "chunk_count": total_chunks,
        "scene_count": len(proposed_scenes),
        "paragraph_count": total_paragraphs,
        "pipeline_output": pipeline_output_payload,
        "local_output_dir": str(output_dir),
        "local_artifacts": local_artifacts,
        "milestone_count": len(proposed_milestones),
        "removed_scene_count": removed_scene_count,
        "updated_count": updated_count,
        "new_count": new_count,
        "entity_discovery_elapsed_seconds": entity_discovery_elapsed_seconds,
        "scene_chunking_elapsed_seconds": scene_chunking_elapsed_seconds,
        "scene_proposal_elapsed_seconds": scene_proposal_elapsed_seconds,
        "milestone_proposal_elapsed_seconds": milestone_proposal_elapsed_seconds,
        "total_elapsed_seconds": total_elapsed_seconds,
        "pipeline_version": "scene-centric-job-details-v1",
        "pipeline_output_transport": "background_job_details",
    }
