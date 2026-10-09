"""Celery entry point for atomic EmbodyAgent embodiment drafts."""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent.embody_agent import (
    EmbodyAgent,
    EmbodimentGenerationError,
    _PerspectivesContainer,
)
from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts
from app.jobs.character_agent.embody_agent_prompts import PROMPT_VERSION
from app.models.character_embodiment import (
    CharacterEmbodimentDraft,
    CharacterEmbodimentDraftStatus,
)
from app.services.character_embodiment_service import CharacterEmbodimentService
from app.schemas.character_agent import EmbodyAgentAnalysis, LLMCallRecord
from app.schemas.character_traits import TraitProfile, TraitEvidence
from app.services.character_trait_service import chunk_source_scenes, merge_evidence
from app.jobs.character_agent.profile import _build_timeline, _apply_aspect_ops, _apply_goal_ops, _stable_profile_id
from app.schemas.character_agent import (
    CharacterIdentityRevisionProjection,
    CharacterSourceProjection,
    CharacterTimelineProjection,
    EmbodimentAspectProposal,
    EmbodimentGoalProposal,
    ProjectedScenePerspective,
    SceneInput,
    SubtitleChangeProposal,
    IdentityDescription,
)
from app.utils.async_helpers import run_async
from app.utils.job_tracking import mark_job_done, mark_job_failed, mark_job_running, update_job_progress

STEP_NAME: dict[int, str] = {
    1: "Perspective",
    2: "Psychological enrichment",
    3: "Trait interpretation and aggregation",
    4: "Psychological consolidation",
}

SCENE_ANALYSIS_CHUNK_SIZE = 5
CHECKPOINT_VERSION = 2


def _checkpoint_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _chunk_checkpoint_key(common_material: dict[str, Any], chunk_index: int, scenes: list[dict]) -> str:
    """Fingerprint shared upstream state plus only this chunk's ordered inputs."""
    return _checkpoint_digest({
        "common": common_material,
        "chunk_index": chunk_index,
        "scenes": scenes,
    })


def _checkpoint_analysis(raw: Any, *, checkpoint_key: str) -> EmbodyAgentAnalysis | None:
    """Load a validated cached source analysis, ignoring corrupt or incompatible data."""
    if not isinstance(raw, dict) or raw.get("version") != CHECKPOINT_VERSION:
        return None
    if raw.get("key") != checkpoint_key or not isinstance(raw.get("analysis"), dict):
        return None
    try:
        return EmbodyAgentAnalysis.model_validate(raw["analysis"])
    except Exception:
        return None


def _checkpoint_chunk(raw: Any, *, chunk_key: str, scene_ids: list[str]):
    """Load independently validated scene-stage outputs for one exact chunk."""
    if not isinstance(raw, dict) or raw.get("version") != CHECKPOINT_VERSION or raw.get("key") != chunk_key:
        return None, None
    perspective_value = None
    analysis_value = None
    try:
        perspective_value = _PerspectivesContainer.model_validate(raw["perspectives"])
        if [item.scene_id for item in perspective_value.perspectives] != scene_ids:
            perspective_value = None
    except Exception:
        perspective_value = None
    try:
        analysis_value = EmbodyAgentAnalysis.model_validate(raw["analysis"])
        if [item.scene_id for item in analysis_value.perspectives] != scene_ids:
            analysis_value = None
    except Exception:
        analysis_value = None
    if perspective_value is None and analysis_value is not None:
        # A complete analysis contains the same bound perspectives and can
        # safely serve as Stage 1 input if only the separate record was corrupt.
        perspective_value = _PerspectivesContainer(perspectives=[
            item.model_copy(deep=True) for item in analysis_value.perspectives
        ])
    return perspective_value, analysis_value


def _step_label(steps: list[int]) -> str:
    if not steps:
        return ""
    if len(steps) == 1:
        return "Step {0}: {1}".format(steps[0], STEP_NAME[steps[0]])
    first, last = steps[0], steps[-1]
    return "Steps {0}-{1}: {2}".format(first, last, STEP_NAME[first].split(" ")[0].rstrip(","))


def _stage_label(source_alias: str, active_stages: list[int]) -> str:
    return "Bundle — {0} — {1}".format(
        source_alias,
        _step_label(active_stages) if active_stages else "finalizing",
    )


@celery_app.task(name="character_agent.generate_embodiment")
def generate_character_embodiment(*, draft_id: str, revision: int, job_id: int) -> dict:
    try:
        run_async(mark_job_running(job_id))
        result = run_async(_generate(draft_id=draft_id, revision=revision, job_id=job_id))
        run_async(mark_job_done(job_id, result))
        return result
    except Exception as exc:
        details = (
            exc.details()
            if isinstance(exc, EmbodimentGenerationError)
            else {
                "failure_category": "unexpected",
                "retryable": False,
            }
        )
        error_message = _public_error_message(exc)
        run_async(_fail(draft_id, revision, error_message))
        run_async(mark_job_failed(job_id, error_message, details))
        raise


