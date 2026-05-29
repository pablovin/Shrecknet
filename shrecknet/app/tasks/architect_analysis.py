from __future__ import annotations

import asyncio
from collections import defaultdict
from difflib import SequenceMatcher
import inspect
import json
import logging
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import LLMModelTarget, get_settings, is_shreckllm_configured
from app.graph.neo4j import get_driver
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.llm.runtime_control import fetch_shreckllm_runtime, resolve_effective_architect_concurrency
from app.jobs.shrecknet import validate_or_repair_json
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.jobs.architect import prompts as architect_prompts
from app.jobs.architect.schemas import (
    ChunkExtractionResponse,
    SceneEntityBatchExtractionResponse,
)
from app.jobs.architect.scene_centric_chunking import (
    build_scene_chunks_from_sources,
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

ENTITY_PROPOSAL_BATCH_SIZE = 3
ENTITY_SCENE_TEXT_MAX_CHARS = 1_400
MILESTONE_BATCH_SIZE = 2
MILESTONE_SCENE_TEXT_MAX_CHARS = 2_400
_ARCHITECT_CONCURRENCY: int | None = None
MAX_SCENES_AFTER_MERGE = 20


def initialize_architect_concurrency(*, concurrency: int) -> None:
    global _ARCHITECT_CONCURRENCY
    _ARCHITECT_CONCURRENCY = max(1, int(concurrency))


def _scene_entity_extraction_concurrency() -> int:
    if _ARCHITECT_CONCURRENCY is None:
        raise RuntimeError("Architect concurrency not initialized from shreckLLM runtime")
    return _ARCHITECT_CONCURRENCY


def _milestone_extraction_concurrency() -> int:
    if _ARCHITECT_CONCURRENCY is None:
        raise RuntimeError("Architect concurrency not initialized from shreckLLM runtime")
    return _ARCHITECT_CONCURRENCY

def _safe_scene_title(scene: dict[str, Any], fallback_index: int) -> str:
    title = str(scene.get("name") or "")
    return title if title.strip() else f"Scene {fallback_index + 1}"


def _format_exception_message(exc: Exception) -> str:
    raw = str(exc).strip()
    if raw:
        return f"{type(exc).__name__}: {raw}"
    return f"{type(exc).__name__}: <empty_message>"


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
    output_path = output_dir / filename
    try:
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(output_path)
    except OSError as exc:
        logger.warning(
            "analysis_local_artifact_write_error: file=%s error=%s",
            output_path,
            exc,
        )
        return None


def _extract_json_block(raw: str) -> str:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response")
    return raw[start : end + 1]


def _format_step_usage_delta(
    llm_client: ShreckLLMClient,
    start_event_index: int,
) -> dict[str, Any]:
    return llm_client.get_usage_summary_since(start_event_index)


def _log_step_usage(
    *,
    run_id: str,
    step: str,
    usage: dict[str, Any],
) -> None:
    logger.info(
        "architect_analysis_llm_usage_step run_id=%s step=%s totals=%s by_model=%s",
        run_id,
        step,
        usage.get("totals"),
        usage.get("by_model"),
    )


def _log_run_llm_usage_summary(*, run_id: str, usage_summary: dict[str, Any]) -> None:
    totals = usage_summary.get("totals") if isinstance(usage_summary, dict) else {}
    totals = totals if isinstance(totals, dict) else {}
    logger.info(
        "architect_analysis_llm_usage_summary run_id=%s total_calls=%d input_tokens_est=%d memory_tokens_est=%d output_tokens=%d total_tokens=%d estimated_cost_usd=%.6f by_model=%s by_tag=%s",
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


def _build_frontend_llm_usage_summary(usage_summary: dict[str, Any] | None) -> dict[str, Any]:
    by_tag = (usage_summary or {}).get("by_tag") if isinstance(usage_summary, dict) else {}
    by_tag = by_tag if isinstance(by_tag, dict) else {}
    tag_to_step = {
        "architect.scene_discovery": "architect.scene_discovery",
        "architect.scene_merging": "architect.scene_merging",
        "agents.json_repair": "agents.json_repair",
        "architect.scene_rewrite": "architect.scene_rewrite",
        "architect.entity_extraction": "architect.entity_extraction",
        "architect.milestone_proposal": "architect.milestone_proposal",
    }

    steps: dict[str, dict[str, int]] = {}
    for tag, step_name in tag_to_step.items():
        row = by_tag.get(tag) if isinstance(by_tag.get(tag), dict) else {}
        steps[step_name] = {
            "calls": int((row or {}).get("calls") or 0),
            "input_tokens_est": int((row or {}).get("input_tokens_est") or 0),
            "output_tokens": int((row or {}).get("output_tokens") or 0),
            "total_tokens": int((row or {}).get("total_tokens") or 0),
        }
    json_repair_totals = {
        "calls": 0,
        "input_tokens_est": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for tag, row in by_tag.items():
        if not isinstance(row, dict):
            continue
        tag_name = str(tag or "")
        if "json_repair" not in tag_name and tag_name != "agents.json_repair":
            continue
        json_repair_totals["calls"] += int(row.get("calls") or 0)
        json_repair_totals["input_tokens_est"] += int(row.get("input_tokens_est") or 0)
        json_repair_totals["output_tokens"] += int(row.get("output_tokens") or 0)
        json_repair_totals["total_tokens"] += int(row.get("total_tokens") or 0)

    return {"steps": steps, "json_repair_totals": json_repair_totals}


def _compress_scene_text_for_milestone_prompt(scene_text: str, max_chars: int) -> str:
    text = str(scene_text or "").strip()
    if not text or len(text) <= max_chars:
        return text

    half = max(400, max_chars // 2)
    head = text[:half].rstrip()
    tail = text[-half:].lstrip()
    return f"{head}\n...\n{tail}"


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


def _parse_scene_entity_batch_extraction(
    response_text: str,
) -> SceneEntityBatchExtractionResponse:
    try:
        payload = json.loads(_extract_json_block(response_text))
        if isinstance(payload.get("scenes"), list):
            return SceneEntityBatchExtractionResponse.model_validate(payload)

        # Backward-compatible single-scene shape for model drift in tests.
        if isinstance(payload.get("entities"), list):
            return SceneEntityBatchExtractionResponse.model_validate(
                {"scenes": [{"scene_ref": "", "entities": payload["entities"]}]}
            )
    except Exception as exc:
        logger.warning("scene_entity_batch_parse_error: error=%s", exc)
    return SceneEntityBatchExtractionResponse(scenes=[])


async def _parse_scene_entity_batch_extraction_with_repair(
    *,
    llm_client: ShreckLLMClient,
    repair_model: str | LLMModelTarget,
    response_text: str,
) -> SceneEntityBatchExtractionResponse:
    parsed = _parse_scene_entity_batch_extraction(response_text)
    if parsed.scenes:
        return parsed
    try:
        repaired_payload = await validate_or_repair_json(
            llm_client=llm_client,
            model=repair_model,
            raw_text=response_text,
            schema_hint='{"scenes":[{"scene_ref":"...","entities":[{"name":"...","ontology":"...","status":"existing|new","matched_alias":null,"confidence":0.0,"why":"..."}]}]}',
            usage_tag="agents.json_repair",
        )
    except Exception as exc:
        logger.warning("scene_entity_batch_repair_error: error=%s", exc)
        return SceneEntityBatchExtractionResponse(scenes=[])
    return _parse_scene_entity_batch_extraction(json.dumps(repaired_payload))


def _normalize_ontology_name(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", raw)


def _normalize_ontology_name_loose(value: str | None) -> str:
    raw = _normalize_ontology_name(value)
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw.endswith("es") and len(raw) > 3:
        singular = raw[:-2]
        if singular:
            return singular
    if raw.endswith("s") and len(raw) > 2:
        singular = raw[:-1]
        if singular:
            return singular
    return raw


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


def _resolve_allowed_ontology_name(
    value: str | None, allowed_ontology_names: dict[str, str]
) -> str | None:
    direct = allowed_ontology_names.get(_normalize_ontology_name(value))
    if direct:
        return direct
    loose = _normalize_ontology_name_loose(value)
    if not loose:
        return None
    for key, canonical in allowed_ontology_names.items():
        key_loose = _normalize_ontology_name_loose(key)
        if key_loose == loose:
            return canonical
    return None


def _resolve_allowed_ontology_name_fuzzy(
    value: str | None, allowed_ontology_names: dict[str, str]
) -> str | None:
    mapped = _resolve_allowed_ontology_name(value, allowed_ontology_names)
    if mapped:
        return mapped
    query = _normalize_ontology_name_loose(value)
    if not query:
        return None
    best_name: str | None = None
    best_score = 0.0
    for key, canonical in allowed_ontology_names.items():
        key_loose = _normalize_ontology_name_loose(key)
        if not key_loose:
            continue
        score = SequenceMatcher(None, query, key_loose).ratio()
        if score > best_score:
            best_score = score
            best_name = canonical
    if best_name and best_score >= 0.72:
        return best_name
    return None


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


def _strip_honorific_prefix(alias: str | None) -> str:
    value = str(alias or "").strip()
    if not value:
        return ""
    tokens = value.split()
    if not tokens:
        return ""
    honorifics = {"lady", "dame", "sir", "lord", "king", "queen", "prince", "princess"}
    while tokens and tokens[0].lower().strip(".") in honorifics:
        tokens = tokens[1:]
    return " ".join(tokens).strip()


def _canonical_alias_for_lookup(alias: str | None) -> str:
    primary = _canonical_alias(alias)
    stripped = _canonical_alias(_strip_honorific_prefix(alias))
    if primary and stripped and primary != stripped:
        # Keep both behaviors reachable; prefer stripped for matching broad existing aliases.
        return stripped
    return primary or stripped


def _aliases_equivalent(alias_a: str | None, alias_b: str | None) -> bool:
    if not alias_a or not alias_b:
        return False
    if alias_a == alias_b:
        return True
    # Honorific-insensitive exact compare.
    stripped_a = _canonical_alias(_strip_honorific_prefix(alias_a))
    stripped_b = _canonical_alias(_strip_honorific_prefix(alias_b))
    if stripped_a and stripped_b and stripped_a == stripped_b:
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


def _build_existing_entity_prompt_catalogue(
    existing_nodes: list[dict[str, Any]],
    allowed_ontology_names: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Expose only natural-language identity fields to the LLM."""
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in existing_nodes:
        alias = str(node.get("alias") or "").strip()
        ontology = str(node.get("ontology") or "").strip()
        if not alias:
            continue
        # Hard filter: never pass internal node labels / unknown ontology labels to LLM.
        if _normalize_ontology_name(ontology) in {"entityinstance", "scene", "milestone"}:
            continue
        if allowed_ontology_names is not None:
            mapped = _resolve_allowed_ontology_name(ontology, allowed_ontology_names)
            if mapped:
                ontology = mapped
        key = (_canonical_alias(alias), _normalize_ontology_name(ontology))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"alias": alias, "ontology": ontology})
    rows.sort(key=lambda item: (item["ontology"].lower(), item["alias"].lower()))
    return rows


def _resolve_existing_node_by_alias(
    *,
    name: str | None,
    matched_alias: str | None,
    ontology: str | None,
    existing_nodes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    def _is_numeric_token(value: str) -> bool:
        return bool(value) and value.isdigit()

    ontology_key = _normalize_ontology_name(ontology)
    candidates = [matched_alias, name]
    for candidate in candidates:
        canonical = _canonical_alias_for_lookup(candidate)
        if not canonical:
            continue
        relaxed_match: dict[str, Any] | None = None
        for node in existing_nodes:
            node_alias = str(node.get("alias") or "")
            node_ontology = _normalize_ontology_name(str(node.get("ontology") or ""))
            node_canonical = _canonical_alias_for_lookup(node_alias)
            same_alias = canonical == node_canonical or _aliases_equivalent(canonical, node_canonical)
            if not same_alias:
                continue
            # Some existing-node sources carry ontology ids (numeric) instead of ontology names.
            # In that case, do not hard-filter by ontology text, or we incorrectly miss true alias matches.
            if (
                ontology_key
                and node_ontology
                and not _is_numeric_token(ontology_key)
                and not _is_numeric_token(node_ontology)
                and node_ontology != ontology_key
            ):
                # Keep an alias-level fallback candidate in case ontology metadata is inconsistent.
                if relaxed_match is None:
                    relaxed_match = node
                continue
            return node
        if relaxed_match is not None:
            return relaxed_match
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


async def _classify_entities_with_reconciliation(
    *,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
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
                    "matched_node_id": None,
                    "matched_alias": None,
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
            if (
                str(entity.get("status") or "").strip().lower() == "existing"
            ):
                matched_node_id = str(entity.get("matched_node_id") or "").strip()
                matched_alias = str(entity.get("matched_alias") or entity.get("name") or "").strip()
                if not matched_node_id:
                    matched = _resolve_existing_node_by_alias(
                        name=entity.get("name"),
                        matched_alias=matched_alias,
                        ontology=mapped_ontology,
                        existing_nodes=existing_nodes,
                    )
                    matched_node_id = str((matched or {}).get("node_id") or "").strip()
                    matched_alias = str((matched or {}).get("alias") or matched_alias).strip()
                if matched_node_id:
                    entry["matched_node_id"] = matched_node_id
                    entry["matched_alias"] = matched_alias

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

    existing_map: dict[str, dict[str, Any]] = {}
    new_set: set[str] = set()
    for canonical, entry in deduped.items():
        matched_id = entry.get("matched_node_id")
        if canonical and matched_id:
            existing_map[canonical] = {
                "proposed_name": entry.get("name"),
                "matched_node_id": matched_id,
                "matched_alias": entry.get("matched_alias"),
                "ontology": entry.get("ontology"),
            }
        else:
            new_set.add(canonical)

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

        matched_node_id = matched.get("matched_node_id") if (is_existing and matched) else None
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

    proposal_index_by_canonical: dict[str, int] = {}
    for proposal_index, proposal in enumerate(proposed_entities):
        canonical = _canonical_alias(proposal.get("canonical") or proposal.get("name"))
        if canonical and canonical not in proposal_index_by_canonical:
            proposal_index_by_canonical[canonical] = proposal_index

    def _resolve_status_entry(alias: str | None) -> tuple[str | None, dict[str, Any] | None]:
        candidate = _canonical_alias(alias)
        if not candidate:
            return None, None
        if candidate in status_by_canonical:
            return candidate, status_by_canonical[candidate]
        for key, value in status_by_canonical.items():
            if _aliases_equivalent(candidate, key):
                return key, value
        return None, None

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
                    fetch_result = fetch_method(
                        ontology_id=ontology_id,
                        skip=skip,
                        limit=batch_size,
                    )
                    batch = (
                        await fetch_result
                        if inspect.isawaitable(fetch_result)
                        else fetch_result
                    )
                    if isinstance(batch, list):
                        pass
                    elif isinstance(batch, tuple):
                        batch = list(batch)
                    elif isinstance(batch, set):
                        batch = list(batch)
                    else:
                        batch = []
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
                node_label = str(getattr(result, "node_label", "") or "").strip()
                if node_label and node_label != "EntityInstance":
                    continue
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
    seen_scene_refs: set[str] = set()

    def _safe_ref_token(value: Any, fallback: str) -> str:
        token = str(value or "").strip().lower()
        token = re.sub(r"[^a-z0-9]+", "_", token).strip("_")
        return token or fallback

    for chunk in chunk_results:
        if chunk.get("status") != "ok":
            continue
        chunk_index = chunk.get("chunk_index")
        chunk_entity_instance_id = chunk.get("entity_instance_id")
        chunk_entity_alias = chunk.get("entity_alias")
        for idx, scene in enumerate(chunk.get("scenes", [])):
            scene_entity_instance_id = scene.get("source_entity_instance_id") or chunk_entity_instance_id
            scene_entity_alias = scene.get("source_entity_alias") or chunk_entity_alias
            scene_id = scene.get("scene_id", idx)
            base_scene_ref = (
                f"entity_{_safe_ref_token(scene_entity_instance_id, 'unknown')}"
                f"_chunk_{_safe_ref_token(chunk_index, '0')}"
                f"_scene_{_safe_ref_token(scene_id, str(idx))}"
            )
            scene_ref = base_scene_ref
            duplicate_index = 2
            while scene_ref in seen_scene_refs:
                scene_ref = f"{base_scene_ref}__dup_{duplicate_index}"
                duplicate_index += 1
            if scene_ref != base_scene_ref:
                logger.warning(
                    "scene_ref_collision_resolved: base=%s resolved=%s entity_id=%s chunk_index=%s scene_id=%s",
                    base_scene_ref,
                    scene_ref,
                    scene_entity_instance_id,
                    chunk_index,
                    scene_id,
                )
            seen_scene_refs.add(scene_ref)

            scenes.append(
                {
                    "scene_ref": scene_ref,
                    "chunk_index": chunk_index,
                    "source_entity_instance_id": scene_entity_instance_id,
                    "source_entity_alias": scene_entity_alias,
                    "scene_id": scene_id,
                    "scene_name": scene.get("name") or "",
                    "scene_description": scene.get("description") or "",
                    "scene_text": scene.get("text") or "",
                    "start_paragraph": scene.get("start_paragraph"),
                    "end_paragraph": scene.get("end_paragraph"),
                    "scene_milestones": scene.get("milestones") or [],
                }
            )
    return scenes


async def _run_scene_merge_phase(
    *,
    run_id: str,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
    chunk_results: list[dict[str, Any]],
    max_scenes_after_merge: int = MAX_SCENES_AFTER_MERGE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    max_scenes_after_merge = max(1, int(max_scenes_after_merge or MAX_SCENES_AFTER_MERGE))
    scene_inputs = _flatten_scene_inputs(chunk_results)
    before_count = len(scene_inputs)
    titles_before = [
        str(row.get("scene_name") or "").strip()
        for row in scene_inputs
        if str(row.get("scene_name") or "").strip()
    ]
    if before_count <= max_scenes_after_merge:
        return chunk_results, {
            "applied": False,
            "scene_count_before": before_count,
            "scene_count_after": before_count,
            "scene_titles_before": titles_before,
            "scene_titles_after": titles_before,
        }

    scenes_payload = [
        {
            "scene_ref": row.get("scene_ref"),
            "scene_name": row.get("scene_name"),
            "scene_description": row.get("scene_description"),
        }
        for row in scene_inputs
    ]
    prompt = str(getattr(architect_prompts, "ARCHITECT_SCENE_MERGE_PROMPT", "")).format(
        max_scenes_after_merge=max_scenes_after_merge,
        scenes_payload=json.dumps(scenes_payload, ensure_ascii=False)
    )
    response_text = await llm_client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        usage_tag="architect.scene_merging",
    )
    parsed = await validate_or_repair_json(
        llm_client=llm_client,
        model=repair_model,
        raw_text=response_text if isinstance(response_text, str) else json.dumps(response_text, ensure_ascii=False),
        schema_hint='{"scenes":[{"scene_ref":"merged_1","name":"...","description":"...","source_scene_refs":["..."]}]}',
        usage_tag="agents.json_repair",
    )
    merged_rows = (parsed or {}).get("scenes") if isinstance(parsed, dict) else []
    merged_rows = merged_rows if isinstance(merged_rows, list) else []

    by_scene_ref = {str(s.get("scene_ref") or ""): s for s in scene_inputs}
    normalized_merged: list[dict[str, Any]] = []
    used_refs: set[str] = set()
    for idx, row in enumerate(merged_rows):
        if not isinstance(row, dict):
            continue
        source_refs = [str(x).strip() for x in (row.get("source_scene_refs") or []) if str(x).strip() in by_scene_ref]
        source_refs = [x for x in source_refs if x not in used_refs]
        if not source_refs:
            continue
        for ref in source_refs:
            used_refs.add(ref)
        source_scenes = [by_scene_ref[ref] for ref in source_refs]
        all_pids: list[int] = []
        for src in source_scenes:
            start = int(src.get("start_paragraph") or 0)
            end = int(src.get("end_paragraph") or start)
            if start > 0 and end >= start:
                all_pids.extend(list(range(start, end + 1)))
        all_pids = sorted(set(all_pids))
        merged_text = "\n".join(
            str(src.get("scene_text") or "").strip() for src in source_scenes if str(src.get("scene_text") or "").strip()
        )
        normalized_merged.append(
            {
                "scene_ref": str(row.get("scene_ref") or f"merged_scene_{idx+1}"),
                "chunk_index": source_scenes[0].get("chunk_index"),
                "source_entity_instance_id": source_scenes[0].get("source_entity_instance_id"),
                "source_entity_alias": source_scenes[0].get("source_entity_alias"),
                "scene_id": idx,
                "scene_name": str(row.get("name") or "Merged Scene").strip(),
                "scene_description": str(row.get("description") or "").strip(),
                "scene_text": merged_text,
                "start_paragraph": all_pids[0] if all_pids else None,
                "end_paragraph": all_pids[-1] if all_pids else None,
                "scene_milestones": [],
            }
        )

    # Ensure coverage for any orphan scene refs.
    for ref, scene in by_scene_ref.items():
        if ref in used_refs:
            continue
        normalized_merged.append(scene)

    merged_chunk_result = [{
        "status": "ok",
        "entity_instance_id": None,
        "entity_alias": None,
        "chunk_index": 0,
        "paragraph_count": sum(1 for s in normalized_merged if s.get("start_paragraph") and s.get("end_paragraph")),
        "token_count": 0,
        "paragraph_start": None,
        "paragraph_end": None,
        "marked_paragraphs": "",
        "scenes": [
            {
                "scene_id": scene.get("scene_id"),
                "name": scene.get("scene_name"),
                "description": scene.get("scene_description"),
                "start_paragraph": scene.get("start_paragraph"),
                "end_paragraph": scene.get("end_paragraph"),
                "text": scene.get("scene_text"),
            }
            for scene in normalized_merged
        ],
    }]
    titles_after = [
        str(scene.get("scene_name") or "").strip()
        for scene in normalized_merged
        if str(scene.get("scene_name") or "").strip()
    ]
    logger.info(
        "scene_merge_summary: run_id=%s before=%d after=%d titles_before=%s titles_after=%s",
        run_id,
        before_count,
        len(normalized_merged),
        titles_before,
        titles_after,
    )
    return merged_chunk_result, {
        "applied": True,
        "scene_count_before": before_count,
        "scene_count_after": len(normalized_merged),
        "scene_titles_before": titles_before,
        "scene_titles_after": titles_after,
    }


async def _run_scene_rewrite_phase(
    *,
    run_id: str,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
    chunk_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scene_inputs = _flatten_scene_inputs(chunk_results)
    if not scene_inputs:
        return chunk_results, {"applied": False, "rewritten_count": 0}

    async def _rewrite_single(scene: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        payload = [
            {
                "scene_ref": scene.get("scene_ref"),
                "scene_name": scene.get("scene_name"),
                "scene_description": scene.get("scene_description"),
                "scene_text": scene.get("scene_text"),
            }
        ]
        scene_ref = str(scene.get("scene_ref") or "").strip()
        prompt = str(getattr(architect_prompts, "ARCHITECT_SCENE_REWRITE_PROMPT", "")).format(
            scenes_payload=json.dumps(payload, ensure_ascii=False)
        )
        try:
            response_text = await llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                usage_tag="architect.scene_rewrite",
            )
            parsed = await validate_or_repair_json(
                llm_client=llm_client,
                model=repair_model,
                raw_text=response_text if isinstance(response_text, str) else json.dumps(response_text, ensure_ascii=False),
                schema_hint='{"scenes":[{"scene_ref":"...","scene_description":"...","scene_text":"..."}]}',
                usage_tag="agents.json_repair",
            )
            rows = (parsed or {}).get("scenes") if isinstance(parsed, dict) else []
            rows = rows if isinstance(rows, list) else []
            row = rows[0] if rows and isinstance(rows[0], dict) else None
            logger.info(
                "scene_rewrite_single_done: run_id=%s scene_ref=%s rewritten=%s",
                run_id,
                scene_ref,
                bool(row),
            )
            return scene_ref, row
        except Exception as exc:
            logger.warning(
                "scene_rewrite_single_error: run_id=%s scene_ref=%s error=%s",
                run_id,
                scene_ref,
                exc,
            )
            return scene_ref, None

    try:
        rewrite_results = await asyncio.gather(
            *(_rewrite_single(scene) for scene in scene_inputs),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.warning(
            "scene_rewrite_phase_error: run_id=%s error=%s; continuing without rewrite",
            run_id,
            exc,
        )
        return chunk_results, {"applied": False, "rewritten_count": 0, "error": str(exc)}

    rewrite_pairs: list[tuple[str, dict[str, Any] | None]] = []
    for item in rewrite_results:
        if isinstance(item, Exception):
            logger.warning(
                "scene_rewrite_phase_task_exception: run_id=%s error=%s",
                run_id,
                item,
            )
            continue
        rewrite_pairs.append(item)

    rewrite_by_ref: dict[str, dict[str, Any]] = {
        scene_ref: row for scene_ref, row in rewrite_pairs if scene_ref and isinstance(row, dict)
    }

    rewritten = 0
    rewritten_scenes: list[dict[str, Any]] = []
    for idx, scene in enumerate(scene_inputs):
        scene_ref = str(scene.get("scene_ref") or "").strip()
        rewrite = rewrite_by_ref.get(scene_ref) or {}
        next_description = str(
            rewrite.get("scene_description")
            or scene.get("scene_description")
            or ""
        ).strip()
        next_text = str(
            rewrite.get("scene_text")
            or scene.get("scene_text")
            or ""
        ).strip()
        if scene_ref in rewrite_by_ref:
            rewritten += 1
        rewritten_scenes.append(
            {
                "scene_id": scene.get("scene_id", idx),
                "scene_ref": scene_ref,
                "name": scene.get("scene_name") or "",
                "description": next_description,
                "start_paragraph": scene.get("start_paragraph"),
                "end_paragraph": scene.get("end_paragraph"),
                "text": next_text,
            }
        )
    rewritten_chunk_results: list[dict[str, Any]] = [{
        "status": "ok",
        "entity_instance_id": None,
        "entity_alias": None,
        "chunk_index": 0,
        "paragraph_count": sum(1 for s in rewritten_scenes if s.get("start_paragraph") and s.get("end_paragraph")),
        "token_count": 0,
        "paragraph_start": None,
        "paragraph_end": None,
        "marked_paragraphs": "",
        "scenes": rewritten_scenes,
    }]

    logger.info(
        "scene_rewrite_done: run_id=%s input_scenes=%d rewritten=%d",
        run_id,
        len(scene_inputs),
        rewritten,
    )
    return rewritten_chunk_results, {"applied": True, "rewritten_count": rewritten}


async def _extract_scene_entities(
    *,
    run_id: str,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
    existing_nodes: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    instructions: str | None = None,
) -> list[dict[str, Any]]:
    concurrency = _scene_entity_extraction_concurrency()
    semaphore = asyncio.Semaphore(concurrency)
    existing_catalogue = _build_existing_entity_prompt_catalogue(
        existing_nodes,
        allowed_ontology_names=allowed_ontology_names,
    )

    def _normalize_entities_for_scene(
        scene_input: dict[str, Any],
        raw_entities: list[Any],
    ) -> dict[str, Any]:
        scene_ref = str(scene_input.get("scene_ref") or "")
        entities: list[dict[str, Any]] = []
        for entity in raw_entities:
            status = str(entity.status or "new").strip().lower()
            payload = entity.model_dump()
            payload["status"] = "existing" if status == "existing" else "new"
            mapped = _resolve_allowed_ontology_name_fuzzy(entity.ontology, allowed_ontology_names)
            payload["ontology"] = mapped or str(entity.ontology or "").strip()
            # Alias-authoritative match first: if name resolves to an existing node, force update semantics.
            alias_matched = _resolve_existing_node_by_alias(
                name=entity.name,
                matched_alias=entity.matched_alias or entity.name,
                ontology=None,
                existing_nodes=existing_nodes,
            )
            if alias_matched:
                payload["status"] = "existing"
                payload["matched_alias"] = str(
                    alias_matched.get("alias") or entity.matched_alias or entity.name or ""
                ).strip()
                payload["matched_node_id"] = str(alias_matched.get("node_id") or "").strip() or None
                alias_matched_ontology = _resolve_allowed_ontology_name_fuzzy(
                    str(alias_matched.get("ontology") or ""),
                    allowed_ontology_names,
                )
                if alias_matched_ontology:
                    payload["ontology"] = alias_matched_ontology

            if payload["status"] == "existing":
                matched = _resolve_existing_node_by_alias(
                    name=entity.name,
                    matched_alias=entity.matched_alias,
                    ontology=mapped or str(entity.ontology or "").strip(),
                    existing_nodes=existing_nodes,
                )
                if matched:
                    payload["matched_alias"] = str(matched.get("alias") or entity.matched_alias or entity.name)
                    payload["matched_node_id"] = str(matched.get("node_id") or "")
                    # Existing-node match is authoritative for ontology typing.
                    matched_ontology = _resolve_allowed_ontology_name_fuzzy(
                        str(matched.get("ontology") or ""),
                        allowed_ontology_names,
                    )
                    if matched_ontology:
                        payload["ontology"] = matched_ontology
                else:
                    # Alias-authoritative fallback: if alias uniquely resolves, keep as existing
                    # and inherit ontology from the matched node regardless of model ontology text.
                    fallback_matched = _resolve_existing_node_by_alias(
                        name=entity.name,
                        matched_alias=entity.name,
                        ontology=None,
                        existing_nodes=existing_nodes,
                    )
                    if fallback_matched:
                        payload["matched_alias"] = str(
                            fallback_matched.get("alias") or entity.name or ""
                        ).strip()
                        payload["matched_node_id"] = str(
                            fallback_matched.get("node_id") or ""
                        ).strip() or None
                        fallback_ontology = _resolve_allowed_ontology_name_fuzzy(
                            str(fallback_matched.get("ontology") or ""),
                            allowed_ontology_names,
                        )
                        if fallback_ontology:
                            payload["ontology"] = fallback_ontology
                    else:
                        requested_alias = str(entity.matched_alias or entity.name or "").strip()
                        requested_canonical = _canonical_alias_for_lookup(requested_alias)
                        alias_present = any(
                            _canonical_alias_for_lookup(str(node.get("alias") or "")) == requested_canonical
                            for node in existing_nodes
                        )
                        logger.warning(
                            "scene_entity_existing_match_unresolved: run_id=%s scene_ref=%s name=%s matched_alias=%s alias_present=%s existing_nodes_count=%d",
                            run_id,
                            scene_ref,
                            entity.name,
                            entity.matched_alias,
                            alias_present,
                            len(existing_nodes),
                        )
                        payload["status"] = "new"
                        payload["matched_alias"] = None
                        payload["matched_node_id"] = None
            else:
                payload["matched_alias"] = None
                payload["matched_node_id"] = None

            # Final ontology validation after recovery/coercion attempts.
            resolved_ontology = _resolve_allowed_ontology_name_fuzzy(payload.get("ontology"), allowed_ontology_names)
            if not resolved_ontology:
                logger.warning(
                    "scene_entity_invalid_ontology_dropped: run_id=%s scene_ref=%s name=%s ontology=%s",
                    run_id,
                    scene_ref,
                    entity.name,
                    payload.get("ontology"),
                )
                continue
            payload["ontology"] = resolved_ontology
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

    async def _process_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scene_by_ref = {str(scene.get("scene_ref") or ""): scene for scene in batch}
        scenes_payload = [
            {
                "scene_ref": scene.get("scene_ref"),
                "scene_name": scene.get("scene_name"),
                "scene_description": scene.get("scene_description"),
            }
            for scene in batch
        ]
        logger.info(
            "scene_entity_extraction_batch_payload: run_id=%s batch_size=%d scene_refs=%s",
            run_id,
            len(batch),
            [scene.get("scene_ref") for scene in batch],
        )
        try:
            async with semaphore:
                entity_prompt_template = getattr(
                    architect_prompts,
                    "ARCHITECT_ENTITY_PROPOSAL_PROMPT",
                    "",
                )
                prompt = str(entity_prompt_template).format(
                    ontology_definitions=ontology_definitions,
                    existing_entities=json.dumps(existing_catalogue, ensure_ascii=False),
                    scenes_payload=json.dumps(scenes_payload, ensure_ascii=False),
                    # Backward compatibility for old templates during partial deploys.
                    scene_name=batch[0].get("scene_name", "") if batch else "",
                    scene_description=batch[0].get("scene_description", "") if batch else "",
                    scene_text="",
                    chunk_text="",
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
                    usage_tag="architect.entity_extraction",
                )

            response_payload = (
                response_text
                if isinstance(response_text, str)
                else json.dumps(response_text, ensure_ascii=False)
            )
            parsed = await _parse_scene_entity_batch_extraction_with_repair(
                llm_client=llm_client,
                repair_model=repair_model,
                response_text=response_payload,
            )
            rows_by_ref = {
                str(row.scene_ref or "").strip(): row.entities
                for row in parsed.scenes
                if str(row.scene_ref or "").strip()
            }
            if not rows_by_ref and len(batch) == 1 and parsed.scenes:
                rows_by_ref[str(batch[0].get("scene_ref") or "")] = parsed.scenes[0].entities

            return [
                _normalize_entities_for_scene(scene, rows_by_ref.get(str(scene.get("scene_ref") or ""), []))
                for scene in batch
            ]
        except Exception as exc:
            logger.warning(
                "scene_entity_extraction_batch_error: run_id=%s scene_refs=%s error=%s",
                run_id,
                [scene.get("scene_ref") for scene in batch],
                exc,
            )
            return [
                {
                    **scene,
                    "status": "error",
                    "error": str(exc),
                    "entities": [],
                }
                for scene in batch
            ]

    # One scene per LLM call for better per-scene precision and easier retries/debugging.
    batches = [[scene] for scene in scenes]
    batch_results = await asyncio.gather(*(_process_batch(batch) for batch in batches))
    return [row for batch in batch_results for row in batch]


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
    scene_ref_counts: dict[str, int] = {}

    for scene_order, scene in enumerate(scene_inputs, start=1):
        scene_ref = str(scene.get("scene_ref") or f"scene_{scene_order}")
        count = scene_ref_counts.get(scene_ref, 0) + 1
        scene_ref_counts[scene_ref] = count
        if count > 1:
            adjusted_scene_ref = f"{scene_ref}__dup_{count}"
            logger.warning(
                "scene_proposal_duplicate_scene_ref: original=%s adjusted=%s scene_order=%s",
                scene_ref,
                adjusted_scene_ref,
                scene_order,
            )
            scene_ref = adjusted_scene_ref
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
                "start_paragraph": scene.get("start_paragraph"),
                "end_paragraph": scene.get("end_paragraph"),
                "source_paragraphs_absolute": list(scene.get("source_paragraphs_absolute") or []),
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
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
    instructions: str | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    all_chunk_results: list[dict[str, Any]] = []
    total_chunks = 0
    total_paragraphs = 0
    total_scenes = 0
    llm_scene_chunk_debug: list[dict[str, Any]] = []

    for entity in ontology_instance.entities:
        global_paragraphs = extract_paragraphs_from_sources(
            getattr(entity, "text", None),
            getattr(entity, "autogenerated_text", None),
        )
        paragraph_registry = {
            idx: paragraph for idx, paragraph in enumerate(global_paragraphs, start=1)
        }
        chunks = build_scene_chunks_from_sources(
            getattr(entity, "text", None),
            getattr(entity, "autogenerated_text", None),
        )
        if not chunks:
            continue

        chunk_metrics = [
            {
                "chunk_index": chunk.chunk_index,
                "paragraph_count": chunk.paragraph_count,
                "token_count": chunk.token_count,
            }
            for chunk in chunks
        ]
        logger.info(
            "scene_chunking_entity_summary: run_id=%s entity_id=%s chunk_count=%d chunk_metrics=%s",
            run_id,
            getattr(entity, "entity_instance_id", ""),
            len(chunks),
            json.dumps(chunk_metrics, ensure_ascii=False),
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
                    repair_model=repair_model,
                    marked_paragraphs=chunk.marked_paragraphs,
                    paragraph_count=chunk.paragraph_count,
                    paragraphs=chunk.paragraphs,
                    instructions=instructions,
                    debug_rows=llm_scene_chunk_debug,
                )
                enriched_scenes: list[dict[str, Any]] = []
                for scene in scenes:
                    if not isinstance(scene, dict):
                        continue
                    global_start = int(scene.get("start_paragraph") or 1)
                    global_end = int(scene.get("end_paragraph") or global_start)
                    source_paragraphs_absolute = list(scene.get("source_paragraphs_absolute") or [])
                    if not source_paragraphs_absolute:
                        source_paragraphs_absolute = list(range(global_start, global_end + 1))
                    absolute_lines = [
                        f"[P{idx}] {paragraph_registry[idx]}"
                        for idx in source_paragraphs_absolute
                        if idx in paragraph_registry
                    ]
                    enriched_scenes.append(
                        {
                            **scene,
                            "start_paragraph": global_start,
                            "end_paragraph": global_end,
                            "text": "\n".join(absolute_lines),
                            "source_paragraphs_absolute": source_paragraphs_absolute,
                        }
                    )
                scenes = enriched_scenes
                total_scenes += len(scenes)
                discovered_scene_titles = [
                    _safe_scene_title(scene, idx)
                    for idx, scene in enumerate(scenes)
                    if isinstance(scene, dict)
                ]
                logger.info(
                    "scene_chunking_chunk_done: run_id=%s entity_id=%s chunk_index=%d scenes_found=%d scene_titles=%s",
                    run_id,
                    getattr(entity, "entity_instance_id", ""),
                    chunk.chunk_index,
                    len(scenes),
                    json.dumps(discovered_scene_titles, ensure_ascii=False),
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
                formatted_error = _format_exception_message(exc)
                logger.warning(
                    "scene_chunking_chunk_error: run_id=%s entity_id=%s chunk_index=%d error=%s",
                    run_id,
                    getattr(entity, "entity_instance_id", ""),
                    chunk.chunk_index,
                    formatted_error,
                )
                all_chunk_results.append(
                    {
                        "status": "error",
                        "error": formatted_error,
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

    total_scenes = sum(
        len(item.get("scenes") or []) for item in all_chunk_results if item.get("status") == "ok"
    )
    logger.info(
        "scene_merge_removed_pipeline_mode: run_id=%s scene_count=%d",
        run_id,
        total_scenes,
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
        "scene_dedup_applied": False,
        "llm_scene_chunk_debug": llm_scene_chunk_debug,
    }


async def _run_entity_proposal_phase(
    *,
    run_id: str,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
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
        _scene_entity_extraction_concurrency(),
    )

    started = perf_counter()
    scene_entity_results = await _extract_scene_entities(
        run_id=run_id,
        llm_client=llm_client,
        model=model,
        repair_model=repair_model,
        ontology_definitions=ontology_definitions,
        allowed_ontology_names=allowed_ontology_names,
        existing_nodes=existing_nodes,
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
        "scene_entity_results": classified["scene_results"],
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
    title = str(raw_title or "")
    if title and not re.fullmatch(r"milestone\s*\d+", title, flags=re.IGNORECASE):
        return title

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
    display_alias_by_key: dict[str, str] = {}
    for item in scene_entities:
        canonical = _canonical_alias_for_lookup(item.get("canonical"))
        alias = _canonical_alias_for_lookup(item.get("alias"))
        display_alias = str(item.get("alias") or item.get("canonical") or "").strip()
        if canonical:
            scene_keys.add(canonical)
            if display_alias and canonical not in display_alias_by_key:
                display_alias_by_key[canonical] = display_alias
        if alias:
            scene_keys.add(alias)
            if display_alias and alias not in display_alias_by_key:
                display_alias_by_key[alias] = display_alias

    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entity_value = _safe_json_text(raw.get("entity") or raw.get("alias") or raw.get("canonical"))
        entity_key = _canonical_alias_for_lookup(entity_value)
        if not entity_key or entity_key not in scene_keys:
            continue

        relationship_label = _safe_json_text(raw.get("relationship_label"), "related")
        relationship_label = relationship_label.split()[0].lower() if relationship_label else "related"
        relationship_description = _safe_json_text(raw.get("relationship_description"))

        normalized.append(
            {
                "entity": display_alias_by_key.get(entity_key) or entity_value,
                "relationship_label": relationship_label,
                "relationship_description": relationship_description,
            }
        )
    return normalized


def _milestone_signature(item: dict[str, Any]) -> str:
    title_key = _canonical_alias(item.get("title") or item.get("label"))
    description_key = _canonical_alias(item.get("description"))
    if not title_key and not description_key:
        return ""
    return f"{title_key}|{description_key}"


def _dedupe_adjacent_boundary_milestones(by_scene: list[dict[str, Any]]) -> int:
    """Drop duplicated end->begin boundary milestones across adjacent scenes."""
    removed_count = 0
    for idx in range(1, len(by_scene)):
        previous = by_scene[idx - 1].get("milestones") or []
        current = by_scene[idx].get("milestones") or []
        if not previous or not current:
            continue

        prev_last = previous[-1]
        curr_first = current[0]
        if _normalize_boundary_type(prev_last.get("boundary_type")) != "end":
            continue
        if _normalize_boundary_type(curr_first.get("boundary_type")) != "begin":
            continue

        prev_sig = _milestone_signature(prev_last)
        curr_sig = _milestone_signature(curr_first)
        if not prev_sig or prev_sig != curr_sig:
            continue

        dropped = current.pop(0)
        removed_count += 1
        logger.info(
            "milestone_boundary_dedup_applied: scene_ref=%s dropped_title=%s",
            by_scene[idx].get("scene_ref"),
            dropped.get("title") or dropped.get("label"),
        )

        if current:
            has_begin = any(
                _normalize_boundary_type(item.get("boundary_type")) == "begin"
                for item in current
            )
            if not has_begin:
                current[0]["boundary_type"] = "begin"
            for order, item in enumerate(current, start=1):
                item["milestone_order"] = order

    return removed_count


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


def _limit_to_two_sentences(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(sentence for sentence in sentences[:2] if sentence).strip()


def _parse_batched_milestone_extraction(response_text: str) -> dict[str, list[dict[str, Any]]]:
    try:
        payload = json.loads(_extract_json_block(response_text))
        scenes = payload.get("scenes")
        if isinstance(scenes, list):
            by_ref: dict[str, list[dict[str, Any]]] = {}
            for row in scenes:
                if not isinstance(row, dict):
                    continue
                scene_ref = str(row.get("scene_ref") or "").strip()
                milestones = row.get("milestones")
                if scene_ref and isinstance(milestones, list):
                    by_ref[scene_ref] = [item for item in milestones if isinstance(item, dict)]
            return by_ref

        # Legacy single-scene shape retained for old tests and partial model drift.
        milestones = payload.get("milestones")
        scene_ref = str(payload.get("scene_ref") or "").strip()
        if scene_ref and isinstance(milestones, list):
            return {scene_ref: [item for item in milestones if isinstance(item, dict)]}
    except Exception as exc:
        logger.warning("milestone_batch_parse_error: error=%s", exc)
    return {}


async def _parse_batched_milestone_extraction_with_repair(
    *,
    llm_client: ShreckLLMClient,
    repair_model: str | LLMModelTarget,
    response_text: str,
) -> dict[str, list[dict[str, Any]]]:
    parsed = _parse_batched_milestone_extraction(response_text)
    if parsed:
        return parsed
    try:
        repaired_payload = await validate_or_repair_json(
            llm_client=llm_client,
            model=repair_model,
            raw_text=response_text,
            schema_hint='{"scenes":[{"scene_ref":"...","milestones":[{"title":"...","description":"...","boundary_type":"begin|middle|end","adjacent_to":[],"related_to":[]}]}]}',
            usage_tag="agents.json_repair",
        )
    except Exception as exc:
        logger.warning("milestone_batch_repair_error: error=%s", exc)
        return {}
    return _parse_batched_milestone_extraction(json.dumps(repaired_payload))


async def _run_milestone_proposal_phase(
    *,
    run_id: str,
    llm_client: ShreckLLMClient,
    model: str | LLMModelTarget,
    repair_model: str | LLMModelTarget,
    proposed_scenes: list[dict[str, Any]],
    author_id: str,
    instructions: str | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    concurrency = _milestone_extraction_concurrency()
    semaphore = asyncio.Semaphore(concurrency)

    logger.info(
        "milestone_proposal_start: run_id=%s scene_count=%d batch_size=%d concurrency=%d",
        run_id,
        len(proposed_scenes),
        MILESTONE_BATCH_SIZE,
        concurrency,
    )

    def _scene_entity_aliases(scene: dict[str, Any]) -> list[str]:
        aliases = []
        for item in list(scene.get("related_to") or []):
            if not isinstance(item, dict):
                continue
            alias = str(item.get("alias") or item.get("canonical") or "").strip()
            if alias:
                aliases.append(alias)
        return sorted(set(aliases))

    def _normalize_raw_milestones(scene: dict[str, Any], raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        scene_ref = str(scene.get("scene_ref") or "")
        scene_id = str(scene.get("scene_id") or "")
        scene_entities = list(scene.get("related_to") or [])
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

            raw_adjacent_to = item.get("adjacent_to")
            adjacent_to = raw_adjacent_to if isinstance(raw_adjacent_to, list) else []
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

        return milestones

    async def _process_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with semaphore:
            scenes_payload = [
                {
                    "scene_ref": scene.get("scene_ref"),
                    "scene_name": scene.get("scene_name"),
                    "scene_description": scene.get("scene_description"),
                    "entities": _scene_entity_aliases(scene),
                }
                for scene in batch
            ]
            milestone_prompt_template = getattr(
                architect_prompts,
                "ARCHITECT_MILESTONE_BATCH_PROMPT",
                "",
            )
            prompt = str(milestone_prompt_template).format(
                scenes_payload=json.dumps(scenes_payload, ensure_ascii=False)
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
                    usage_tag="architect.milestone_proposal",
                )
                response_payload = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
                raw_by_ref = await _parse_batched_milestone_extraction_with_repair(
                    llm_client=llm_client,
                    repair_model=repair_model,
                    response_text=response_payload,
                )
            except Exception as exc:
                logger.warning(
                    "milestone_proposal_batch_error: run_id=%s scene_refs=%s error=%s",
                    run_id,
                    [scene.get("scene_ref") for scene in batch],
                    exc,
                )
                raw_by_ref = {}

            rows: list[dict[str, Any]] = []
            for scene in batch:
                scene_ref = str(scene.get("scene_ref") or "")
                milestones = _normalize_raw_milestones(scene, raw_by_ref.get(scene_ref, []))
                logger.info(
                    "milestone_proposal_scene_total: run_id=%s scene_ref=%s milestone_count=%d",
                    run_id,
                    scene_ref,
                    len(milestones),
                )
                rows.append(
                    {
                        "scene_ref": scene_ref,
                        "scene_id": str(scene.get("scene_id") or ""),
                        "milestones": milestones,
                    }
                )
            return rows

    # One scene per milestone extraction call for stronger scene-local fidelity.
    batches = [[scene] for scene in proposed_scenes]
    batch_results = await asyncio.gather(*(_process_batch(batch) for batch in batches))
    by_scene = [row for batch in batch_results for row in batch]
    deduped_boundary_count = _dedupe_adjacent_boundary_milestones(by_scene)

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
        "deduped_boundary_count": deduped_boundary_count,
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
                "analysis_metadata": {
                    "request_payload": request_payload,
                },
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
    if not is_shreckllm_configured(settings):
        raise RuntimeError("shreckLLM is not configured")
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

            llm_client = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.shreckllm_request_timeout_s, max_retries=settings.shreckllm_max_retries)
            runtime_config = await fetch_shreckllm_runtime(settings)
            scene_chunking_model = settings.model_architect_scene_chunking
            entity_proposal_model = settings.model_architect_entity_proposal
            milestone_proposal_model = settings.model_architect_milestone_proposal
            repair_json_model = getattr(settings, "model_agents_repair_json", scene_chunking_model) or scene_chunking_model
            global _ARCHITECT_CONCURRENCY
            _ARCHITECT_CONCURRENCY = resolve_effective_architect_concurrency(
                runtime_config,
                provider_id=entity_proposal_model.provider,
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

            logger.info(
                "architect_analysis_llm_client_config: run_id=%s timeout_s=%.3f max_retries=%d base_url=%s",
                run_id,
                float(settings.shreckllm_request_timeout_s),
                int(settings.shreckllm_max_retries),
                settings.shreckllm_base_url,
            )
            try:
                result = await _run_scene_centric_chunking_test(
                    run_id=run_id,
                    agent_id=agent_id,
                    job_id=job_id,
                    ontology_instance_id=ontology_instance_id,
                    ontology_instance=ontology_instance,
                    llm_client=llm_client,
                    scene_chunking_model=scene_chunking_model,
                    entity_proposal_model=entity_proposal_model,
                    milestone_proposal_model=milestone_proposal_model,
                    repair_json_model=repair_json_model,
                    ontology_definitions=ontology_definitions,
                    allowed_ontology_names=allowed_ontology_names,
                    existing_nodes=existing_nodes,
                    celery_task_id=analyze_instance.request.id,
                    author_id=agent_id,
                )
                usage_summary = llm_client.get_usage_summary()
                _log_run_llm_usage_summary(run_id=run_id, usage_summary=usage_summary)
                result["llm_usage_by_step"] = _build_frontend_llm_usage_summary(usage_summary)
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
            "scene_dedup_applied": bool(result.get("scene_dedup_applied", True)),
            "llm_usage_by_step": result.get("llm_usage_by_step") or {},
        }
        await session.commit()

        await update_job_progress(
            job_id,
            0.95,
            {
                "status": "Architect analysis completed",
                "chunk_count": result.get("chunk_count", 0),
                "scene_count": result.get("scene_count", 0),
                "llm_usage_by_step": result.get("llm_usage_by_step") or {},
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
    llm_client: ShreckLLMClient,
    scene_chunking_model: str | LLMModelTarget,
    entity_proposal_model: str | LLMModelTarget,
    milestone_proposal_model: str | LLMModelTarget,
    repair_json_model: str | LLMModelTarget,
    ontology_definitions: str,
    allowed_ontology_names: dict[str, str],
    existing_nodes: list[dict[str, Any]],
    celery_task_id: str | None,
    author_id: str,
) -> dict[str, Any]:
    """Temporary test path for scene-centric chunking with single consolidated output."""
    total_started = perf_counter()
    await update_job_progress(job_id, 0.25, {"status": "Scene-centric chunking"})

    step_usage_start = llm_client.get_usage_event_count()
    chunking_phase = await _run_scene_chunking_phase(
        run_id=run_id,
        ontology_instance=ontology_instance,
        llm_client=llm_client,
        model=scene_chunking_model,
        repair_model=repair_json_model,
    )
    _log_step_usage(
        run_id=run_id,
        step="scene_chunking",
        usage=_format_step_usage_delta(llm_client, step_usage_start),
    )
    all_chunk_results = chunking_phase["chunk_results"]
    total_chunks = chunking_phase["chunk_count"]
    total_paragraphs = chunking_phase["paragraph_count"]
    total_scenes = chunking_phase["scene_count"]
    scene_dedup_applied = bool(chunking_phase.get("scene_dedup_applied", True))
    scene_chunking_elapsed_seconds = chunking_phase["elapsed_seconds"]
    llm_scene_chunk_debug = list(chunking_phase.get("llm_scene_chunk_debug") or [])
    step_usage_start = llm_client.get_usage_event_count()
    all_chunk_results, scene_merge_summary = await _run_scene_merge_phase(
        run_id=run_id,
        llm_client=llm_client,
        model=scene_chunking_model,
        repair_model=repair_json_model,
        chunk_results=all_chunk_results,
    )
    _log_step_usage(
        run_id=run_id,
        step="scene_merging",
        usage=_format_step_usage_delta(llm_client, step_usage_start),
    )
    scene_rewrite_summary = {
        "applied": False,
        "reason": "disabled_by_pipeline_configuration",
        "rewritten_count": 0,
    }

    await update_job_progress(job_id, 0.7, {"status": "Scene entity discovery"})
    step_usage_start = llm_client.get_usage_event_count()
    entity_phase = await _run_entity_proposal_phase(
        run_id=run_id,
        llm_client=llm_client,
        model=entity_proposal_model,
        repair_model=repair_json_model,
        ontology_definitions=ontology_definitions,
        allowed_ontology_names=allowed_ontology_names,
        existing_nodes=existing_nodes,
        chunk_results=all_chunk_results,
    )
    _log_step_usage(
        run_id=run_id,
        step="entity_discovery",
        usage=_format_step_usage_delta(llm_client, step_usage_start),
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
    step_usage_start = llm_client.get_usage_event_count()
    milestone_phase = await _run_milestone_proposal_phase(
        run_id=run_id,
        llm_client=llm_client,
        model=milestone_proposal_model,
        repair_model=repair_json_model,
        proposed_scenes=proposed_scenes,
        author_id=author_id,
    )
    _log_step_usage(
        run_id=run_id,
        step="milestone_proposal",
        usage=_format_step_usage_delta(llm_client, step_usage_start),
    )
    proposed_milestones = milestone_phase["proposed_milestones"]
    milestones_per_scene = milestone_phase["per_scene"]
    deduped_boundary_count = int(milestone_phase.get("deduped_boundary_count") or 0)
    removed_scene_refs = list(milestone_phase.get("removed_scene_refs") or [])
    removed_scene_count = int(milestone_phase.get("removed_scene_count") or 0)
    milestone_proposal_elapsed_seconds = float(milestone_phase.get("elapsed_seconds") or 0)

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
            "scene_dedup_applied": scene_dedup_applied,
            "deduped_boundary_count": deduped_boundary_count,
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
            "scene_merge": scene_merge_summary,
            "scene_rewrite": scene_rewrite_summary,
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
        "scene_dedup_applied": scene_dedup_applied,
        "elapsed_seconds": scene_chunking_elapsed_seconds,
        "chunks": all_chunk_results,
        "llm_scene_chunk_debug": llm_scene_chunk_debug,
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
        "deduped_boundary_count": deduped_boundary_count,
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
        "scene_chunk_llm_debug": _write_local_json_artifact(
            output_dir=output_dir,
            filename="scene_chunk_llm_debug.json",
            payload={
                "run_id": run_id,
                "chunk_count": total_chunks,
                "records": llm_scene_chunk_debug,
            },
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
        "scene_dedup_applied": scene_dedup_applied,
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
