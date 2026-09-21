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
    point_to_z,
)

POLICY_VERSION = 'evidence-policy-v1'
MIN_CONFIDENCE = 0.7
MIN_DIAGNOSTICITY = 0.7
MIN_EPISODES = 3
MIN_NEW_EPISODES = 2
MIN_SPREAD_EPISODES = 6
MIN_COMPARISON_GROUPS = 2
MIN_GROUP_EPISODES = 3


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
        if observation.expression_point is None:
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
        side = 'low' if proposal.point < 5 else 'high' if proposal.point > 5 else 'midpoint'
        if not any(lookup[ref].direction == side for ref in proposal.observation_ids):
            # Polarized evidence is retained as contested, never accepted as a midpoint.
            if side != 'midpoint':
                raise ValueError('trait proposal contradicts the pole of its cited evidence')


def _set(profile: TraitProfile, key: str, estimate: TraitEstimate):
    if key == 'steadiness':
        profile.steadiness = estimate
    else:
        profile.dispositional_traits[key] = estimate


def apply_manual_edits(profile: TraitProfile, edits: dict[str, TraitEdit]) -> TraitProfile:
    result = profile.model_copy(deep=True)
    for key, edit in edits.items():
        if edit.point is None:
            result.overrides.pop(key, None)
            _set(result, key, result.inferred_traits.pop(key, result.estimate(key)))
        else:
            if key not in result.overrides:
                result.inferred_traits[key] = result.estimate(key).model_copy(deep=True)
            result.overrides[key] = edit
            _set(result, key, TraitEstimate(z=point_to_z(edit.point), status='manual'))
    return result


def _bounded_point(previous: TraitEstimate, point: int) -> int:
    if previous.point is None:
        return point
    return max(previous.point - 1, min(previous.point + 1, point))


def _directional(previous: TraitEstimate, key: str, proposal: TraitProposal | None,
                 evidence: list[TraitEvidence]) -> TraitEstimate:
    items = [item for item in evidence if item.trait == key and item.eligible]
    behavioral = [item for item in items if item.evidence_kind == 'behavior']
    result = previous.model_copy(deep=True)
    result.qualifying_count = len(behavioral)
    result.observation_ids = [item.id for item in items]
    directions = {item.direction for item in behavioral}
    opposed = {'low', 'high'} <= directions
    if not proposal:
        if opposed:
            result.status = 'contested'
            result.uncertainty = ['Contradictory diagnostic behavior remains in the evidence history.']
        return result
    proposed_side = 'low' if proposal.point < 5 else 'high' if proposal.point > 5 else 'midpoint'
    if opposed:
        result.uncertainty = ['Contradictory diagnostic behavior remains in the evidence history.']
        if len(behavioral) < 3 or any(item.direction != proposed_side for item in behavioral[-3:]):
            result.status = 'contested'
            return result
    if proposal.point == 5 and not any(item.direction == 'midpoint' for item in items):
        result.uncertainty = ['A midpoint requires intermediate behavior, not an average of extremes.']
        return result
    if len(behavioral) < MIN_EPISODES:
        authored = [item for item in items if item.evidence_kind == 'authored_disposition']
        if previous.z is None and authored and not behavioral:
            result.z = point_to_z(proposal.point)
            result.status = 'provisional'
            result.uncertainty = ['Authored disposition; insufficient independent behavioral evidence.']
        return result
    if key == 'restlessness' and len({item.source_group_id for item in behavioral}) < 2:
        result.uncertainty = ['Restlessness requires value choices in at least two source contexts.']
        return result
    if previous.z is not None and len(behavioral) - previous.accepted_count < MIN_NEW_EPISODES:
        return result
    point = _bounded_point(previous, proposal.point)
    result.z = point_to_z(point)
    result.status = 'supported'
    if previous.z != result.z:
        result.accepted_count = len(behavioral)
        result.comparison_start = max(item.chronological_position for item in behavioral) + 1 if previous.z is not None else 0
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
    if previous.z is not None and len(items) - previous.accepted_count < MIN_NEW_EPISODES:
        return previous.model_copy(deep=True)
    squared = 0.0
    for group in groups.values():
        values = [point_to_z(item.expression_point) for item in group]
        mean = sum(values) / len(values)
        squared += sum((value - mean) ** 2 for value in values)
    spread = math.sqrt(squared / len(items))
    point = math.floor(9 - 8 * min(spread / 1.9, 1) + 0.5)
    return TraitEstimate(z=point_to_z(_bounded_point(previous, point)), status='provisional',
        observation_ids=[item.id for item in items], qualifying_count=len(items), accepted_count=len(items),
        uncertainty=['Engineering estimate of within-context spread; not population calibrated.'])


def update_profile(profile: TraitProfile, evidence: list[TraitEvidence], proposals: list[TraitProposal]) -> tuple[TraitProfile, list[TraitChange]]:
    validate_proposals(proposals, evidence)
    result = profile.model_copy(deep=True)
    proposals_by_trait = {p.trait: p for p in proposals}
    for key in DIRECTIONAL_TRAITS:
        previous = result.inferred_traits.get(key, result.estimate(key))
        estimate = _directional(previous, key, proposals_by_trait.get(key), evidence)
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


def chunk_source_scenes(groups: list[dict], *, batch_size: int = 10, max_chars: int = 120_000) -> list[dict]:
    if not 1 <= batch_size <= 10:
        raise ValueError('scene batch size must be between 1 and 10')
    ordered = sorted(((scene, group) for group in groups for scene in group['scenes']),
                     key=lambda pair: (pair[0].get('created_at') or '', pair[0]['scene_id']))
    chunks = []
    seen = set()
    for scene, group in ordered:
        if scene['scene_id'] in seen:
            raise ValueError('duplicate canonical scene in source groups')
        seen.add(scene['scene_id'])
        size = len(json.dumps(scene, ensure_ascii=False))
        if size > max_chars:
            raise ValueError('single scene exceeds embodiment evidence budget')
        source = group.get('source_id') or f"orphan:{scene['scene_id']}"
        if (not chunks or chunks[-1]['source_id'] != source or len(chunks[-1]['scenes']) >= batch_size
                or chunks[-1]['input_chars'] + size > max_chars):
            chunks.append({'source_id': source, 'source_alias': group['source_alias'], 'scenes': [], 'input_chars': 0})
        chunks[-1]['scenes'].append(scene)
        chunks[-1]['input_chars'] += size
    for chunk in chunks:
        material = json.dumps(chunk, sort_keys=True, ensure_ascii=False)
        chunk['batch_id'] = hashlib.sha256(material.encode()).hexdigest()[:24]
    return chunks
