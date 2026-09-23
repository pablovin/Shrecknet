"""Celery entry point for atomic EmbodyAgent embodiment drafts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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
    CharacterEmbodimentCheckpoint,
    CharacterEmbodimentDraft,
    CharacterEmbodimentDraftStatus,
)
from app.services.character_embodiment_service import CharacterEmbodimentService
from app.schemas.character_traits import TraitProfile, TraitEvidence, SPEC_VERSION
from app.services.character_trait_service import POLICY_VERSION, chunk_source_scenes, merge_evidence
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
    1: "Character incorporation",
    2: "Psychological enrichment",
    3: "Profile updates",
    4: "Profile updates",  # legacy progress readers
}


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


def _checkpoint_cache_key(
    *,
    revision: int,
    source_group: dict,
    canonical_identity: dict,
    trait_profile: dict,
    trait_evidence: list,
    aspects: list,
    goals: list,
    model_targets: dict[str, str],
    batch_size: int = 10,
) -> str:
    material = {
        "revision": revision, "batch_size": batch_size,
        "prompt_version": PROMPT_VERSION,
        "source_group": source_group,
        "canonical_identity": canonical_identity,
        "profile": {"trait_profile": trait_profile, "trait_evidence": trait_evidence, "aspects": aspects, "goals": goals},
        "spec_version": SPEC_VERSION, "policy_version": POLICY_VERSION,
        "model_targets": model_targets,
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _save_checkpoint(
    *,
    draft_id: str,
    revision: int,
    source_index: int,
    source_entity_id: str,
    stage: str,
    cache_key: str,
    model_target: str,
    payload: dict,
) -> None:
    async with AsyncSessionMaker() as checkpoint_sql:
        existing = await checkpoint_sql.scalar(select(
            CharacterEmbodimentCheckpoint
        ).where(
            CharacterEmbodimentCheckpoint.draft_id == draft_id,
            CharacterEmbodimentCheckpoint.generation_revision == revision,
            CharacterEmbodimentCheckpoint.source_index == source_index,
            CharacterEmbodimentCheckpoint.stage == stage,
        ))
        if existing is None:
            existing = CharacterEmbodimentCheckpoint(
                id=str(uuid4()),
                draft_id=draft_id,
                generation_revision=revision,
                source_index=source_index,
                source_entity_id=source_entity_id,
                stage=stage,
                cache_key=cache_key,
                payload=json.dumps(payload, ensure_ascii=False),
                prompt_version=PROMPT_VERSION,
                model_target=model_target,
            )
            checkpoint_sql.add(existing)
        else:
            existing.source_entity_id = source_entity_id
            existing.cache_key = cache_key
            existing.payload = json.dumps(payload, ensure_ascii=False)
            existing.prompt_version = PROMPT_VERSION
            existing.model_target = model_target
        await checkpoint_sql.commit()


class _EmbodimentProgress:
    """Serialize concurrent bundle progress writes and keep progress monotonic."""

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
        self.lock = asyncio.Lock()
        self.last_progress = 0.10

    def callback(self, index: int):
        async def on_stage(_stage_label_value: str, active_stages: list[int]) -> None:
            await self.stage(index, active_stages)
        return on_stage

    async def stage(self, index: int, active_stages: list[int]) -> None:
        async with self.lock:
            self.starts.setdefault(index, time.monotonic())
            previous = self.active.get(index, [])
            self.done[index].update(previous)
            self.active[index] = list(active_stages)
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
        self.bundles[index] = {
            "index": index + 1,
            "source_name": source_alias,
            "status": status,
            "active_steps": list(self.active.get(index, [])),
            "done_steps": sorted(self.done[index]),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "checkpointed_stages": list(
                self.bundles[index].get("checkpointed_stages", [])
            ),
            "reused_stages": list(self.bundles[index].get("reused_stages", [])),
        }
        completed = sum(len(steps) for steps in self.done.values())
        active_credit = sum(0.5 for steps in self.active.values() if steps)
        calculated = 0.10 + 0.80 * (
            (completed + active_credit) / max(self.total * 4, 1)
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
        await sql.execute(delete(CharacterEmbodimentCheckpoint).where(
            CharacterEmbodimentCheckpoint.draft_id == draft_id,
            CharacterEmbodimentCheckpoint.generation_revision != revision,
        ))
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
                "checkpointed_stages": [],
                "reused_stages": [],
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
        )
        progress = _EmbodimentProgress(
            job_id=job_id,
            draft_id=draft_id,
            bundles=bundles,
            source_groups=source_groups,
        )
        stage_model_targets = {
            "profile_update": f"{settings.model_character_agent_update.provider}:{settings.model_character_agent_update.name}",
            "character_incorporation": (
                f"{settings.model_character_agent_character_incorporation.provider}:"
                f"{settings.model_character_agent_character_incorporation.name}"
            ),
            "scene_interpretation": (
                f"{settings.model_character_agent_scene_interpretation.provider}:"
                f"{settings.model_character_agent_scene_interpretation.name}"
            ),
            "observations": (
                f"{settings.model_character_agent_scene_interpretation.provider}:"
                f"{settings.model_character_agent_scene_interpretation.name}"
            ),
        }
        agents: list[EmbodyAgent] = []

        try:
            def make_agent(*, source_index: int | None = None, source_alias: str | None = None):
                return EmbodyAgent(
                    llm_client=client,
                    character_incorporation_model=settings.model_character_agent_character_incorporation,
                    scene_interpretation_model=settings.model_character_agent_scene_interpretation,
                    character_update_model=settings.model_character_agent_update,
                    max_goals=settings.character_agent_embodiment_max_goals,
                    max_aspects=settings.character_agent_embodiment_max_aspects,
                    semantic_correction_attempts=settings.character_agent_embodiment_semantic_correction_attempts,
                    debug_artifacts=debug_artifacts,
                    debug_source_index=source_index,
                    debug_source_alias=source_alias,
                )
            initializer = make_agent()
            agents.append(initializer)
            baseline_key = _checkpoint_cache_key(revision=revision, source_group={},
                canonical_identity=inputs["canonical_identity"], trait_profile={}, trait_evidence=[],
                aspects=[], goals=[], model_targets=stage_model_targets)
            baseline = await sql.scalar(select(CharacterEmbodimentCheckpoint).where(
                CharacterEmbodimentCheckpoint.draft_id == draft_id,
                CharacterEmbodimentCheckpoint.generation_revision == revision,
                CharacterEmbodimentCheckpoint.source_index == -1,
                CharacterEmbodimentCheckpoint.stage == "baseline",
                CharacterEmbodimentCheckpoint.cache_key == baseline_key))
            # A debug request must execute and record every LLM stage rather than
            # hiding a prior response behind a checkpoint cache hit.
            if baseline and not settings.character_agent_embodiment_debug_artifacts_enabled:
                data = json.loads(baseline.payload)
                current_profile = TraitProfile.model_validate(data["profile"])
                current_evidence = [TraitEvidence.model_validate(item) for item in data["evidence"]]
            else:
                current_profile, current_evidence, _ = await initializer.initialize(
                    canonical_identity=inputs["canonical_identity"], entity_id=draft.source_entity_id)
                await _save_checkpoint(draft_id=draft_id, revision=revision, source_index=-1,
                    source_entity_id=draft.source_entity_id, stage="baseline", cache_key=baseline_key,
                    model_target=stage_model_targets["observations"],
                    payload={"profile":current_profile.model_dump(mode="json"),
                             "evidence":[item.model_dump(mode="json") for item in current_evidence]})
            initial_profile = current_profile.model_copy(deep=True)
            initial_evidence = list(current_evidence)
            total_llm_calls += len(initializer.llm_calls)
            total_tokens_est += sum(item.total_tokens_est for item in initializer.llm_calls)
            for bi, group in enumerate(source_groups):
                agent = make_agent(source_index=bi, source_alias=group["source_alias"])
                agents.append(agent)
                cache_key = _checkpoint_cache_key(revision=revision, source_group=group,
                    canonical_identity=inputs["canonical_identity"],
                    trait_profile=current_profile.model_dump(mode="json"),
                    trait_evidence=[item.model_dump(mode="json") for item in current_evidence],
                    aspects=current_aspects, goals=current_goals, model_targets=stage_model_targets)
                rows = (await sql.execute(select(CharacterEmbodimentCheckpoint).where(
                    CharacterEmbodimentCheckpoint.draft_id == draft_id,
                    CharacterEmbodimentCheckpoint.generation_revision == revision,
                    CharacterEmbodimentCheckpoint.source_index == bi,
                    CharacterEmbodimentCheckpoint.cache_key == cache_key))).scalars().all()
                checkpoints = (
                    {} if settings.character_agent_embodiment_debug_artifacts_enabled else {
                        row.stage: json.loads(row.payload) for row in rows
                        if row.prompt_version == PROMPT_VERSION
                        and row.model_target == stage_model_targets.get(row.stage)
                    }
                )
                debug_artifacts.write_checkpoint(
                    source_index=bi, source_alias=group["source_alias"], checkpoints=checkpoints,
                )
                bundles[bi]["reused_stages"] = sorted(checkpoints)
                async def save_stage(stage, value):
                    await _save_checkpoint(draft_id=draft_id, revision=revision, source_index=bi,
                        source_entity_id=group["source_id"], stage=stage, cache_key=cache_key,
                        model_target=stage_model_targets[stage], payload=value.model_dump(mode="json"))
                analysis = await agent.analyze(
                    source_entity_id=group["source_id"], source_entity_alias=group["source_alias"],
                    canonical_identity=inputs["canonical_identity"], current_trait_profile=current_profile,
                    current_aspects=current_aspects, current_goals=current_goals,
                    scenes=[SceneInput(**scene) for scene in group["scenes"]],
                    on_stage=progress.callback(bi), stage_checkpoints=checkpoints, on_checkpoint=save_stage)
                try:
                    result = await agent.apply_profile_update(
                        analysis=analysis,
                        stage_checkpoint=checkpoints.get("profile_update"), on_checkpoint=save_stage,
                        current_trait_profile=current_profile,
                        current_trait_evidence=current_evidence, batch_id=group["batch_id"],
                        current_aspects=current_aspects,
                        current_goals=current_goals,
                        on_stage=progress.callback(bi),
                    )
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
        await sql.execute(delete(CharacterEmbodimentCheckpoint).where(
            CharacterEmbodimentCheckpoint.draft_id == draft_id,
            CharacterEmbodimentCheckpoint.generation_revision == revision,
        ))
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
            "reused_checkpoint_stages": sum(
                len(bundle.get("reused_stages", [])) for bundle in bundles
            ),
        }


async def _fail(draft_id: str, revision: int, error: str) -> None:
    async with AsyncSessionMaker() as sql:
        draft = await sql.get(CharacterEmbodimentDraft, draft_id)
        if draft and draft.generation_revision == revision:
            draft.status = CharacterEmbodimentDraftStatus.FAILED
            draft.error_message = error
            await sql.commit()
