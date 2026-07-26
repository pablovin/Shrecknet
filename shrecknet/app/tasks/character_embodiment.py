"""Celery entry point for atomic EmbodyAgent embodiment drafts."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.character_agent.embody_agent import EmbodyAgent
from app.jobs.character_agent.embody_agent_prompts import PROMPT_VERSION
from app.models.character_embodiment import CharacterEmbodimentDraft, CharacterEmbodimentDraftStatus
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
    1: "Scene perspective",
    2: "Observations",
    3: "Axis updates",
    4: "Aspect updates",
    5: "Goal updates",
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
        run_async(_fail(draft_id, revision, str(exc)))
        run_async(mark_job_failed(job_id, str(exc)))
        raise


def _build_timeline(
    source_entity_id: str,
    source_entity_alias: str,
    canonical_identity: dict,
    current_axes: dict[str, int],
    current_aspects: list[dict],
    current_goals: list[dict],
    current_subtitle: str | None,
    per_bundle_results: list[Any],
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
            suggestion_id=_id("aspect", a.get("name", "")),
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
            suggestion_id=_id("goal", g.get("title", "")),
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
        _apply_aspect_ops(cum_aspects, br.aspect_updates)
        _apply_goal_ops(cum_goals, br.goal_updates)

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
                    memory_strength=p.memory_strength, importance=p.importance,
                    status=p.status,
                )
                for p in br.perspectives
            ],
            axis_changes=b_axes,
            aspects=b_aspects,
            goals=b_goals,
            subtitle_change=(getattr(br, "subtitle_change", None)
                             or SubtitleChangeProposal()),
            resulting_revision=rev_n,
        ))

    return CharacterTimelineProjection(
        revisions=revisions,
        source_projections=source_projections,
    ).model_dump_json()


def _make_on_stage(
    job_id: int, draft_id: str, bundles: list[dict], bi: int,
    source_alias: str, total: int, bundle_start: list[float],
    state: dict,
) -> Any:
    """Return an on_stage callback that captures loop variables by value for proper async closure."""
    done_accum: set[int] = set()
    prev_active: list[int] = []

    async def on_stage(stage_label: str, active_stages: list[int]) -> None:
        nonlocal done_accum, prev_active
        if prev_active:
            done_accum.update(prev_active)
        prev_active = active_stages

        step_progress = {(1,): 0.20, (2,): 0.50, (3, 4, 5): 0.85}
        pct = step_progress.get(tuple(active_stages) if active_stages else (0,), 0.5)
        per_bundle = 0.80 / max(total, 1)
        overall = 0.10 + bi * per_bundle + per_bundle * pct

        elapsed = time.monotonic() - bundle_start[0]

        bundles[bi] = {
            "index": bi + 1,
            "source_name": source_alias,
            "status": "processing" if active_stages else "done",
            "active_steps": list(active_stages),
            "done_steps": sorted(done_accum),
            "elapsed_seconds": round(elapsed, 1),
        }
        await update_job_progress(job_id, overall, {
            "stage": _stage_label(source_alias, active_stages),
            "draft_id": draft_id,
            "bundles": list(bundles),
        })

    return on_stage, done_accum, prev_active


def _apply_axis_updates(
    axes: dict[str, int], updates: list,
) -> None:
    for u in updates:
        current = axes[u.axis]
        axes[u.axis] = max(current - 5, min(current + 5, u.new_value))


def _apply_aspect_ops(
    aspects: list[dict], updates: list,
) -> None:
    for upd in updates:
        op = upd.operation.value
        if op == "remove":
            aspects[:] = [a for a in aspects if a.get("name") != upd.name]
        elif op == "update":
            for a in aspects:
                if a.get("name") == upd.name:
                    a.update(
                        category=upd.category, description=upd.description,
                        importance=upd.importance, intensity=upd.intensity,
                        justification=upd.justification, confidence=upd.confidence,
                        evidence_ids=list(upd.evidence_ids),
                    )
                    break
            else:
                aspects.append(dict(
                    name=upd.name, category=upd.category or "identity",
                    description=upd.description, importance=upd.importance or 3,
                    intensity=upd.intensity, justification=upd.justification,
                    confidence=upd.confidence, evidence_ids=list(upd.evidence_ids),
                ))
        elif op == "add":
            aspects.append(dict(
                name=upd.name, category=upd.category or "identity",
                description=upd.description, importance=upd.importance or 3,
                intensity=upd.intensity, justification=upd.justification,
                confidence=upd.confidence, evidence_ids=list(upd.evidence_ids),
            ))


def _apply_goal_ops(
    goals: list[dict], updates: list,
) -> None:
    for upd in updates:
        op = upd.operation.value
        if op in ("remove", "complete"):
            goals[:] = [g for g in goals if g.get("title") != upd.title]
        elif op == "update":
            for g in goals:
                if g.get("title") == upd.title:
                    g.update(
                        description=upd.description, goal_type=upd.goal_type,
                        priority=upd.priority, commitment=upd.commitment,
                        basis=upd.basis, justification=upd.justification,
                        confidence=upd.confidence, evidence_ids=list(upd.evidence_ids),
                    )
                    break
            else:
                goals.append(dict(
                    title=upd.title, description=upd.description or upd.title,
                    goal_type=upd.goal_type or "desire", priority=upd.priority or 50,
                    commitment=upd.commitment or 50, basis=upd.basis or "inferred",
                    justification=upd.justification, confidence=upd.confidence,
                    evidence_ids=list(upd.evidence_ids or ["generated"]),
                ))
        elif op == "add":
            goals.append(dict(
                title=upd.title, description=upd.description or upd.title,
                goal_type=upd.goal_type or "desire", priority=upd.priority or 50,
                commitment=upd.commitment or 50, basis=upd.basis or "inferred",
                justification=upd.justification, confidence=upd.confidence,
                evidence_ids=list(upd.evidence_ids or ["generated"]),
            ))


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
        per_bundle_results: list = []

        client = ShreckLLMClient(
            base_url=settings.shreckllm_base_url,
            timeout=settings.shreckllm_request_timeout_s,
            max_retries=settings.shreckllm_max_retries,
        )
        try:
            agent = EmbodyAgent(
                llm_client=client,
                model=settings.model_character_agent_embodiment,
                max_goals=settings.character_agent_embodiment_max_goals,
                max_aspects=settings.character_agent_embodiment_max_aspects,
            )

            for bi, group in enumerate(source_groups):
                source_alias = group["source_alias"]
                source_id = group["source_id"] or draft.source_entity_id
                scene_inputs = [
                    SceneInput(scene_id=s["scene_id"], name=s["name"],
                               description=s["description"], created_at=s["created_at"])
                    for s in group["scenes"]
                ]

                bundle_start: list[float] = [time.monotonic()]
                on_stage, _done_accum, _prev_active = _make_on_stage(
                    job_id, draft_id, bundles, bi,
                    source_alias, total, bundle_start, {},
                )

                result = await agent.run(
                    source_entity_id=source_id,
                    source_entity_alias=source_alias,
                    canonical_identity=inputs["canonical_identity"],
                    current_behavioural_axes=current_axes,
                    current_aspects=current_aspects,
                    current_goals=current_goals,
                    scenes=scene_inputs,
                    on_stage=on_stage,
                )

                # Mark bundle done
                if _prev_active:
                    _done_accum.update(_prev_active)
                elapsed = time.monotonic() - bundle_start[0]
                bundles[bi] = {
                    "index": bi + 1,
                    "source_name": source_alias,
                    "status": "done",
                    "active_steps": [],
                    "done_steps": [1, 2, 3, 4, 5],
                    "elapsed_seconds": round(elapsed, 1),
                }

                # Merge perspectives
                all_perspectives.extend(result.perspectives)

                # Merge observations
                if merged_obs is None:
                    merged_obs = result.observations
                else:
                    merged_obs = _merge_observations(merged_obs, result.observations)

                # Apply cumulative state updates
                _apply_axis_updates(current_axes, result.axis_updates)
                _apply_aspect_ops(current_aspects, result.aspect_updates)
                _apply_goal_ops(current_goals, result.goal_updates)
                br_sub = result.subtitle_change
                if br_sub.operation == "set":
                    current_subtitle = br_sub.subtitle
                elif br_sub.operation == "clear":
                    current_subtitle = None

                all_axis_updates.extend(result.axis_updates)
                all_aspect_updates.extend(result.aspect_updates)
                all_goal_updates.extend(result.goal_updates)
                total_llm_calls += result.total_llm_calls
                total_tokens_est += result.total_tokens_est
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
                "suggestion_id": str(uuid4()),
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
                "suggestion_id": str(uuid4()),
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
        )
        draft.provider = settings.model_character_agent_embodiment.provider
        draft.model = settings.model_character_agent_embodiment.name
        draft.prompt_version = PROMPT_VERSION
        draft.generated_at = datetime.now(timezone.utc)
        draft.status = CharacterEmbodimentDraftStatus.READY
        await sql.commit()
        await update_job_progress(job_id, 1.0, {
            "stage": "Complete",
            "draft_id": draft_id,
            "bundles": bundles,
        })
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            "Embodiment complete for draft=%s: %d bundles, %d LLM calls, ~%d total tokens",
            draft_id, total, total_llm_calls, total_tokens_est,
        )
        return {
            "draft_id": draft_id, "status": "ready", "revision": revision,
            "llm_calls": total_llm_calls, "total_tokens_est": total_tokens_est,
        }


async def _fail(draft_id: str, revision: int, error: str) -> None:
    async with AsyncSessionMaker() as sql:
        draft = await sql.get(CharacterEmbodimentDraft, draft_id)
        if draft and draft.generation_revision == revision:
            draft.status = CharacterEmbodimentDraftStatus.FAILED
            draft.error_message = error
            await sql.commit()
