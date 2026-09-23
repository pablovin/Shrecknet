"""Shared chronological timeline and profile lifecycle for drafts and Architect."""
from __future__ import annotations
import re
from typing import Any
from app.schemas.character_traits import TraitProfile, TraitEvidence
from app.schemas.character_agent import (
    CharacterIdentityRevisionProjection, CharacterSourceProjection, CharacterTimelineProjection,
    DisplayReference, EmbodimentAspectProposal, EmbodimentGoalProposal, ProjectedCharacterImpact,
    ProjectedScenePerspective, ProjectedTraitChange, SubtitleChangeProposal,
)

def _stable_profile_id(kind: str, value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return f"{kind}:{normalized or 'unnamed'}"


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
    max_aspects: int | None = None,
    max_goals: int | None = None,
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
            starting_revision_number=starting_revision + i,
            batch_id=br.batch_id,
            perspectives=[
                ProjectedScenePerspective(
                    scene_id=p.scene_id, scene=scene_reference(p.scene_id),
                    source_type=p.source_type, evidence_ids=p.evidence_ids,
                    evidence=evidence_references(p.evidence_ids),
                    source_digest=br.scene_input_digests.get(p.scene_id),
                    awareness_level=p.awareness_level, confidence=p.confidence,
                    summary=p.summary, interpretation=p.interpretation,
                    character_reflection=p.character_reflection,
                    memory_strength=p.memory_strength, importance=p.importance,
                    status=p.status,
                    emotions=p.emotions, beliefs=p.beliefs,
                    impacts=[ProjectedCharacterImpact(
                        **impact.model_dump(mode="json"),
                        target=target_references[impact.target_id],
                    ) for impact in p.impacts],
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


