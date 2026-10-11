"""Deterministic point scoring and perspective-grounded trait evidence."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from app.schemas.character_traits import (
    DIRECTIONAL_TRAITS, EVIDENCE_POLICY_VERSION, SPEC_VERSION, LEGACY_SPEC_VERSION, TraitChange, TraitEdit, TraitEstimate,
    TraitEvidence, TraitObservation, TraitProfile, TraitHistoryEstimate,
)

POLICY_VERSION = EVIDENCE_POLICY_VERSION
RECENCY = 0.9
SMOOTHING = 4
STEADINESS_WINDOW = 12
MIN_COMPARABLE = 3  # Historical v3 policy only.
STEADINESS_REPEAT_CAP = 4
MIN_STEADINESS_TRAITS = 2
MIN_STEADINESS_SERIES = 3
MIN_STEADINESS_EVENTS = 6


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


def legacy_directional_items(key: str, evidence: list[TraitEvidence]) -> list[TraitEvidence]:
    items = [item for item in evidence if item.trait == key and item.evidence_kind == 'behavior']
    items.sort(key=lambda item: (item.chronological_position, item.id))
    if len({item.perspective_id for item in items}) != len(items):
        raise ValueError('duplicate perspective evidence for a trait')
    return items


def legacy_directional_details(key: str, evidence: list[TraitEvidence]) -> TraitHistoryEstimate:
    """The shared continuous calculation used by consolidation and read models."""
    items = legacy_directional_items(key, evidence)
    if not items:
        return TraitHistoryEstimate(availability='available', high_count=0,
                                   low_count=0, observation_count=0)
    n = len(items)
    weights = [RECENCY ** (n - index - 1) for index in range(n)]
    weighted = sum(weight * (1 if item.polarity == 'high' else -1)
                   for weight, item in zip(weights, items, strict=True))
    total = sum(weights)
    mean = weighted / total
    high = sum(item.polarity == 'high' for item in items)
    return TraitHistoryEstimate(inferred_continuous_value=5 + 4 * mean * n / (n + SMOOTHING),
        availability='available', high_count=high, low_count=n - high, observation_count=n)


def observation_group(item: TraitEvidence) -> str:
    """Backend event equivalence; legacy rows fall back to their canonical scene."""
    return item.observation_group_id or next(
        (ref for ref in item.evidence_ids if ref.startswith('scene:')), item.perspective_id or item.id)


def evidence_weight(item: TraitEvidence) -> int:
    return 2 if item.source == 'canonical' else 1


def directional_items(key: str, evidence: list[TraitEvidence]) -> list[TraitEvidence]:
    """Resolve strongest evidence once per independent event and trait.

    Unresolved attribution and equally strong conflicting retellings are excluded.
    Group recency uses its earliest recorded position, never the latest retelling.
    """
    groups = defaultdict(list)
    positions = {}
    for item in evidence:
        if item.evidence_kind == 'behavior':
            group = observation_group(item)
            positions[group] = min(positions.get(group, item.chronological_position), item.chronological_position)
    for item in evidence:
        if item.trait == key and item.evidence_kind == 'behavior':
            groups[observation_group(item)].append(item)
    result = []
    for items in groups.values():
        if any(item.independence_status == 'unresolved' for item in items):
            continue
        strongest = max(evidence_weight(item) for item in items)
        candidates = [item for item in items if evidence_weight(item) == strongest]
        if len({item.polarity for item in candidates}) != 1:
            continue
        chosen = min(candidates, key=lambda item: (item.chronological_position, item.id))
        result.append(chosen.model_copy(update={'chronological_position': positions[observation_group(chosen)]}))
    return sorted(result, key=lambda item: (item.chronological_position, item.id))


def directional_details(key: str, evidence: list[TraitEvidence], *, version: str = SPEC_VERSION) -> TraitHistoryEstimate:
    if version == LEGACY_SPEC_VERSION:
        return legacy_directional_details(key, evidence)
    items = directional_items(key, evidence)
    calculation = 'recency-weighted-scenes-v2'
    if not items:
        return TraitHistoryEstimate(availability='available', high_count=0,
            low_count=0, observation_count=0, calculation_version=calculation)
    n = len(items)
    weights = [RECENCY ** (n - i - 1) * evidence_weight(item) for i, item in enumerate(items)]
    mean = sum(weight * (1 if item.polarity == 'high' else -1)
               for weight, item in zip(weights, items, strict=True)) / sum(weights)
    mass = sum(evidence_weight(item) for item in items)
    high = sum(item.polarity == 'high' for item in items)
    return TraitHistoryEstimate(inferred_continuous_value=5 + 4 * mean * mass / (mass + SMOOTHING),
        availability='available', high_count=high, low_count=n-high, observation_count=n,
        calculation_version=calculation)


def _directional(key: str, evidence: list[TraitEvidence]) -> TraitEstimate:
    details = directional_details(key, evidence)
    if details.inferred_continuous_value is None:
        return TraitEstimate()
    items = directional_items(key, evidence)
    ids = [item.perspective_id for item in items]
    n = len(items)
    supported = n >= 2 and (any(item.source == 'canonical' for item in items) or n >= 3)
    return TraitEstimate(point=_rounded(details.inferred_continuous_value),
        status='supported' if supported else 'provisional', observation_count=n, observation_ids=ids)


def snapshot_evidence(profile: TraitProfile, evidence: list[TraitEvidence]) -> list[TraitEvidence]:
    """Apply revision-owned equivalence without rewriting historical ledger rows."""
    if profile.version == LEGACY_SPEC_VERSION:
        return evidence
    unresolved = set(profile.unresolved_observation_ids)
    return [item.model_copy(update={
        'observation_group_id': profile.observation_groups.get(item.perspective_id, item.observation_group_id),
        'independence_status': 'unresolved' if item.perspective_id in unresolved else item.independence_status,
        'chronological_position': profile.observation_group_positions.get(
            profile.observation_groups.get(item.perspective_id, observation_group(item)), item.chronological_position),
    }) for item in evidence]


def scene_observation_group(scene: dict) -> str:
    """Explicit event identity, conservative exact-copy grouping, then scene identity.

    The content digest detects exact copies only; paraphrased retellings require
    a reviewed observation_group_id. All comparisons are agent/ontology-local.
    """
    if scene.get('observation_group_id'):
        return scene['observation_group_id']
    material = ' '.join(scene.get('description', '').split())
    return 'content:' + hashlib.sha256(material.encode()).hexdigest() if material else f"scene:{scene['scene_id']}"


def trait_history_details(profile: TraitProfile, evidence: list[TraitEvidence]) -> dict[str, TraitHistoryEstimate]:
    """Resolve only each snapshot's contributing evidence, including under overrides."""
    evidence = snapshot_evidence(profile, evidence)
    calculation = 'recency-points-v1' if profile.version == LEGACY_SPEC_VERSION else 'recency-weighted-scenes-v2'
    result = {}
    for key in DIRECTIONAL_TRAITS:
        effective = profile.estimate(key)
        expected = set(effective.observation_ids)
        items = [item for item in evidence if item.trait == key
                 and item.evidence_kind == 'behavior' and item.perspective_id in expected]
        if {item.perspective_id for item in items} != expected:
            result[key] = TraitHistoryEstimate(calculation_version=calculation, reason='Contributing evidence is missing.')
            continue
        if profile.version == SPEC_VERSION:
            groups = {observation_group(item) for item in items}
            items = [item for item in evidence if item.trait == key and item.evidence_kind == 'behavior'
                     and observation_group(item) in groups]
            if {item.perspective_id for item in directional_items(key, items)} != expected:
                result[key] = TraitHistoryEstimate(calculation_version=calculation,
                    reason='Contributing evidence is ambiguous.')
                continue
        try:
            details = directional_details(key, items, version=profile.version)
        except ValueError:
            result[key] = TraitHistoryEstimate(calculation_version=calculation, reason='Contributing evidence is ambiguous.')
            continue
        inferred = profile.inferred_traits.get(key) if effective.status == 'manual' else effective
        raw = details.inferred_continuous_value
        if inferred is not None and (inferred.point != (_rounded(raw) if raw is not None else None)
                                     or inferred.observation_count != details.observation_count):
            result[key] = TraitHistoryEstimate(calculation_version=calculation, reason='Evidence does not reproduce the recorded estimate.')
        else:
            result[key] = details
    return result


