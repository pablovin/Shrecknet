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
from app.jobs.character_agent.embody_agent_prompts import PROMPT_VERSION
from app.models.character_embodiment import (
    CharacterEmbodimentCheckpoint,
    CharacterEmbodimentDraft,
    CharacterEmbodimentDraftStatus,
)
from app.services.character_embodiment_service import CharacterEmbodimentService
from app.schemas.character_agent import (
    CharacterIdentityRevisionProjection,
    CharacterSourceProjection,
    CharacterTimelineProjection,
    EmbodimentAxisProposal,
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
    3: "Cross-scene observations",
    4: "Profile updates",
}


def _stable_profile_id(kind: str, value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return f"{kind}:{normalized or 'unnamed'}"


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
    axes: dict,
    aspects: list,
    goals: list,
    model_targets: dict[str, str],
) -> str:
    material = {
        "revision": revision,
        "prompt_version": PROMPT_VERSION,
        "source_group": source_group,
        "canonical_identity": canonical_identity,
        "profile": {"axes": axes, "aspects": aspects, "goals": goals},
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


def _build_timeline(
    source_entity_id: str,
    source_entity_alias: str,
    canonical_identity: dict,
    current_axes: dict[str, int],
    current_aspects: list[dict],
    current_goals: list[dict],
    current_subtitle: str | None,
    per_bundle_results: list[Any],
    *,
    max_aspects: int | None = None,
    max_goals: int | None = None,
) -> str:
    from app.schemas.character_agent import AxisChangeData, AspectUpdateData, GoalUpdateData
    from app.services.character_agent_service import _normalize_name

    def _id(kind: str, name: str) -> str:
        return f"{kind}:{_normalize_name(name)}"

    def map_axis(a: AxisChangeData) -> EmbodimentAxisProposal:
        return EmbodimentAxisProposal(
            axis=a.axis, value=a.new_value,
            justification=a.justification, confidence=a.confidence,
            evidence_ids=a.evidence_ids or ["generated"],
        )

    def map_aspect(a: dict) -> EmbodimentAspectProposal:
        return EmbodimentAspectProposal(
            suggestion_id=a.get("id") or _id("aspect", a.get("name", "")),
            name=a.get("name", ""),
            category=a.get("category", "identity"),
            description=a.get("description"),
            importance=a.get("importance", 3),
            intensity=a.get("intensity"),
            justification=a.get("justification") or "Proposed aspect.",
            confidence=a.get("confidence") or 0.5,
            evidence_ids=a.get("evidence_ids") or ["generated"],
        )

    def map_goal(g: dict) -> EmbodimentGoalProposal:
        return EmbodimentGoalProposal(
            suggestion_id=g.get("id") or _id("goal", g.get("title", "")),
            title=g.get("title", ""),
            description=g.get("description") or g.get("title", ""),
            goal_type=g.get("goal_type", "desire"),
            priority=g.get("priority", 50),
            commitment=g.get("commitment", 50),
            justification=g.get("justification") or "Proposed goal.",
            confidence=g.get("confidence") or 0.5,
            evidence_ids=g.get("evidence_ids") or ["generated"],
            basis="inferred",
        )

    alias = str(canonical_identity.get("alias") or source_entity_alias)

    # Revision 0 — starting state before any bundle
    rev0 = CharacterIdentityRevisionProjection(
        revision_number=0, name=alias, subtitle=current_subtitle,
        trait_adherence=80,
        behavioural_axes=dict(current_axes),
        active_aspects=[map_aspect(a) for a in current_aspects if a.get("name")],
        active_goals=[map_goal(g) for g in current_goals if g.get("title")],
    )

    revisions: list[CharacterIdentityRevisionProjection] = [rev0]
    source_projections: list[CharacterSourceProjection] = []

    # Cumulative state that evolves through bundles
    cum_axes = dict(current_axes)
    cum_aspects = [dict(a) for a in current_aspects]
    cum_goals = [dict(g) for g in current_goals]
    cum_subtitle = current_subtitle

    for i, br in enumerate(per_bundle_results):
        br_source_id = str(getattr(br, "source_entity_id", source_entity_id) or source_entity_id)

        _apply_axis_updates(cum_axes, br.axis_updates)
        _apply_aspect_ops(
            cum_aspects, br.aspect_updates, max_active=max_aspects,
        )
        _apply_goal_ops(
            cum_goals, br.goal_updates, max_active=max_goals,
        )

        br_sub = getattr(br, "subtitle_change", None)
        if br_sub:
            if br_sub.operation == "set":
                cum_subtitle = br_sub.subtitle
            elif br_sub.operation == "clear":
                cum_subtitle = None

        rev_n = CharacterIdentityRevisionProjection(
            revision_number=i + 1,
            source_group_id=br_source_id,
            name=alias,
            subtitle=cum_subtitle,
            trait_adherence=80,
            behavioural_axes=dict(cum_axes),
            active_aspects=[map_aspect(a) for a in cum_aspects if a.get("name")],
            active_goals=[map_goal(g) for g in cum_goals if g.get("title")],
        )
        revisions.append(rev_n)

        b_axes = [map_axis(a) for a in br.axis_updates]

        b_aspects: list[EmbodimentAspectProposal] = []
        for upd in br.aspect_updates:
            if upd.operation.value in ("add", "update"):
                b_aspects.append(EmbodimentAspectProposal(
                    suggestion_id=_id("aspect", upd.name),
                    name=upd.name,
                    category=upd.category or "identity",
                    description=upd.description,
                    importance=upd.importance or 3,
                    intensity=upd.intensity,
                    justification=upd.justification,
                    confidence=upd.confidence,
                    evidence_ids=list(upd.evidence_ids or ["generated"]),
                ))

        b_goals: list[EmbodimentGoalProposal] = []
        for upd in br.goal_updates:
            if upd.operation.value in ("add", "update"):
                b_goals.append(EmbodimentGoalProposal(
                    suggestion_id=_id("goal", upd.title),
                    title=upd.title,
                    description=upd.description or upd.title,
                    goal_type=upd.goal_type or "desire",
                    priority=upd.priority or 50,
                    commitment=upd.commitment or 50,
                    justification=upd.justification,
                    confidence=upd.confidence,
                    evidence_ids=list(upd.evidence_ids or ["generated"]),
                    basis=upd.basis or "inferred",
                ))

        source_projections.append(CharacterSourceProjection(
            source_group_id=br_source_id,
            starting_revision_number=i,
            perspectives=[
                ProjectedScenePerspective(
                    scene_id=p.scene_id, source_type=p.source_type,
                    awareness_level=p.awareness_level, confidence=p.confidence,
                    summary=p.summary, interpretation=p.interpretation,
                    character_reflection=p.character_reflection,
                    memory_strength=p.memory_strength, importance=p.importance,
                    status=p.status,
                    emotions=p.emotions, beliefs=p.beliefs, impacts=p.impacts,
                )
                for p in br.perspectives
            ],
            axis_changes=b_axes,
            aspects=b_aspects,
            goals=b_goals,
            completed_goal_titles=[
                upd.title for upd in br.goal_updates
                if upd.operation.value == "complete"
            ],
            subtitle_change=(getattr(br, "subtitle_change", None)
                             or SubtitleChangeProposal()),
            llm_calls=list(br.llm_calls),
            resulting_revision=rev_n,
        ))

    return CharacterTimelineProjection(
        revisions=revisions,
        source_projections=source_projections,
    ).model_dump_json()


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
            self.done[index].update({1, 2, 3, 4})
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


def _apply_axis_updates(
    axes: dict[str, int], updates: list,
) -> None:
    for u in updates:
        current = axes[u.axis]
        axes[u.axis] = max(current - 5, min(current + 5, u.new_value))


def _apply_aspect_ops(
    aspects: list[dict], updates: list, *, max_active: int | None = None,
) -> None:
    for upd in updates:
        op = upd.operation.value
        if op == "remove":
            key = _profile_key(upd.name)
            aspects[:] = [
                a for a in aspects if _profile_key(a.get("name")) != key
            ]
        elif op == "update":
            for a in aspects:
                if _profile_key(a.get("name")) == _profile_key(upd.name):
                    changes = {
                        "justification": upd.justification,
                        "confidence": upd.confidence,
                        "evidence_ids": list(upd.evidence_ids),
                    }
                    for field in ("category", "description", "importance", "intensity"):
                        value = getattr(upd, field)
                        if value is not None:
                            changes[field] = value
                    a.update(changes)
                    break
            else:
                aspects.append(dict(
                    id=_stable_profile_id("aspect", upd.name),
                    name=upd.name, category=upd.category or "identity",
                    description=upd.description, importance=upd.importance or 3,
                    intensity=upd.intensity, justification=upd.justification,
                    confidence=upd.confidence, evidence_ids=list(upd.evidence_ids),
                    _profile_is_new=True,
                ))
        elif op == "add":
            aspects.append(dict(
                id=_stable_profile_id("aspect", upd.name),
                name=upd.name, category=upd.category or "identity",
                description=upd.description, importance=upd.importance or 3,
                intensity=upd.intensity, justification=upd.justification,
                confidence=upd.confidence, evidence_ids=list(upd.evidence_ids),
                _profile_is_new=True,
            ))
    _retain_strongest_profile_items(
        aspects, max_active=max_active, score_field="importance",
        default_score=3,
    )


def _apply_goal_ops(
    goals: list[dict], updates: list, *, max_active: int | None = None,
) -> None:
    for upd in updates:
        op = upd.operation.value
        if op in ("remove", "complete"):
            key = _profile_key(upd.title)
            goals[:] = [
                g for g in goals if _profile_key(g.get("title")) != key
            ]
        elif op == "update":
            for g in goals:
                if _profile_key(g.get("title")) == _profile_key(upd.title):
                    changes = {
                        "justification": upd.justification,
                        "confidence": upd.confidence,
                        "evidence_ids": list(upd.evidence_ids),
                    }
                    for field in (
                        "description", "goal_type", "priority", "commitment", "basis",
                    ):
                        value = getattr(upd, field)
                        if value is not None:
                            changes[field] = value
                    g.update(changes)
                    break
            else:
                goals.append(dict(
                    id=_stable_profile_id("goal", upd.title),
                    title=upd.title, description=upd.description or upd.title,
                    goal_type=upd.goal_type or "desire",
                    priority=50 if upd.priority is None else upd.priority,
                    commitment=50 if upd.commitment is None else upd.commitment,
                    basis=upd.basis or "inferred",
                    justification=upd.justification, confidence=upd.confidence,
                    evidence_ids=list(upd.evidence_ids or ["generated"]),
                    _profile_is_new=True,
                ))
        elif op == "add":
            goals.append(dict(
                id=_stable_profile_id("goal", upd.title),
                title=upd.title, description=upd.description or upd.title,
                goal_type=upd.goal_type or "desire",
                priority=50 if upd.priority is None else upd.priority,
                commitment=50 if upd.commitment is None else upd.commitment,
                basis=upd.basis or "inferred",
                justification=upd.justification, confidence=upd.confidence,
                evidence_ids=list(upd.evidence_ids or ["generated"]),
                _profile_is_new=True,
            ))
    _retain_strongest_profile_items(
        goals, max_active=max_active, score_field="priority",
        default_score=50,
    )


def _retain_strongest_profile_items(
    items: list[dict],
    *,
    max_active: int | None,
    score_field: str,
    default_score: int,
) -> None:
    """Keep high-value active items, preferring newer items when scores tie."""
    if max_active is None or len(items) <= max_active:
        return

    def score(item: dict) -> int:
        value = item.get(score_field)
        return default_score if value is None else int(value)

    ranked = sorted(
        enumerate(items),
        key=lambda pair: (
            score(pair[1]),
            bool(pair[1].get("_profile_is_new")),
            str(pair[1].get("created_at") or ""),
            pair[0],
        ),
        reverse=True,
    )
    retained = {index for index, _item in ranked[:max_active]}
    items[:] = [item for index, item in enumerate(items) if index in retained]


def _profile_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _merge_observations(target: Any, source: Any) -> Any:
    """Merge source observation lists into target in-place."""
    list_fields = [
        "recurring_behaviours", "motivations", "values", "fears",
        "conflicts", "relationships", "contradictions", "evidence_gaps",
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
        source_groups = inputs.get("source_groups", [])
        if not source_groups:
            raise ValueError("no scenes found for this entity")

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
        current_axes = dict(inputs["current_axes"])
        current_aspects = [dict(a) for a in inputs["current_aspects"]]
        current_goals = [dict(g) for g in inputs["current_goals"]]
        initial_subtitle = inputs["canonical_identity"].get("subtitle") or None
        current_subtitle = initial_subtitle

        all_perspectives: list = []
        merged_obs: Any = None
        all_axis_updates: list = []
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
        analysis_limit = asyncio.Semaphore(
            settings.character_agent_embodiment_source_concurrency
        )
        snapshot_axes = dict(current_axes)
        snapshot_aspects = [dict(item) for item in current_aspects]
        snapshot_goals = [dict(item) for item in current_goals]
        stage_model_targets = {
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
        checkpoint_keys = [
            _checkpoint_cache_key(
                revision=revision,
                source_group=group,
                canonical_identity=inputs["canonical_identity"],
                axes=snapshot_axes,
                aspects=snapshot_aspects,
                goals=snapshot_goals,
                model_targets=stage_model_targets,
            )
            for group in source_groups
        ]
        checkpoint_rows = (
            await sql.execute(select(CharacterEmbodimentCheckpoint).where(
                CharacterEmbodimentCheckpoint.draft_id == draft_id,
                CharacterEmbodimentCheckpoint.generation_revision == revision,
            ))
        ).scalars().all()
        checkpoints_by_source: list[dict[str, dict]] = [
            {} for _group in source_groups
        ]
        for checkpoint in checkpoint_rows:
            index = checkpoint.source_index
            if (
                0 <= index < total
                and checkpoint.cache_key == checkpoint_keys[index]
                and checkpoint.prompt_version == PROMPT_VERSION
                and checkpoint.model_target == stage_model_targets.get(checkpoint.stage)
            ):
                checkpoints_by_source[index][checkpoint.stage] = json.loads(
                    checkpoint.payload
                )
        for index, reused in enumerate(checkpoints_by_source):
            bundles[index]["reused_stages"] = sorted(reused)
        agents: list[EmbodyAgent] = []
        analysis_tasks: list[asyncio.Task] = []

        async def analyze_source(index: int):
            group = source_groups[index]
            source_alias = group["source_alias"]
            source_id = group["source_id"] or draft.source_entity_id
            scene_inputs = [
                SceneInput(
                    scene_id=scene["scene_id"],
                    name=scene["name"],
                    description=scene["description"],
                    created_at=scene["created_at"],
                )
                for scene in group["scenes"]
            ]
            async with analysis_limit:
                try:
                    async def save_stage(stage: str, value: Any) -> None:
                        await _save_checkpoint(
                            draft_id=draft_id,
                            revision=revision,
                            source_index=index,
                            source_entity_id=str(source_id),
                            stage=stage,
                            cache_key=checkpoint_keys[index],
                            model_target=stage_model_targets[stage],
                            payload=value.model_dump(mode="json"),
                        )
                        bundles[index]["checkpointed_stages"] = sorted({
                            *bundles[index].get("checkpointed_stages", []),
                            stage,
                        })

                    analysis = await agents[index].analyze(
                        source_entity_id=source_id,
                        source_entity_alias=source_alias,
                        canonical_identity=inputs["canonical_identity"],
                        current_behavioural_axes=snapshot_axes,
                        current_aspects=snapshot_aspects,
                        current_goals=snapshot_goals,
                        scenes=scene_inputs,
                        on_stage=progress.callback(index),
                        stage_checkpoints=checkpoints_by_source[index],
                        on_checkpoint=save_stage,
                    )
                    await progress.analysis_ready(index)
                    return analysis
                except Exception:
                    await progress.failed(index)
                    raise

        try:
            agents = [
                EmbodyAgent(
                    llm_client=client,
                    character_incorporation_model=(
                        settings.model_character_agent_character_incorporation
                    ),
                    scene_interpretation_model=(
                        settings.model_character_agent_scene_interpretation
                    ),
                    character_update_model=settings.model_character_agent_update,
                    max_goals=settings.character_agent_embodiment_max_goals,
                    max_aspects=settings.character_agent_embodiment_max_aspects,
                    semantic_correction_attempts=(
                        settings.character_agent_embodiment_semantic_correction_attempts
                    ),
                )
                for _group in source_groups
            ]
            analysis_tasks = [
                asyncio.create_task(analyze_source(index))
                for index in range(total)
            ]

            for bi, analysis_task in enumerate(analysis_tasks):
                analysis = await analysis_task
                try:
                    result = await agents[bi].apply_profile_update(
                        analysis=analysis,
                        current_behavioural_axes=current_axes,
                        current_aspects=current_aspects,
                        current_goals=current_goals,
                        on_stage=progress.callback(bi),
                    )
                except Exception:
                    await progress.failed(bi)
                    raise

                # Apply cumulative state updates in source order.
                _apply_axis_updates(current_axes, result.axis_updates)
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
                all_axis_updates.extend(result.axis_updates)
                all_aspect_updates.extend(result.aspect_updates)
                all_goal_updates.extend(result.goal_updates)
                total_llm_calls += result.total_llm_calls
                total_tokens_est += result.total_tokens_est
                total_semantic_corrections += agents[bi].semantic_correction_count
                per_bundle_results.append(result)

        finally:
            for task in analysis_tasks:
                if not task.done():
                    task.cancel()
            if analysis_tasks:
                await asyncio.gather(*analysis_tasks, return_exceptions=True)
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
        draft.evidence_snapshot = json.dumps([
            {
                "evidence_id": f"scene:{s.scene_id}",
                "kind": "scene",
                "text": f"{s.name}: {s.description}",
                "source_id": s.scene_id,
                "occurred_at": s.created_at,
                "provenance": {},
            }
            for s in all_scene_inputs
        ])
        draft.source_evidence_ids = json.dumps(
            [f"scene:{s.scene_id}" for s in all_scene_inputs]
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
            "evidence_ids": [f"scene:{s.scene_id}" for s in all_scene_inputs[:1]] if all_scene_inputs else [],
        }
        obs_dict["important_experiences"] = []
        obs_dict["possible_goals"] = []
        obs_dict["possible_aspects"] = []
        draft.observations = json.dumps(obs_dict)

        # Proposal uses the FINAL cumulative axes, not just deltas
        final_axes_for_proposal = [
            {"axis": k, "value": v,
             "justification": "Cumulative after all bundles.",
             "confidence": 0.5, "evidence_ids": ["cumulative"]}
            for k, v in current_axes.items()
        ]
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
            "behavioural_axes": final_axes_for_proposal,
            "aspects": final_aspects_for_proposal,
            "goals": final_goals_for_proposal,
        })

        # Build timeline with per-bundle revisions
        draft.timeline_projection = _build_timeline(
            source_entity_id=draft.source_entity_id,
            source_entity_alias=inputs["source_entity_alias"],
            canonical_identity=inputs["canonical_identity"],
            current_axes=inputs["current_axes"],
            current_aspects=inputs["current_aspects"],
            current_goals=inputs["current_goals"],
            current_subtitle=initial_subtitle,
            per_bundle_results=per_bundle_results,
            max_aspects=settings.character_agent_embodiment_max_aspects,
            max_goals=settings.character_agent_embodiment_max_goals,
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
