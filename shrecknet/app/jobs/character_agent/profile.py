"""Shared chronological timeline and profile lifecycle for drafts and Architect."""
from __future__ import annotations
import copy
import re
from typing import Any
from app.schemas.character_traits import TraitProfile, TraitEvidence
from app.schemas.character_agent import (
    CharacterIdentityRevisionProjection, CharacterSourceProjection, CharacterTimelineProjection,
    CharacterAspectStatus, CharacterGoalStatus,
    DisplayReference, EmbodimentAspectProposal, EmbodimentGoalProposal, ProjectedCharacterImpact,
    ProjectedScenePerspective, ProjectedTraitChange, SubtitleChangeProposal,
)

def _stable_profile_id(kind: str, value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return f"{kind}:{normalized or 'unnamed'}"


def _apply_profile_ops(items: list[dict], updates: list, *, kind: str,
                       focused_ids: list[str] | None) -> None:
    """Apply the authoritative lifecycle on a copy; commit only a valid final state.

    Aspect transitions allow active/inactive; goals allow active/completed/
    abandoned/superseded, including explicit reactivation from any resolved state.
    Content operations never change lifecycle. Focus is an independent selection
    of active items and cannot implicitly change status or delete history.
    """
    state = copy.deepcopy(items)
    label = "name" if kind == "aspect" else "title"
    classification = "category" if kind == "aspect" else "goal_type"
    default_classification = "identity" if kind == "aspect" else "desire"
    status_type = CharacterAspectStatus if kind == "aspect" else CharacterGoalStatus
    allowed_statuses = {status.value for status in status_type}
    for upd in updates:
        op = upd.operation.value
        target_id = upd.target_id or upd.candidate_id
        existing = next((item for item in state if str(item.get("id")) == str(target_id)), None)
        status = getattr(upd.status, "value", upd.status)
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"invalid {kind} lifecycle status")
        if op in {"update", "reinforce"} and status is not None:
            raise ValueError("update and reinforce operations cannot change lifecycle status")
        if op == "status" and status is None:
            raise ValueError("status operation requires a lifecycle status")
        if op == "add":
            candidate_id = upd.candidate_id or _stable_profile_id(kind, getattr(upd, label))
            if upd.target_id is not None or any(str(item.get("id")) == candidate_id for item in state):
                raise ValueError(f"duplicate or invalid {kind} addition")
            if status not in {None, "active"}:
                raise ValueError("new profile items must start active")
            description = upd.description
            if kind == "goal" and not description:
                description = getattr(upd, label)
            state.append(dict(
                id=candidate_id, **{label: getattr(upd, label),
                    classification: getattr(getattr(upd, classification), "value", getattr(upd, classification)) or default_classification},
                description=description, status="active", in_focus=bool(upd.in_focus),
                justification=upd.justification, evidence_ids=list(upd.evidence_ids),
            ))
        else:
            if existing is None or upd.candidate_id is not None:
                raise ValueError(f"unknown or invalid {kind} target")
            if op in {"update", "reinforce"}:
                if getattr(upd, label):
                    existing[label] = getattr(upd, label)
                if upd.description is not None:
                    existing["description"] = upd.description
                value = getattr(upd, classification)
                if value is not None:
                    existing[classification] = value.value
            existing["justification"] = upd.justification
            existing["evidence_ids"] = sorted(set(existing.get("evidence_ids", [])) | set(upd.evidence_ids))
            if op == "status":
                existing["status"] = status
    if focused_ids is not None:
        if len(focused_ids) > 10:
            raise ValueError(f"focused {kind} limit exceeded")
        if len(set(focused_ids)) != len(focused_ids):
            raise ValueError("duplicate focus reference")
        by_id = {str(item.get("id")): item for item in state}
        for target_id in focused_ids:
            if target_id not in by_id:
                raise ValueError("focus references unknown profile ID")
            if by_id[target_id].get("status", "active") != "active":
                raise ValueError(f"inactive or resolved {kind} cannot be focused")
        for item in state:
            item["in_focus"] = str(item.get("id")) in focused_ids
    if sum(bool(item.get("in_focus")) for item in state if item.get("status", "active") == "active") > 10:
        raise ValueError(f"focused {kind} limit exceeded")
    items[:] = state