def legacy_steadiness_estimate(evidence: list[TraitEvidence]) -> TraitEstimate:
    """Estimate within-context consistency from repeated comparable behavior."""
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


def steadiness_estimate(evidence: list[TraitEvidence], *, version: str = SPEC_VERSION) -> TraitEstimate:
    """Capped, evidence-weighted within-trait consistency across independent events."""
    if version == LEGACY_SPEC_VERSION:
        return legacy_steadiness_estimate(evidence)
    series = [directional_items(key, evidence)[-STEADINESS_WINDOW:] for key in DIRECTIONAL_TRAITS]
    series = [items for items in series if len(items) >= 2]
    if not series:
        return TraitEstimate(point=5, status='baseline')
    mass, disagreement = 0.0, 0.0
    included = {}
    for items in series:
        n = len(items)
        high = sum(RECENCY ** (n-i-1) * evidence_weight(item)
                   for i, item in enumerate(items) if item.polarity == 'high')
        low = sum(RECENCY ** (n-i-1) * evidence_weight(item)
                  for i, item in enumerate(items) if item.polarity == 'low')
        support = min(STEADINESS_REPEAT_CAP, sum(evidence_weight(item) for item in items[1:]))
        mass += support
        disagreement += support * 2 * min(high, low) / (high + low)
        for item in items:
            group = observation_group(item)
            prior = included.get(group)
            if prior is None or (item.chronological_position, item.id) < (prior.chronological_position, prior.id):
                included[group] = item
    ids = [item.perspective_id for item in sorted(included.values(), key=lambda item: (item.chronological_position, item.id))]
    supported = (sum(len(items) >= MIN_STEADINESS_SERIES for items in series) >= MIN_STEADINESS_TRAITS
                 and len(included) >= MIN_STEADINESS_EVENTS)
    value = 5 + 4 * (1 - 2 * disagreement / mass) * mass / (mass + SMOOTHING)
    return TraitEstimate(point=_rounded(value), status='supported' if supported else 'provisional',
                         observation_count=len(ids), observation_ids=ids)