def _public_error_message(exc: Exception) -> str:
    if not isinstance(exc, EmbodimentGenerationError):
        return str(exc)
    if exc.category == "provider_unavailable":
        provider = exc.provider_id or "configured LLM provider"
        model = f" model '{exc.model_name}'" if exc.model_name else ""
        reason = f" ({exc.provider_reason})" if exc.provider_reason else ""
        return (
            f"Character embodiment cannot start because provider '{provider}'{model} "
            f"is unavailable{reason}. Configure an available model and retry."
        )
    stage_key = (exc.stage or "generation").replace("-", " ").replace(" ", "_")
    detail = {
        "code": f"{stage_key}_validation_failed",
        **exc.details(),
    }
    return json.dumps(detail, sort_keys=True)


class _EmbodimentProgress:
    """Serialize progress writes while exposing independent chunk state."""

    def __init__(
        self, *, job_id: int, draft_id: str, bundles: list[dict],
        source_groups: list[dict],
    ) -> None:
        self.job_id = job_id
        self.draft_id = draft_id
        self.bundles = bundles
        self.source_groups = source_groups
        self.total = len(source_groups)
        self.starts: dict[int, float] = {}
        self.active: dict[int, list[int]] = {}
        self.done: dict[int, set[int]] = {i: set() for i in range(self.total)}
        self.chunks: dict[int, list[dict[str, Any]]] = {}
        self.lock = asyncio.Lock()
        self.last_progress = 0.10

    def configure_chunks(self, index: int, scene_chunks: list[list[dict]]) -> None:
        """Register a bundle's bounded chunks before their concurrent work starts."""
        self.chunks[index] = [
            {"index": chunk_index + 1, "scene_count": len(scenes),
             "active_steps": [], "done_steps": []}
            for chunk_index, scenes in enumerate(scene_chunks)
        ]

    def callback(
        self, index: int, *, chunk_index: int | None = None,
    ):
        async def on_stage(_stage_label_value: str, active_stages: list[int]) -> None:
            await self.stage(index, active_stages, chunk_index=chunk_index)
        return on_stage

    async def stage(
        self, index: int, active_stages: list[int], *, chunk_index: int | None = None,
    ) -> None:
        async with self.lock:
            self.starts.setdefault(index, time.monotonic())
            if chunk_index is None:
                previous = self.active.get(index, [])
                self.done[index].update(previous)
                self.active[index] = list(active_stages)
            else:
                chunk = self.chunks[index][chunk_index]
                previous = chunk["active_steps"]
                chunk["done_steps"] = sorted(set(chunk["done_steps"]) | set(previous))
                chunk["active_steps"] = list(active_stages)
                self.active[index] = sorted({
                    step
                    for item in self.chunks[index]
                    for step in item["active_steps"]
                })
            await self._publish(index, "processing")

    async def chunk_complete(self, index: int, chunk_index: int) -> None:
        async with self.lock:
            chunk = self.chunks[index][chunk_index]
            chunk["done_steps"] = [1, 2, 3, 4]
            chunk["active_steps"] = []
            self.active[index] = sorted({
                step
                for item in self.chunks[index]
                for step in item["active_steps"]
            })
            if all(item["done_steps"] == [1, 2, 3, 4] for item in self.chunks[index]):
                self.done[index].update({1, 2})
            await self._publish(index, "processing")

    async def chunk_failed(
        self, index: int, chunk_index: int, *, stage: str, error: Exception,
    ) -> None:
        """Expose a failed work unit instead of hiding it behind gather()."""
        async with self.lock:
            chunk = self.chunks[index][chunk_index]
            chunk["active_steps"] = []
            chunk["failed_stage"] = stage
            chunk["error"] = _exception_details(error)
            await self._publish(index, "processing")

    async def analysis_ready(self, index: int) -> None:
        async with self.lock:
            self.done[index].update(self.active.get(index, []))
            self.active[index] = []
            await self._publish(index, "processing", stage="Waiting for ordered profile update")

    async def complete(self, index: int) -> None:
        async with self.lock:
            self.done[index].update({1, 2, 3})
            self.active[index] = []
            await self._publish(index, "done", stage="Source complete")

    async def failed(self, index: int) -> None:
        async with self.lock:
            self.done[index].update(self.active.get(index, []))
            self.active[index] = []
            await self._publish(index, "failed", stage="Source failed")

    async def _publish(
        self, index: int, status: str, *, stage: str | None = None,
    ) -> None:
        source_alias = self.source_groups[index]["source_alias"]
        started = self.starts.get(index, time.monotonic())
        chunk_states = [
            {
                **item,
                "status": (
                    "failed" if item.get("error") else
                    "processing" if item["active_steps"] else
                    "done" if item["done_steps"] == [1, 2] else "pending"
                ),
            }
            for item in self.chunks.get(index, [])
        ]
        self.bundles[index] = {
            "index": index + 1,
            "source_name": source_alias,
            "status": status,
            "active_steps": list(self.active.get(index, [])),
            "done_steps": sorted(self.done[index]),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "chunks": chunk_states,
            "parallel": {
                "active": len(self.active.get(index, [])) > 1 or sum(
                    item["status"] == "processing" for item in chunk_states
                ) > 1,
                "active_branches": [STEP_NAME[step] for step in self.active.get(index, [])],
                "active_chunk_count": sum(
                    item["status"] == "processing" for item in chunk_states
                ),
                "execution": "shreckllm_managed",
            },
        }
        completed = sum(len(steps) for steps in self.done.values())
        active_credit = sum(0.5 for steps in self.active.values() if steps)
        calculated = 0.10 + 0.80 * (
            (completed + active_credit) / max(self.total * 3, 1)
        )
        self.last_progress = min(0.90, max(self.last_progress, calculated))
        await update_job_progress(self.job_id, self.last_progress, {
            "stage": stage or _stage_label(
                source_alias, self.active.get(index, [])
            ),
            "draft_id": self.draft_id,
            "bundles": list(self.bundles),
        })