def _apply_aspect_ops(aspects: list[dict], updates: list, *,
                      focused_ids: list[str] | None = None) -> None:
    _apply_profile_ops(aspects, updates, kind="aspect", focused_ids=focused_ids)


def _apply_goal_ops(goals: list[dict], updates: list, *,
                    focused_ids: list[str] | None = None) -> None:
    _apply_profile_ops(goals, updates, kind="goal", focused_ids=focused_ids)


def _profile_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _build_timeline(
    source_entity_id: str,
    source_entity_alias: str,
    canonical_identity: dict,
    current_trait_profile: TraitProfile,
    current_aspects: list[dict],
    current_goals: list[dict],
    current_subtitle: str | None,
    per_bundle_results: list[Any],
    *,
    initial_evidence: list[TraitEvidence] | None = None,
    starting_revision: int = 0,
    source_groups: list[dict[str, Any]] | None = None,
) -> str:
    from app.services.character_agent_service import _normalize_name

    def _id(kind: str, name: str) -> str:
        return f"{kind}:{_normalize_name(name)}"

    def map_aspect(a: dict) -> EmbodimentAspectProposal:
        return EmbodimentAspectProposal(
            suggestion_id=a.get("id") or _id("aspect", a.get("name", "")),
            name=a.get("name", ""),
            category=a.get("category", "identity"),
            description=a.get("description"),
            status=a.get("status", "active"), in_focus=bool(a.get("in_focus")),
            justification=a.get("justification") or "", evidence_ids=a.get("evidence_ids") or [],
        )

    def map_goal(g: dict) -> EmbodimentGoalProposal:
        return EmbodimentGoalProposal(
            suggestion_id=g.get("id") or _id("goal", g.get("title", "")),
            title=g.get("title", ""),
            description=g.get("description") or g.get("title", ""),
            goal_type=g.get("goal_type", "desire"),
            status=g.get("status", "active"), in_focus=bool(g.get("in_focus")),
            justification=g.get("justification") or "", evidence_ids=g.get("evidence_ids") or [],
        )

    alias = str(canonical_identity.get("alias") or source_entity_alias)
    source_groups_by_id = {
        str(group.get("source_id") or group.get("source_group_id")): group
        for group in source_groups or []
    }
    scenes_by_id = {
        str(scene["scene_id"]): scene
        for group in source_groups or [] for scene in group.get("scenes", [])
    }

    def scene_reference(scene_id: str) -> DisplayReference:
        scene = scenes_by_id.get(scene_id, {})
        return DisplayReference(
            id=scene_id, type="scene", name=str(scene.get("name") or scene_id),
            description=scene.get("description"), instance_name=alias,
        )

    def evidence_references(evidence_ids: list[str]) -> list[DisplayReference]:
        result = []
        for evidence_id in evidence_ids:
            scene_id = evidence_id.removeprefix("scene:")
            if evidence_id.startswith("scene:") and scene_id in scenes_by_id:
                result.append(scene_reference(scene_id).model_copy(update={"id": evidence_id}))
            else:
                result.append(DisplayReference(
                    id=evidence_id, type="evidence", name="Evidence",
                    description=None, instance_name=alias,
                ))
        return result

    # Revision 0 — starting state before any bundle
    rev0 = CharacterIdentityRevisionProjection(
        revision_number=starting_revision, name=alias, subtitle=current_subtitle,
        trait_profile=current_trait_profile.model_copy(deep=True),
        trait_evidence=initial_evidence or [],
        active_aspects=[map_aspect(a) for a in current_aspects if a.get("name")],
        active_goals=[map_goal(g) for g in current_goals if g.get("title")],
    )

    revisions: list[CharacterIdentityRevisionProjection] = [rev0]
    source_projections: list[CharacterSourceProjection] = []

    # Cumulative state that evolves through bundles
    cum_profile = current_trait_profile.model_copy(deep=True)
    cum_aspects = [dict(a) for a in current_aspects]
    cum_goals = [dict(g) for g in current_goals]
    cum_subtitle = current_subtitle

    for i, br in enumerate(per_bundle_results):
        br_source_id = str(getattr(br, "source_entity_id", source_entity_id) or source_entity_id)

        target_references = {
            str(item.get("id")): DisplayReference(
                id=str(item.get("id")), type="aspect", name=str(item.get("name") or "Aspect"),
                description=item.get("description"), instance_name=alias,
            )
            for item in cum_aspects if item.get("id")
        } | {
            str(item.get("id")): DisplayReference(
                id=str(item.get("id")), type="goal", name=str(item.get("title") or "Goal"),
                description=item.get("description"), instance_name=alias,
            )
            for item in cum_goals if item.get("id")
        }
        source_group = source_groups_by_id.get(br_source_id, {})
        source_reference = DisplayReference(
            id=br_source_id, type="source_group",
            name=str(source_group.get("source_alias") or getattr(br, "source_entity_alias", None) or br_source_id),
            description=source_group.get("description") or "Source scenes used for this identity update.",
            instance_name=alias,
        )

        cum_profile = br.trait_profile.model_copy(deep=True)
        _apply_aspect_ops(
            cum_aspects, br.aspect_updates,
            focused_ids=br.focused_aspects,
        )
        _apply_goal_ops(
            cum_goals, br.goal_updates,
            focused_ids=br.focused_goals,
        )

        br_sub = getattr(br, "subtitle_change", None)
        if br_sub:
            if br_sub.operation == "set":
                cum_subtitle = br_sub.subtitle
            elif br_sub.operation == "clear":
                cum_subtitle = None

        rev_n = CharacterIdentityRevisionProjection(
            revision_number=starting_revision + i + 1,
            source_group_id=br_source_id,
            name=alias,
            subtitle=cum_subtitle,
            trait_profile=cum_profile,
            trait_evidence=br.trait_evidence,
            batch_id=br.batch_id,
            scene_ids=[p.scene_id for p in br.perspectives],
            last_processed_scene_id=br.perspectives[-1].scene_id if br.perspectives else None,
            active_aspects=[map_aspect(a) for a in cum_aspects if a.get("name")],
            active_goals=[map_goal(g) for g in cum_goals if g.get("title")],
        )
        revisions.append(rev_n)


        b_aspects: list[EmbodimentAspectProposal] = []
        for upd in br.aspect_updates:
            if upd.operation.value in ("add", "update", "reinforce", "status"):
                b_aspects.append(EmbodimentAspectProposal(
                    suggestion_id=upd.candidate_id or upd.target_id or _id("aspect", upd.name),
                    name=upd.name,
                    category=upd.category or "identity",
                    description=upd.description,
                    status=upd.status or "active", in_focus=False,
                    justification=upd.justification,
                    evidence_ids=list(upd.evidence_ids),
                ))

        b_goals: list[EmbodimentGoalProposal] = []
        for upd in br.goal_updates:
            if upd.operation.value in ("add", "update", "reinforce", "status"):
                b_goals.append(EmbodimentGoalProposal(
                    suggestion_id=upd.candidate_id or upd.target_id or _id("goal", upd.title),
                    title=upd.title,
                    description=upd.description or upd.title,
                    goal_type=upd.goal_type or "desire",
                    status=upd.status or "active", in_focus=False,
                    justification=upd.justification,
                    evidence_ids=list(upd.evidence_ids),
                ))

        source_projections.append(CharacterSourceProjection(
            source_group_id=br_source_id,
            starting_revision_number=starting_revision + i,
            batch_id=br.batch_id,
            perspectives=[
                ProjectedScenePerspective(
                    id=p.id, scene_id=p.scene_id, scene=scene_reference(p.scene_id),
                    source_type=p.source_type, evidence_ids=p.evidence_ids,
                    evidence=evidence_references(p.evidence_ids),
                    source_digest=br.scene_input_digests.get(p.scene_id),
                    perspective=p.perspective,
                    emotions=p.emotions, beliefs=p.beliefs,
                )
                for p in br.perspectives
            ],
            trait_changes=[ProjectedTraitChange(
                **change.model_dump(mode="json"),
                evidence=evidence_references(change.evidence_ids),
            ) for change in br.trait_changes],
            source_group=source_reference,
            aspects=b_aspects,
            goals=b_goals,
            aspect_operations=list(br.aspect_updates),
            goal_operations=list(br.goal_updates),
            focused_aspects=list(br.focused_aspects),
            focused_goals=list(br.focused_goals),
            completed_goal_titles=[
                upd.title for upd in br.goal_updates
                if upd.status is not None and upd.status.value == "completed"
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