def update_profile(
    profile: TraitProfile, evidence: list[TraitEvidence],
    *, source_group_id: str | None = None,
) -> tuple[TraitProfile, list[TraitChange]]:
    result = profile.model_copy(deep=True)
    result.version = SPEC_VERSION
    result.observation_groups = {item.perspective_id: observation_group(item) for item in evidence if item.perspective_id}
    result.observation_group_positions = {}
    for item in evidence:
        if item.evidence_kind == 'behavior':
            group = observation_group(item)
            result.observation_group_positions[group] = min(
                result.observation_group_positions.get(group, item.chronological_position), item.chronological_position)
    result.unresolved_observation_ids = sorted({item.perspective_id for item in evidence if item.perspective_id and item.independence_status == 'unresolved'})
    evidence = snapshot_evidence(result, evidence)
    for key in DIRECTIONAL_TRAITS:
        estimate = _directional(key, evidence)
        if key in result.overrides:
            result.inferred_traits[key] = estimate
            _set(result, key, TraitEstimate(point=result.overrides[key].point,
                status='manual', observation_count=estimate.observation_count,
                observation_ids=list(estimate.observation_ids)))
        else:
            _set(result, key, estimate)
    spread = steadiness_estimate(evidence)
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
    # Preserve old digests when optional provenance is absent.
    for key in ("observation_group_id", "independence_status"):
        if scene.get(key) is not None and not (key == "independence_status" and scene[key] == "independent"):
            material[key] = scene[key]
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def chunk_source_scenes(groups: list[dict]) -> list[dict]:
    """Return chronological contiguous source runs; scene chunking happens later."""
    scenes = []
    seen: set[str] = set()
    for group in groups:
        for scene in group.get('scenes', []):
            if scene['scene_id'] in seen:
                raise ValueError('duplicate canonical scene in source groups')
            seen.add(scene['scene_id'])
            source_id = group.get('source_id')
            # Unrelated scenes without a DERIVED_FROM source are separate runs.
            run_key = str(source_id) if source_id else f"__orphan__:{scene['scene_id']}"
            scenes.append((scene, run_key, group.get('source_alias') or 'Unknown source'))
    scenes.sort(key=lambda item: (item[0].get('created_at') or '', item[0]['scene_id']))
    runs: list[dict] = []
    for scene, run_key, alias in scenes:
        if not runs or runs[-1]['source_id'] != run_key:
            runs.append({'source_id': run_key, 'source_alias': alias, 'scenes': []})
        runs[-1]['scenes'].append(scene)
    bundles: list[dict] = []
    for run_index, run in enumerate(runs):
        bundle = {
            **run,
        }
        material = json.dumps({**bundle, 'run_index': run_index}, sort_keys=True, ensure_ascii=False)
        bundle['batch_id'] = hashlib.sha256(material.encode()).hexdigest()[:24]
        bundles.append(bundle)
    return bundles