def _merge_observations(target: Any, source: Any) -> Any:
    """Merge source observation lists into target in-place."""
    list_fields = [
        "recurring_behaviours", "motivations", "values", "fears",
        "conflicts", "relationships", "contradictions", "evidence_gaps", "trait_evidence",
    ]
    for field in list_fields:
        existing = list(getattr(target, field, None) or [])
        new_vals = list(getattr(source, field, None) or [])
        setattr(target, field, existing + new_vals)
    return target


def _exception_details(error: Exception) -> dict[str, Any]:
    """Keep polling telemetry safe for job-progress consumers."""
    candidate: Any = error
    while candidate is not None:
        details = getattr(candidate, "details", None)
        if callable(details):
            value = details()
            if isinstance(value, dict):
                return value
        candidate = getattr(candidate, "__cause__", None)
    return {"message": str(error), "error_type": type(error).__name__}


def _scene_analysis_chunks(group: dict) -> list[list[dict]]:
    """Partition one source's ordered raw scenes for scene-local LLM work."""
    scenes = list(group["scenes"])
    return [
        scenes[index:index + SCENE_ANALYSIS_CHUNK_SIZE]
        for index in range(0, len(scenes), SCENE_ANALYSIS_CHUNK_SIZE)
    ]


def _merge_chunk_analyses(analyses: list[EmbodyAgentAnalysis]) -> EmbodyAgentAnalysis:
    """Merge validated scene-local chunk analyses before one profile update."""
    if not analyses:
        raise EmbodimentGenerationError("source has no scene analyses")
    first = analyses[0]
    observations = first.observations.model_copy(deep=True)
    for analysis in analyses[1:]:
        _merge_observations(observations, analysis.observations)
    subtitle = next(
        (analysis.subtitle_change for analysis in reversed(analyses)
         if analysis.subtitle_change.operation != "retain"),
        SubtitleChangeProposal(),
    )
    return first.model_copy(update={
        "scene_input_digests": {
            scene_id: digest
            for analysis in analyses for scene_id, digest in analysis.scene_input_digests.items()
        },
        "perspectives": [item for analysis in analyses for item in analysis.perspectives],
        "observations": observations,
        "subtitle_change": subtitle,
        "evidence_ids": set().union(*(analysis.evidence_ids for analysis in analyses)),
        "profile_events": [item for analysis in analyses for item in analysis.profile_events],
        "llm_calls": [item for analysis in analyses for item in analysis.llm_calls],
        "observations_unavailable": any(analysis.observations_unavailable for analysis in analyses),
    })


