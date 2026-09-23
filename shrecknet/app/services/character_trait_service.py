"""Deterministic evidence gates and conservative personality update policy.

Thresholds and the spread-to-sheet conversion are versioned engineering policy,
not calibrated psychometric claims. LLMs propose; this module accepts transitions.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Iterable

from app.schemas.character_traits import (
    ChoiceConditions, DIRECTIONAL_TRAITS, SPEC_VERSION, TraitChange, TraitEdit,
    TraitEstimate, TraitEvidence, TraitObservation, TraitProfile, TraitProposal,
)

POLICY_VERSION = 'evidence-policy-v3-perspective-single-observation'
MIN_CONFIDENCE = 0.7
MIN_DIAGNOSTICITY = 0.7
MIN_EPISODES = 1
MIN_DIRECTIONAL_NEW_EPISODES = 1
MIN_STEADINESS_NEW_EPISODES = 2
MIN_SPREAD_EPISODES = 6
MIN_COMPARISON_GROUPS = 2
MIN_GROUP_EPISODES = 3
UPDATE_MAGNITUDES = {'small': 0.05, 'medium': 0.10, 'large': 0.20}


def validate_scene_grounding(items, scene_ids: list[str]) -> None:
    positions = {f'scene:{scene}': i for i, scene in enumerate(scene_ids)}
    if [item.scene_id for item in items] != scene_ids or len(positions) != len(scene_ids):
        raise ValueError('scene outputs must match the exact unique input order')
    for i, item in enumerate(items):
        if not item.evidence_ids or any(ref not in positions or positions[ref] > i for ref in item.evidence_ids):
            raise ValueError('scene output references missing or future evidence')


def ground_observations(
    observations: Iterable[TraitObservation], *, scene_ids: list[str], source_group_id: str,
    offset: int = 0, authored_evidence_ids: set[str] | None = None,
) -> list[TraitEvidence]:
    positions = {f'scene:{scene}': i for i, scene in enumerate(scene_ids)}
    authored = authored_evidence_ids or set()
    result = []
    for observation in observations:
        refs = set(observation.evidence_ids)
        if observation.evidence_kind == 'authored_disposition':
            if not refs <= authored or observation.episode_id not in authored:
                raise ValueError('authored observation must cite supplied canonical authored evidence')
            if observation.available_after_scene_id is not None:
                raise ValueError('authored baseline cannot claim a scene cutoff')
            position = offset
        else:
            if not refs <= set(positions) or observation.episode_id not in positions:
                raise ValueError('observation references unknown scene evidence or episode')
            if observation.episode_id not in refs:
                raise ValueError('observation must cite its canonical episode')
            latest = max(positions[ref] for ref in refs)
            if observation.available_after_scene_id != scene_ids[latest]:
                raise ValueError('observation availability must equal its latest contributing scene')
            position = offset + latest
        exclusions = []
        if observation.confidence < MIN_CONFIDENCE:
            exclusions.append('insufficient_confidence')
        if observation.diagnosticity < MIN_DIAGNOSTICITY:
            exclusions.append('insufficient_diagnosticity')
        if observation.expression_z is None:
            exclusions.append('expression_unknown')
        if observation.evidence_kind == 'behavior':
            for key in ChoiceConditions.model_fields:
                if getattr(observation.conditions, key).status != 'supported':
                    exclusions.append(f'{key}_not_supported')
        identity = f'{source_group_id}:{observation.episode_id}:{observation.trait}'
        record_id = 'trait:' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        result.append(TraitEvidence(
            **observation.model_dump(), id=record_id, source_group_id=source_group_id,
            chronological_position=position, eligible=not exclusions, exclusions=exclusions,
        ))
    # An extractor cannot inflate support by repeating the same episode/trait.
    if len({item.id for item in result}) != len(result):
        raise ValueError('duplicate trait observations for the same canonical episode')
    return result


def merge_evidence(existing: list[TraitEvidence], incoming: list[TraitEvidence]) -> list[TraitEvidence]:
    by_episode = {(item.episode_id, item.trait): item for item in existing}
    for item in incoming:
        key = (item.episode_id, item.trait)
        prior = by_episode.get(key)
        if prior and prior.model_dump() != item.model_dump():
            raise ValueError('changed previously processed evidence requires regeneration')
        by_episode[key] = item
    return sorted(by_episode.values(), key=lambda item: (item.chronological_position, item.id))


def validate_proposals(proposals: list[TraitProposal], evidence: list[TraitEvidence]) -> None:
    lookup = {item.id: item for item in evidence}
    if len({p.trait for p in proposals}) != len(proposals):
        raise ValueError('trait proposals must be unique')
    for proposal in proposals:
        if any(ref not in lookup or lookup[ref].trait != proposal.trait or not lookup[ref].eligible
               for ref in proposal.observation_ids):
            raise ValueError('trait proposal must cite eligible observations for that trait')


def _set(profile: TraitProfile, key: str, estimate: TraitEstimate):
    if key == 'steadiness':
        profile.steadiness = estimate
    else:
        profile.dispositional_traits[key] = estimate


def apply_manual_edits(profile: TraitProfile, edits: dict[str, TraitEdit]) -> TraitProfile:
    result = profile.model_copy(deep=True)
    for key, edit in edits.items():
        if edit.z is None:
            result.overrides.pop(key, None)
            _set(result, key, result.inferred_traits.pop(key, result.estimate(key)))
        else:
            if key not in result.overrides:
                result.inferred_traits[key] = result.estimate(key).model_copy(deep=True)
            result.overrides[key] = edit
            _set(result, key, TraitEstimate(z=edit.z, status='manual'))
    return result


def _source_delta(items: list[TraitEvidence]) -> float:
    """Average one source's eligible directional evidence for one trait.

    This is deliberately an average, not a sum: a source with many scenes
    cannot manufacture a larger personality jump than a source with one
    decisive eligible choice. Midpoint evidence remains auditable but moves
    neither pole.
    """
    contributions = [
        (1 if item.direction == 'high' else -1 if item.direction == 'low' else 0)
        * UPDATE_MAGNITUDES[item.update_intensity]
        for item in items
    ]
    return round(sum(contributions) / len(contributions), 4) if contributions else 0.0


def _bounded_z(previous: TraitEstimate, delta: float) -> float:
    baseline = previous.z if previous.z is not None else 0.0
    return round(max(-1.9, min(1.9, baseline + delta)), 4)


def _directional(previous: TraitEstimate, key: str, proposal: TraitProposal | None,
                 evidence: list[TraitEvidence], source_group_id: str | None) -> TraitEstimate:
    items = [item for item in evidence if item.trait == key and item.eligible]
    behavioral = [item for item in items if item.evidence_kind == 'behavior']
    source_behavioral = [
        item for item in behavioral
        if source_group_id is None or item.source_group_id == source_group_id
    ]
    result = previous.model_copy(deep=True)
    result.qualifying_count = len(behavioral)
    result.observation_ids = [item.id for item in items]
    directions = {item.direction for item in behavioral}
    opposed = {'low', 'high'} <= directions
    if opposed:
        result.uncertainty = ['Contradictory diagnostic behavior remains in the evidence history.']
        if len(behavioral) < 3 or len({item.direction for item in behavioral[-3:]}) > 1:
            result.status = 'contested'
            return result
    if len(behavioral) < MIN_EPISODES:
        authored = [item for item in items if item.evidence_kind == 'authored_disposition']
        if previous.z is None and authored and not behavioral and proposal:
            result.z = _source_delta(authored)
            result.status = 'provisional'
            result.uncertainty = ['Authored disposition; insufficient independent behavioral evidence.']
        return result
    if (previous.z is not None
            and len(behavioral) - previous.accepted_count < MIN_DIRECTIONAL_NEW_EPISODES):
        return result
    if source_group_id is not None and source_group_id in previous.applied_source_ids:
        return result
    delta = _source_delta(source_behavioral)
    if delta == 0:
        if source_behavioral and {'low', 'high'} <= {item.direction for item in source_behavioral}:
            result.status = 'contested'
            result.uncertainty = ['This source contains opposing diagnostic behavior; no net update was applied.']
        return result
    result.z = _bounded_z(previous, delta)
    result.status = 'supported'
    result.accepted_count = len(behavioral)
    result.comparison_start = max(item.chronological_position for item in behavioral) + 1 if previous.z is not None else 0
    if source_group_id is not None:
        result.applied_source_ids = [*previous.applied_source_ids, source_group_id]
    return result


def _steadiness(profile: TraitProfile, evidence: list[TraitEvidence]) -> TraitEstimate:
    groups = defaultdict(list)
    for item in evidence:
        centre = profile.inferred_traits.get(item.trait, profile.estimate(item.trait))
        if (item.eligible and item.evidence_kind == 'behavior' and item.comparison_context
                and item.chronological_position >= centre.comparison_start):
            key = (item.trait, item.situation_type, ' '.join(item.comparison_context.casefold().split()))
            groups[key].append(item)
    groups = {key: items for key, items in groups.items() if len(items) >= MIN_GROUP_EPISODES}
    items = [item for group in groups.values() for item in group]
    if len(groups) < MIN_COMPARISON_GROUPS or len(items) < MIN_SPREAD_EPISODES:
        return TraitEstimate(uncertainty=['Insufficient repeated comparable behavior.'])
    previous = profile.inferred_traits.get('steadiness', profile.steadiness)
    if previous.z is not None and len(items) - previous.accepted_count < MIN_STEADINESS_NEW_EPISODES:
        return previous.model_copy(deep=True)
    squared = 0.0
    for group in groups.values():
        values = [item.expression_z for item in group if item.expression_z is not None]
        mean = sum(values) / len(values)
        squared += sum((value - mean) ** 2 for value in values)
    spread = math.sqrt(squared / len(items))
    z = round(1.9 - 3.8 * min(spread / 1.9, 1), 4)
    return TraitEstimate(z=z, status='provisional',
        observation_ids=[item.id for item in items], qualifying_count=len(items), accepted_count=len(items),
        uncertainty=['Engineering estimate of within-context spread; not population calibrated.'])


def update_profile(
    profile: TraitProfile,
    evidence: list[TraitEvidence],
    proposals: list[TraitProposal],
    *,
    source_group_id: str | None = None,
) -> tuple[TraitProfile, list[TraitChange]]:
    """Apply at most one bounded update per directional trait/source bundle.

    Numeric change comes exclusively from eligible evidence in ``source_group_id``:
    high/low directions carry the candidate's small/medium/large magnitude and
    same-trait contributions are averaged. LLM proposals provide traceable
    explanations only; they cannot choose a numeric personality value.
    """
    validate_proposals(proposals, evidence)
    result = profile.model_copy(deep=True)
    proposals_by_trait = {p.trait: p for p in proposals}
    for key in DIRECTIONAL_TRAITS:
        previous = result.inferred_traits.get(key, result.estimate(key))
        estimate = _directional(
            previous, key, proposals_by_trait.get(key), evidence, source_group_id,
        )
        if key in result.overrides:
            result.inferred_traits[key] = estimate
        else:
            _set(result, key, estimate)
    spread = _steadiness(result, evidence)
    if 'steadiness' in result.overrides:
        result.inferred_traits['steadiness'] = spread
    else:
        result.steadiness = spread
    changes = []
    lookup = {item.id: item for item in evidence}
    for key in (*DIRECTIONAL_TRAITS, 'steadiness'):
        before, after = profile.estimate(key), result.estimate(key)
        if before != after:
            proposal = proposals_by_trait.get(key)
            changes.append(TraitChange(trait=key, previous=before, current=after,
                justification=proposal.justification if proposal else 'Accumulated evidence and consistency policy.',
                observation_ids=after.observation_ids,
                evidence_ids=sorted({ref for oid in after.observation_ids for ref in lookup[oid].evidence_ids})))
    return result, changes


def scene_digest(scene: dict) -> str:
    material = {key: scene.get(key) for key in ("scene_id", "name", "description", "created_at")}
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def chunk_source_scenes(groups: list[dict]) -> list[dict]:
    """Return exactly one chronological embodiment bundle for every source.

    The historical name is retained for callers, but this deliberately does not
    chunk.  A source's complete scene set is the atomic evidence boundary: one
    analysis pass and one resulting identity revision.  Payload/provider limits
    therefore fail visibly at the LLM boundary instead of silently splitting or
    omitting evidence.
    """
    bundles: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        scenes = sorted(group.get('scenes', []), key=lambda scene: (
            scene.get('created_at') or '', scene['scene_id'],
        ))
        if not scenes:
            continue
        for scene in scenes:
            if scene['scene_id'] in seen:
                raise ValueError('duplicate canonical scene in source groups')
            seen.add(scene['scene_id'])
        source_id = str(group.get('source_id') or '__orphan__')
        bundle = {
            'source_id': source_id,
            'source_alias': group.get('source_alias') or 'Unknown source',
            'scenes': scenes,
        }
        material = json.dumps(bundle, sort_keys=True, ensure_ascii=False)
        bundle['batch_id'] = hashlib.sha256(material.encode()).hexdigest()[:24]
        bundles.append(bundle)
    return sorted(bundles, key=lambda bundle: (
        bundle['scenes'][0].get('created_at') or '', bundle['source_id'],
    ))
