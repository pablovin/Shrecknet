"""Celery entry point for atomic EmbodyAgent embodiment drafts."""

from __future__ import annotations

import asyncio
import json
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
)
from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts
from app.jobs.character_agent.embody_agent_prompts import PROMPT_VERSION
from app.models.character_embodiment import (
    CharacterEmbodimentDraft,
    CharacterEmbodimentDraftStatus,
)
from app.services.character_embodiment_service import CharacterEmbodimentService
from app.schemas.character_agent import EmbodyAgentAnalysis
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
)
from app.utils.async_helpers import run_async
from app.utils.job_tracking import mark_job_done, mark_job_failed, mark_job_running, update_job_progress

STEP_NAME: dict[int, str] = {
    1: "Perspective",
    2: "Psychological analysis",
    3: "Deterministic source reduction",
}

SCENE_ANALYSIS_CHUNK_SIZE = 5


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
            chunk["done_steps"] = [1, 2]
            chunk["active_steps"] = []
            self.active[index] = sorted({
                step
                for item in self.chunks[index]
                for step in item["active_steps"]
            })
            if all(item["done_steps"] == [1, 2] for item in self.chunks[index]):
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
        "aspect_signals": [item for analysis in analyses for item in analysis.aspect_signals],
        "goal_signals": [item for analysis in analyses for item in analysis.goal_signals],
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
                    max_goals=settings.character_agent_embodiment_max_goals,
                    max_aspects=settings.character_agent_embodiment_max_aspects,
                    # JSON repair is handled once in the shared stage wrapper;
                    # failed units surface directly instead of triggering hidden LLM loops.
                    semantic_correction_attempts=0,
                    debug_artifacts=debug_artifacts,
                    debug_source_index=source_index,
                    debug_source_alias=source_alias,
                )
            initializer = make_agent()
            agents.append(initializer)
            current_profile, current_evidence, _ = await initializer.initialize(
                canonical_identity=inputs["canonical_identity"], entity_id=draft.source_entity_id)
            initial_profile = current_profile.model_copy(deep=True)
            initial_evidence = list(current_evidence)
            total_llm_calls += len(initializer.llm_calls)
            total_tokens_est += sum(item.total_tokens_est for item in initializer.llm_calls)
            for bi, group in enumerate(source_groups):
                agent = make_agent(source_index=bi, source_alias=group["source_alias"])
                agents.append(agent)
                scene_chunks = _scene_analysis_chunks(group)
                progress.configure_chunks(bi, scene_chunks)

                # A source is chronological, but its chunks are a flat data pipeline:
                # every perspective call completes before any psychological analysis starts.
                chunk_agents = [make_agent(
                    source_index=bi,
                    source_alias=f"{group['source_alias']} (chunk {index + 1})",
                ) for index in range(len(scene_chunks))]
                agents.extend(chunk_agents)

                async def perspective_chunk(chunk_index: int, chunk_scenes: list[dict]):
                    await progress.stage(bi, [1], chunk_index=chunk_index)
                    return await chunk_agents[chunk_index].generate_perspectives(
                        source_entity_id=group["source_id"], source_entity_alias=group["source_alias"],
                        canonical_identity=inputs["canonical_identity"], current_trait_profile=current_profile,
                        current_aspects=current_aspects, current_goals=current_goals,
                        scenes=[SceneInput(**scene) for scene in chunk_scenes],
                    )

                perspective_results = await asyncio.gather(*[
                    perspective_chunk(index, scenes) for index, scenes in enumerate(scene_chunks)
                ], return_exceptions=True)
                perspective_errors = [item for item in perspective_results if isinstance(item, Exception)]
                if perspective_errors:
                    for index, item in enumerate(perspective_results):
                        if isinstance(item, Exception):
                            await progress.chunk_failed(bi, index, stage="perspective", error=item)
                    await progress.failed(bi)
                    raise EmbodimentGenerationError(
                        "perspective stage failed for one or more chunks", category="stage_failure",
                        stage="perspective", source_entity_id=group["source_id"],
                        source_entity_alias=group["source_alias"], retryable=True,
                    ) from perspective_errors[0]

                async def psychology_chunk(chunk_index: int, chunk_scenes: list[dict], perspectives):
                    await progress.stage(bi, [2], chunk_index=chunk_index)
                    result = await chunk_agents[chunk_index].analyze(
                        source_entity_id=group["source_id"], source_entity_alias=group["source_alias"],
                        canonical_identity=inputs["canonical_identity"], current_trait_profile=current_profile,
                        current_aspects=current_aspects, current_goals=current_goals,
                        scenes=[SceneInput(**scene) for scene in chunk_scenes],
                        perspectives_result=perspectives,
                    )
                    await progress.chunk_complete(bi, chunk_index)
                    return result

                analysis_results = await asyncio.gather(*[
                    psychology_chunk(index, scenes, perspective_results[index])
                    for index, scenes in enumerate(scene_chunks)
                ], return_exceptions=True)
                analysis_errors = [item for item in analysis_results if isinstance(item, Exception)]
                if analysis_errors:
                    for index, item in enumerate(analysis_results):
                        if isinstance(item, Exception):
                            await progress.chunk_failed(
                                bi, index, stage="psychological_analysis", error=item,
                            )
                    await progress.failed(bi)
                    raise EmbodimentGenerationError(
                        "psychological analysis failed for one or more chunks", category="stage_failure",
                        stage="psychological_analysis", source_entity_id=group["source_id"],
                        source_entity_alias=group["source_alias"], retryable=True,
                    ) from analysis_errors[0]
                analysis = _merge_chunk_analyses(analysis_results)
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
                    max_active=settings.character_agent_embodiment_max_aspects,
                )
                _apply_goal_ops(
                    current_goals, result.goal_updates,
                    max_active=settings.character_agent_embodiment_max_goals,
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
            "text": str(inputs["canonical_identity"].get("alias", "Character")),
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
                "importance": a.get("importance", 3),
                "intensity": a.get("intensity"),
                "justification": a.get("justification") or "Proposed aspect.",
                "confidence": a.get("confidence") or 0.5,
                "evidence_ids": a.get("evidence_ids") or ["generated"],
            }
            for a in current_aspects
        ]
        final_goals_for_proposal = [
            {
                "suggestion_id": g.get("id") or _stable_profile_id("goal", g.get("title", "")),
                "title": g.get("title", ""),
                "description": g.get("description") or g.get("title", ""),
                "goal_type": g.get("goal_type", "desire"),
                "status": "active",
                "priority": g.get("priority", 50),
                "commitment": g.get("commitment", 50),
                "justification": g.get("justification") or "Proposed goal.",
                "confidence": g.get("confidence") or 0.5,
                "evidence_ids": g.get("evidence_ids") or ["generated"],
                "basis": g.get("basis", "inferred"),
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
            "aspects": final_aspects_for_proposal,
            "goals": final_goals_for_proposal,
        })

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
            max_aspects=settings.character_agent_embodiment_max_aspects,
            max_goals=settings.character_agent_embodiment_max_goals,
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