async def _generate(*, draft_id: str, revision: int, job_id: int) -> dict:
    generation_started = time.monotonic()
    settings = get_settings()
    async with AsyncSessionMaker() as sql:
        draft = await sql.get(CharacterEmbodimentDraft, draft_id)
        if not draft or draft.generation_revision != revision:
            return {"draft_id": draft_id, "status": "superseded"}
        draft.status = CharacterEmbodimentDraftStatus.GENERATING
        draft.error_message = None
        await sql.commit()
        await update_job_progress(job_id, 0.05, {
            "stage": "Loading embodiment input",
            "draft_id": draft_id,
            "bundles": [],
        })
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as graph:
            svc = CharacterEmbodimentService(sql, graph)
            inputs = await svc.load_embodiment_input(
                source_entity_id=draft.source_entity_id,
                ontology_id=draft.ontology_id,
            )
        source_groups = chunk_source_scenes(inputs.get("source_groups", []))
        debug_artifacts = EmbodimentDebugArtifacts.create(
            enabled=settings.character_agent_embodiment_debug_artifacts_enabled,
            draft_id=draft_id,
            revision=revision,
        )

        total = len(source_groups)
        bundles: list[dict] = [
            {
                "index": i + 1,
                "source_name": g["source_alias"],
                "status": "pending",
                "active_steps": [],
                "done_steps": [],
                "elapsed_seconds": None,
            }
            for i, g in enumerate(source_groups)
        ]

        await update_job_progress(job_id, 0.10, {
            "stage": f"Preparing {total} source bundle(s)",
            "draft_id": draft_id,
            "bundles": bundles,
        })

        all_scene_inputs: list[SceneInput] = []
        for g in source_groups:
            for s in g["scenes"]:
                all_scene_inputs.append(SceneInput(
                    scene_id=s["scene_id"], name=s["name"],
                    description=s["description"], created_at=s["created_at"],
                ))

        # Checkpoints are draft-local and only reused when all scene inputs,
        # upstream working state, and generation contracts still match.
        try:
            checkpoint_store = json.loads(draft.generation_checkpoints or "{}")
            if not isinstance(checkpoint_store, dict):
                checkpoint_store = {}
        except (TypeError, ValueError):
            checkpoint_store = {}
        checkpoint_store = {
            key: value for key, value in checkpoint_store.items()
            if key.startswith("source:")
        }

        # Cumulative state that carries across bundles
        current_profile = inputs["trait_profile"]
        current_evidence = list(inputs["trait_evidence"])
        current_aspects = [dict(a) for a in inputs["current_aspects"]]
        current_goals = [dict(g) for g in inputs["current_goals"]]
        initial_subtitle = inputs["canonical_identity"].get("subtitle") or None
        current_subtitle = initial_subtitle

        all_perspectives: list = []
        merged_obs: Any = None
        all_aspect_updates: list = []
        all_goal_updates: list = []
        total_llm_calls = 0
        total_tokens_est = 0
        total_semantic_corrections = 0
        per_bundle_results: list = []

        client = ShreckLLMClient(
            base_url=settings.shreckllm_base_url,
            timeout=settings.shreckllm_request_timeout_s,
            max_retries=settings.shreckllm_max_retries,
            chat_job_timeout_s=settings.shreckllm_chat_job_timeout_s,
        )
        progress = _EmbodimentProgress(
            job_id=job_id,
            draft_id=draft_id,
            bundles=bundles,
            source_groups=source_groups,
        )
        agents: list[EmbodyAgent] = []

        try:
            def make_agent(*, source_index: int | None = None, source_alias: str | None = None):
                return EmbodyAgent(
                    llm_client=client,
                    character_incorporation_model=settings.model_character_agent_character_incorporation,
                    scene_interpretation_model=settings.model_character_agent_scene_interpretation,
                    validation_retries=(
                        settings.character_agent_embodiment_validation_retries
                    ),
                    debug_artifacts=debug_artifacts,
                    debug_source_index=source_index,
                    debug_source_alias=source_alias,
                )
            initializer = make_agent()
            agents.append(initializer)
            current_profile = inputs["trait_profile"]
            current_evidence = list(inputs["trait_evidence"])
            existing_identity = inputs.get("identity_description")
            if isinstance(existing_identity, str):
                try:
                    existing_identity = json.loads(existing_identity)
                except ValueError:
                    existing_identity = None
            existing_identity = IdentityDescription.model_validate(existing_identity) if existing_identity else None
            identity_description = existing_identity or await initializer.generate_identity_description(
                canonical_identity=inputs["canonical_identity"], current_profile=current_profile,
                current_aspects=inputs["current_aspects"], current_goals=inputs["current_goals"],
            )
            total_llm_calls += len(initializer.llm_calls)
            total_tokens_est += sum(item.total_tokens_est for item in initializer.llm_calls)
            inputs["canonical_identity"]["identity_description"] = identity_description.model_dump(mode="json")
            initial_profile = current_profile.model_copy(deep=True)
            initial_evidence = list(current_evidence)
            for bi, group in enumerate(source_groups):
                agent = make_agent(source_index=bi, source_alias=group["source_alias"])
                agents.append(agent)
                scene_chunks = _scene_analysis_chunks(group)
                progress.configure_chunks(bi, scene_chunks)

                checkpoint_material = {
                    "version": CHECKPOINT_VERSION,
                    "source_id": group["source_id"],
                    "batch_id": group["batch_id"],
                    "scenes": [
                        {"scene_id": scene["scene_id"], "name": scene["name"],
                         "description": scene["description"], "created_at": scene["created_at"]}
                        for scene in group["scenes"]
                    ],
                    "profile": current_profile.model_dump(mode="json"),
                    "trait_evidence": [item.model_dump(mode="json") for item in current_evidence],
                    "aspects": current_aspects,
                    "goals": current_goals,
                    "identity_description": identity_description.model_dump(mode="json"),
                    "prompt_version": PROMPT_VERSION,
                    "chunk_size": SCENE_ANALYSIS_CHUNK_SIZE,
                    "models": {
                        "identity": settings.model_character_agent_character_incorporation.model_dump(mode="json"),
                        "scene": settings.model_character_agent_scene_interpretation.model_dump(mode="json"),
                    },
                }
                checkpoint_key = _checkpoint_digest(checkpoint_material)
                source_checkpoint = checkpoint_store.get(f"source:{bi}")
                if (not isinstance(source_checkpoint, dict)
                        or source_checkpoint.get("version") != CHECKPOINT_VERSION):
                    source_checkpoint = {"version": CHECKPOINT_VERSION, "key": checkpoint_key, "chunks": {}}
                elif source_checkpoint.get("key") != checkpoint_key:
                    # The full-source aggregate is stale, but independently
                    # fingerprinted chunks may still match their exact inputs.
                    source_checkpoint.pop("analysis", None)
                    source_checkpoint["key"] = checkpoint_key
                chunks_store = source_checkpoint.get("chunks")
                if not isinstance(chunks_store, dict):
                    chunks_store = {}
                source_checkpoint["chunks"] = chunks_store
                checkpoint_store[f"source:{bi}"] = source_checkpoint

                async def persist_chunk_checkpoints() -> bool:
                    await sql.refresh(draft)
                    if draft.generation_revision != revision:
                        return False
                    draft.generation_checkpoints = json.dumps(checkpoint_store, ensure_ascii=False)
                    await sql.commit()
                    return True

                cached_analysis = _checkpoint_analysis(source_checkpoint, checkpoint_key=checkpoint_key)
                if cached_analysis is not None:
                    analysis = cached_analysis
                    await progress.analysis_ready(bi)
                else:
                    common_chunk_material = {key: value for key, value in checkpoint_material.items() if key != "scenes"}
                    chunk_keys = [
                        _chunk_checkpoint_key(common_chunk_material, index, scenes)
                        for index, scenes in enumerate(scene_chunks)
                    ]
                    chunks_store = {
                        str(index): record
                        for index, key in enumerate(chunk_keys)
                        if isinstance((record := chunks_store.get(str(index))), dict)
                        and record.get("key") == key
                    }
                    source_checkpoint["chunks"] = chunks_store
                    chunk_agents = [make_agent(
                        source_index=bi,
                        source_alias=f"{group['source_alias']} (chunk {index + 1})",
                    ) for index in range(len(scene_chunks))]
                    agents.extend(chunk_agents)
                    perspective_results: list[Any] = [None] * len(scene_chunks)
                    analysis_results: list[Any] = [None] * len(scene_chunks)
                    perspective_call_records: list[list[dict[str, Any]]] = [[] for _ in scene_chunks]

                    for index, scenes in enumerate(scene_chunks):
                        scene_ids = [str(scene["scene_id"]) for scene in scenes]
                        cached_perspectives, cached_chunk_analysis = _checkpoint_chunk(
                            chunks_store.get(str(index)), chunk_key=chunk_keys[index], scene_ids=scene_ids,
                        )
                        if cached_perspectives is None and cached_chunk_analysis is None:
                            chunks_store.pop(str(index), None)
                        elif cached_perspectives is not None:
                            normalized = chunks_store.get(str(index), {})
                            if not isinstance(normalized, dict):
                                normalized = {}
                            normalized.pop("analysis", None)
                            normalized.update({
                                "version": CHECKPOINT_VERSION, "key": chunk_keys[index],
                                "perspectives": cached_perspectives.model_dump(mode="json"),
                            })
                            if cached_chunk_analysis is not None:
                                normalized["analysis"] = cached_chunk_analysis.model_dump(mode="json")
                            chunks_store[str(index)] = normalized
                        perspective_results[index] = cached_perspectives
                        analysis_results[index] = cached_chunk_analysis
                        chunk_record = chunks_store.get(str(index), {})
                        if isinstance(chunk_record, dict) and chunk_record.get("key") == chunk_keys[index]:
                            records = chunk_record.get("perspective_llm_calls", [])
                            if isinstance(records, list):
                                perspective_call_records[index] = records
                        if cached_chunk_analysis is not None:
                            await progress.chunk_complete(bi, index)

                    async def perspective_chunk(chunk_index: int, chunk_scenes: list[dict]):
                        await progress.stage(bi, [1], chunk_index=chunk_index)
                        return await chunk_agents[chunk_index].generate_perspectives(
                            source_entity_id=group["source_id"], source_entity_alias=group["source_alias"],
                            canonical_identity=inputs["canonical_identity"], current_trait_profile=current_profile,
                            current_aspects=current_aspects, current_goals=current_goals,
                            scenes=[SceneInput(**scene) for scene in chunk_scenes],
                        )

                    missing_perspectives = [index for index, result in enumerate(perspective_results) if result is None]
                    generated_perspectives = await asyncio.gather(*[
                        perspective_chunk(index, scene_chunks[index]) for index in missing_perspectives
                    ], return_exceptions=True)
                    perspective_errors = []
                    for index, output in zip(missing_perspectives, generated_perspectives, strict=True):
                        if isinstance(output, Exception):
                            perspective_errors.append(output)
                        else:
                            perspective_results[index] = output
                            perspective_call_records[index] = [call.model_dump(mode="json") for call in chunk_agents[index].llm_calls]
                            chunks_store[str(index)] = {
                                "version": CHECKPOINT_VERSION, "key": chunk_keys[index],
                                "perspectives": output.model_dump(mode="json"),
                                "perspective_llm_calls": perspective_call_records[index],
                            }
                    if missing_perspectives and not await persist_chunk_checkpoints():
                        return {"draft_id": draft_id, "status": "superseded"}
                    if perspective_errors:
                        for index, output in zip(missing_perspectives, generated_perspectives, strict=True):
                            if isinstance(output, Exception):
                                await progress.chunk_failed(bi, index, stage="perspective", error=output)
                        await progress.failed(bi)
                        raise perspective_errors[0]

                    async def psychology_chunk(chunk_index: int, chunk_scenes: list[dict], perspectives):
                        await progress.stage(bi, [2, 3], chunk_index=chunk_index)
                        return await chunk_agents[chunk_index].analyze(
                            source_entity_id=group["source_id"], source_entity_alias=group["source_alias"],
                            canonical_identity=inputs["canonical_identity"], current_trait_profile=current_profile,
                            current_aspects=current_aspects, current_goals=current_goals,
                            scenes=[SceneInput(**scene) for scene in chunk_scenes],
                            perspectives_result=perspectives,
                        )

                    missing_analyses = [index for index, result in enumerate(analysis_results) if result is None]
                    generated_analyses = await asyncio.gather(*[
                        psychology_chunk(index, scene_chunks[index], perspective_results[index])
                        for index in missing_analyses
                    ], return_exceptions=True)
                    analysis_errors = []
                    for index, output in zip(missing_analyses, generated_analyses, strict=True):
                        if isinstance(output, Exception):
                            analysis_errors.append(output)
                            continue
                        if perspective_call_records[index]:
                            known_calls = {(call.stage, call.usage_tag) for call in output.llm_calls}
                            prior_calls = [LLMCallRecord.model_validate(item) for item in perspective_call_records[index]]
                            output = output.model_copy(update={
                                "llm_calls": [*output.llm_calls, *[
                                    call for call in prior_calls if (call.stage, call.usage_tag) not in known_calls
                                ]],
                            })
                        analysis_results[index] = output
                        old = chunks_store.get(str(index), {})
                        chunks_store[str(index)] = {
                            **(old if isinstance(old, dict) else {}),
                            "version": CHECKPOINT_VERSION, "key": chunk_keys[index],
                            "perspectives": perspective_results[index].model_dump(mode="json"),
                            "perspective_llm_calls": perspective_call_records[index],
                            "analysis": output.model_dump(mode="json"),
                        }
                        await progress.chunk_complete(bi, index)
                    if missing_analyses and not await persist_chunk_checkpoints():
                        return {"draft_id": draft_id, "status": "superseded"}
                    if analysis_errors:
                        for index, output in zip(missing_analyses, generated_analyses, strict=True):
                            if isinstance(output, Exception):
                                await progress.chunk_failed(bi, index, stage="psychological_analysis", error=output)
                        await progress.failed(bi)
                        raise analysis_errors[0]

                    analysis = _merge_chunk_analyses(analysis_results)
                    source_checkpoint["analysis"] = analysis.model_dump(mode="json")
                    await progress.analysis_ready(bi)
                    if not await persist_chunk_checkpoints():
                        return {"draft_id": draft_id, "status": "superseded"}
                try:
                    result = await agent.apply_profile_update(
                        analysis=analysis,
                        current_trait_profile=current_profile,
                        current_trait_evidence=current_evidence, batch_id=group["batch_id"],
                        current_aspects=current_aspects,
                        current_goals=current_goals,
                        on_stage=progress.callback(bi),
                    )
                    result = result.model_copy(update={
                        "llm_calls": [*analysis.llm_calls, *agent.llm_calls],
                    })
                except Exception:
                    await progress.failed(bi)
                    raise

                # Apply cumulative state updates in source order.
                current_profile = result.trait_profile
                current_evidence = merge_evidence(current_evidence, result.trait_evidence)
                _apply_aspect_ops(
                    current_aspects, result.aspect_updates,
                    focused_ids=result.focused_aspects,
                )
                _apply_goal_ops(
                    current_goals, result.goal_updates,
                    focused_ids=result.focused_goals,
                )
                br_sub = result.subtitle_change
                if br_sub.operation == "set":
                    current_subtitle = br_sub.subtitle
                elif br_sub.operation == "clear":
                    current_subtitle = None

                await progress.complete(bi)

                all_perspectives.extend(result.perspectives)
                if merged_obs is None:
                    merged_obs = result.observations
                else:
                    merged_obs = _merge_observations(
                        merged_obs, result.observations
                    )
                all_aspect_updates.extend(result.aspect_updates)
                all_goal_updates.extend(result.goal_updates)
                total_llm_calls += result.total_llm_calls
                total_tokens_est += result.total_tokens_est
                total_semantic_corrections += agent.semantic_correction_count
                per_bundle_results.append(result)

            # Refresh once from the final accumulated identity after all scene
            # chunks; the starting description grounded every stage above.
            prior_identity_calls = len(initializer.llm_calls)
            identity_description = await initializer.generate_identity_description(
                canonical_identity=inputs["canonical_identity"], current_profile=current_profile,
                current_aspects=current_aspects, current_goals=current_goals,
            )
            refresh_calls = initializer.llm_calls[prior_identity_calls:]
            total_llm_calls += len(refresh_calls)
            total_tokens_est += sum(item.total_tokens_est for item in refresh_calls)

        finally:
            await client.aclose()

        await update_job_progress(job_id, 0.95, {
            "stage": "Finalizing",
            "draft_id": draft_id,
            "bundles": bundles,
        })
        await sql.refresh(draft)
        if draft.generation_revision != revision:
            return {"draft_id": draft_id, "status": "superseded"}

        # Evidence snapshot from all scenes
        draft.evidence_snapshot = json.dumps([{
            "evidence_id": f"identity:{draft.source_entity_id}", "kind": "identity",
            "text": json.dumps({key: inputs["canonical_identity"].get(key)
                                for key in ("alias", "authored_text", "properties", "entity_type")},
                               ensure_ascii=False),
            "source_id": draft.source_entity_id, "provenance": {"kind": "authored_baseline"},
        }, *[
            {
                "evidence_id": f"scene:{s.scene_id}",
                "kind": "scene",
                "text": f"{s.name}: {s.description}",
                "source_id": s.scene_id,
                "occurred_at": s.created_at,
                "provenance": {},
            }
            for s in all_scene_inputs
        ]])
        draft.source_evidence_ids = json.dumps(
            [f"identity:{draft.source_entity_id}", *[f"scene:{s.scene_id}" for s in all_scene_inputs]]
        )
        draft.evidence_cutoff = datetime.now(timezone.utc).isoformat()

        # ``subtitle_change`` is an orchestration-only result.  It is projected
        # into the timeline/proposal below, but is not part of the persisted
        # EmbodimentObservations API contract.
        obs_dict = (
            merged_obs.model_dump(mode="json", exclude={"subtitle_change"})
            if merged_obs
            else {}
        )
        obs_dict["identity_description"] = {
            "text": identity_description.identity_summary,
            "evidence_ids": [f"identity:{draft.source_entity_id}"],
        }
        obs_dict["important_experiences"] = []
        obs_dict["possible_goals"] = []
        obs_dict["possible_aspects"] = []
        draft.observations = json.dumps(obs_dict)

        final_aspects_for_proposal = [
            {
                "suggestion_id": a.get("id") or _stable_profile_id("aspect", a.get("name", "")),
                "name": a.get("name", ""),
                "category": a.get("category", "identity"),
                "description": a.get("description"),
                "status": a.get("status", "active"),
                "in_focus": bool(a.get("in_focus")),
                "justification": a.get("justification") or "",
                "evidence_ids": a.get("evidence_ids") or [],
            }
            for a in current_aspects
        ]
        final_goals_for_proposal = [
            {
                "suggestion_id": g.get("id") or _stable_profile_id("goal", g.get("title", "")),
                "title": g.get("title", ""),
                "description": g.get("description") or g.get("title", ""),
                "goal_type": g.get("goal_type", "desire"),
                "status": g.get("status", "active"),
                "in_focus": bool(g.get("in_focus")),
                "justification": g.get("justification") or "",
                "evidence_ids": g.get("evidence_ids") or [],
            }
            for g in current_goals
        ]

        draft.generated_proposal = json.dumps({
            "name": inputs["canonical_identity"]["alias"],
            "subtitle": current_subtitle,
            "background_story": str(
                inputs["canonical_identity"].get("authored_text")
                or inputs["canonical_identity"].get("generated_text")
                or inputs["canonical_identity"]["alias"]
            ),
            "image_url": inputs["canonical_identity"].get("avatar_url"),
            "trait_profile": current_profile.model_dump(mode="json"),
            "identity_description": identity_description.model_dump(mode="json"),
            "aspects": final_aspects_for_proposal,
            "goals": final_goals_for_proposal,
        })
        # Keep only checkpoints compatible with the exact inputs used here.
        draft.generation_checkpoints = json.dumps(checkpoint_store, ensure_ascii=False)

        # Build timeline with per-bundle revisions
        draft.timeline_projection = _build_timeline(
            source_entity_id=draft.source_entity_id,
            source_entity_alias=inputs["source_entity_alias"],
            canonical_identity=inputs["canonical_identity"],
            current_trait_profile=initial_profile,
            initial_evidence=initial_evidence,
            current_aspects=inputs["current_aspects"],
            current_goals=inputs["current_goals"],
            current_subtitle=initial_subtitle,
            per_bundle_results=per_bundle_results,
            starting_revision=max(0, int(inputs.get("latest_revision", -1))),
            source_groups=source_groups,
        )
        debug_artifacts.write_final(
            input={
                "draft_id": draft_id, "revision": revision,
                "canonical_identity": inputs["canonical_identity"],
                "source_groups": source_groups, "initial_trait_profile": initial_profile,
                "initial_trait_evidence": initial_evidence,
            },
            output={
                "trait_profile": current_profile, "trait_evidence": current_evidence,
                "aspects": current_aspects, "goals": current_goals,
                "subtitle": current_subtitle, "observations": merged_obs,
                "perspectives": all_perspectives,
                "aspect_updates": all_aspect_updates, "goal_updates": all_goal_updates,
                "generated_proposal": json.loads(draft.generated_proposal),
                "timeline_projection": draft.timeline_projection,
            },
        )
        draft.provider = settings.model_character_agent_character_incorporation.provider
        draft.model = settings.model_character_agent_character_incorporation.name
        draft.prompt_version = PROMPT_VERSION
        draft.generated_at = datetime.now(timezone.utc)
        draft.status = CharacterEmbodimentDraftStatus.READY
        await sql.commit()
        await update_job_progress(job_id, 1.0, {
            "stage": "Complete",
            "draft_id": draft_id,
            "bundles": bundles,
        })
        elapsed_seconds = time.monotonic() - generation_started
        stage_seconds: dict[str, float] = {}
        for agent in agents:
            for stage, elapsed in agent.stage_elapsed_seconds.items():
                stage_seconds[stage] = stage_seconds.get(stage, 0.0) + elapsed
        logger = logging.getLogger(__name__)
        logger.info(
            "Embodiment complete for draft=%s: %d bundles, %d LLM calls, "
            "~%d total tokens, %d semantic corrections, %.2fs wall time, "
            "stage_seconds=%s",
            draft_id, total, total_llm_calls, total_tokens_est,
            total_semantic_corrections,
            elapsed_seconds, {key: round(value, 2) for key, value in stage_seconds.items()},
        )
        return {
            "draft_id": draft_id, "status": "ready", "revision": revision,
            "llm_calls": total_llm_calls, "total_tokens_est": total_tokens_est,
            "semantic_corrections": total_semantic_corrections,
        }


async def _fail(draft_id: str, revision: int, error: str) -> None:
    async with AsyncSessionMaker() as sql:
        draft = await sql.get(CharacterEmbodimentDraft, draft_id)
        if draft and draft.generation_revision == revision:
            draft.status = CharacterEmbodimentDraftStatus.FAILED
            draft.error_message = error
            await sql.commit()
