"""Deterministic point scoring and perspective-grounded trait evidence."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from app.schemas.character_traits import (
    DIRECTIONAL_TRAITS, EVIDENCE_POLICY_VERSION, TraitChange, TraitEdit, TraitEstimate,
    TraitEvidence, TraitObservation, TraitProfile,
)

POLICY_VERSION = EVIDENCE_POLICY_VERSION
RECENCY = 0.9
SMOOTHING = 4
STEADINESS_WINDOW = 12
MIN_COMPARABLE = 3


def validate_scene_grounding(items, scene_ids: list[str]) -> None:
    if [item.scene_id for item in items] != scene_ids or len(set(scene_ids)) != len(scene_ids):
        raise ValueError('scene outputs must match the exact unique input order')
    for item in items:
        if set(item.evidence_ids) != {f'scene:{item.scene_id}'}:
            raise ValueError('scene output must cite only its own scene evidence')


def ground_observations(
    observations: Iterable[TraitObservation], *, scene_ids: list[str], source_group_id: str,
    perspective_ids: list[str] | None = None, offset: int = 0,
    authored_evidence_ids: set[str] | None = None,
) -> list[TraitEvidence]:
    perspective_ids = perspective_ids or []
    if scene_ids and len(scene_ids) != len(perspective_ids):
        raise ValueError('each scene requires one preassigned perspective ID')
    if len(set(perspective_ids)) != len(perspective_ids):
        raise ValueError('perspective IDs must be unique')
    positions = {pid: i for i, pid in enumerate(perspective_ids)}
    authored = authored_evidence_ids or set()
    result = []
    for observation in observations:
        if observation.evidence_kind == 'authored_disposition':
            if not set(observation.evidence_ids) <= authored or not observation.evidence_ids:
                raise ValueError('authored observation must cite supplied identity evidence')
            position = offset
            identity = observation.evidence_ids[0]
        else:
            pid = observation.perspective_id
            if pid not in positions:
                raise ValueError('observation references an unknown perspective')
            scene_id = scene_ids[positions[pid]]
            if observation.evidence_ids != [f'scene:{scene_id}']:
                raise ValueError('perspective observation must cite its own canonical scene')
            position = offset + positions[pid]
            identity = pid
        record_id = 'trait:' + hashlib.sha256(
            f'{source_group_id}:{identity}:{observation.trait}'.encode()
        ).hexdigest()[:24]
        result.append(TraitEvidence(**observation.model_dump(), id=record_id,
            source_group_id=source_group_id, chronological_position=position,
            policy_version=POLICY_VERSION))
    if len({item.id for item in result}) != len(result):
        raise ValueError('duplicate trait observations for the same perspective')
    return result


def merge_evidence(existing: list[TraitEvidence], incoming: list[TraitEvidence]) -> list[TraitEvidence]:
    by_id = {item.id: item for item in existing}
    for item in incoming:
        prior = by_id.get(item.id)
        if prior and prior.model_dump(exclude={'revision_id'}) != item.model_dump(exclude={'revision_id'}):
            raise ValueError('changed previously processed evidence requires regeneration')
        by_id[item.id] = prior if prior and prior.revision_id else item
    return sorted(by_id.values(), key=lambda item: (item.chronological_position, item.id))


def _set(profile: TraitProfile, key: str, estimate: TraitEstimate) -> None:
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
            inferred = result.inferred_traits.get(key, result.estimate(key))
            result.inferred_traits[key] = inferred.model_copy(deep=True)
            result.overrides[key] = edit
            _set(result, key, TraitEstimate(point=edit.point, status='manual',
                observation_count=inferred.observation_count,
                observation_ids=list(inferred.observation_ids)))
    return result


def _rounded(value: float) -> int:
    displacement = Decimal(str(value)) - Decimal(5)
    magnitude = int(abs(displacement).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return max(1, min(9, 5 + (magnitude if displacement >= 0 else -magnitude)))


def _directional(key: str, evidence: list[TraitEvidence]) -> TraitEstimate:
    items = [item for item in evidence if item.trait == key and item.evidence_kind == 'behavior']
    items.sort(key=lambda item: (item.chronological_position, item.id))
    if not items:
        return TraitEstimate()
    n = len(items)
    weights = [RECENCY ** (n - index - 1) for index in range(n)]
    weighted = sum(weight * (1 if item.polarity == 'high' else -1)
                   for weight, item in zip(weights, items, strict=True))
    total = sum(weights)
    mean = weighted / total
    ids = [item.perspective_id for item in items]
    if len(set(ids)) != n:
        raise ValueError('duplicate perspective evidence for a trait')
    return TraitEstimate(point=_rounded(5 + 4 * mean * n / (n + SMOOTHING)),
        status='supported' if n >= 2 else 'provisional',
        observation_count=n, observation_ids=ids)


def _steadiness(evidence: list[TraitEvidence]) -> TraitEstimate:
    groups = defaultdict(list)
    for item in sorted(evidence, key=lambda value: (value.chronological_position, value.id)):
        if item.evidence_kind == 'behavior' and item.situation_type != 'unspecified':
            groups[(item.trait, item.situation_type)].append(item)
    groups = [items[-STEADINESS_WINDOW:] for items in groups.values() if len(items) >= MIN_COMPARABLE]
    groups = [items for items in groups if len(items) >= MIN_COMPARABLE]
    if not groups:
        return TraitEstimate()
    mass, disagreement = 0.0, 0.0
    included = []
    for items in groups:
        n = len(items)
        high = sum(RECENCY ** (n - i - 1) for i, item in enumerate(items) if item.polarity == 'high')
        low = sum(RECENCY ** (n - i - 1) for i, item in enumerate(items) if item.polarity == 'low')
        mass += high + low
        disagreement += 2 * min(high, low)
        included.extend(items)
    ids = [item.perspective_id for item in sorted(included, key=lambda value: (value.chronological_position, value.id))]
    ids = list(dict.fromkeys(ids))
    m = len(ids)
    D = disagreement / mass
    return TraitEstimate(point=_rounded(5 + 4 * (1 - 2 * D) * m / (m + SMOOTHING)),
        status='supported' if m >= 6 and len(groups) >= 2 else 'provisional',
        observation_count=m, observation_ids=ids)


def update_profile(
    profile: TraitProfile, evidence: list[TraitEvidence],
    *, source_group_id: str | None = None,
) -> tuple[TraitProfile, list[TraitChange]]:
    result = profile.model_copy(deep=True)
    for key in DIRECTIONAL_TRAITS:
        estimate = _directional(key, evidence)
        if key in result.overrides:
            result.inferred_traits[key] = estimate
            _set(result, key, TraitEstimate(point=result.overrides[key].point,
                status='manual', observation_count=estimate.observation_count,
                observation_ids=list(estimate.observation_ids)))
        else:
            _set(result, key, estimate)
    spread = _steadiness(evidence)
    if 'steadiness' in result.overrides:
        result.inferred_traits['steadiness'] = spread
        _set(result, 'steadiness', TraitEstimate(point=result.overrides['steadiness'].point,
            status='manual', observation_count=spread.observation_count,
            observation_ids=list(spread.observation_ids)))
    else:
        result.steadiness = spread
    lookup = {item.perspective_id: item for item in evidence if item.perspective_id}
    changes = []
    for key in (*DIRECTIONAL_TRAITS, 'steadiness'):
        before, after = profile.estimate(key), result.estimate(key)
        if before != after:
            changes.append(TraitChange(trait=key, previous=before, current=after,
                justification='Accumulated perspective evidence.',
                observation_ids=list(after.observation_ids),
                evidence_ids=sorted({ref for pid in after.observation_ids
                                     for ref in lookup[pid].evidence_ids})))
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
