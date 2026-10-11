"""Evidence-grounded identity context and offline extraction diagnostics.

The diagnostic CLI reads an administrator-exported JSON ledger without network
access: python -m app.services.character_trait_analysis INPUT.json OUTPUT.json.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.schemas.character_traits import DIRECTIONAL_TRAITS, TraitEvidence, TraitProfile
from app.services.character_trait_service import (
    directional_items, merge_evidence, steadiness_estimate, trait_history_details,
)

SYNTHESIS_VERSION = 'evidence-synthesis-v2-behavioral-context'
EXTRACTION_VERSION = 'diagnostic-observations-v1'
EXAMPLE_LIMIT = 4
EXAMPLE_CHARS = 600
PERSPECTIVE_LIMIT = 6


def bounded_previous_identity(value) -> dict | None:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    return {
        'identity_summary': str(value.get('identity_summary') or '')[:1800],
        'psychological_summary': str(value.get('psychological_summary') or '')[:1800],
        'personality_traits': [{
            'trait': item['trait'], 'description': str(item.get('description') or '')[:600],
        } for item in (value.get('personality_traits') or [])[:8]
            if isinstance(item, dict) and item.get('trait') in DIRECTIONAL_TRAITS],
    }


def representative_evidence(items: list[TraitEvidence]) -> list[TraitEvidence]:
    """First retain the newest of each pole, then diversify diagnostic situations."""
    ordered = sorted(items, key=lambda item: (item.chronological_position, item.id), reverse=True)
    selected = []
    for pole in ('high', 'low'):
        match = next((item for item in ordered if item.polarity == pole), None)
        if match:
            selected.append(match)
    for item in ordered:
        if len(selected) >= EXAMPLE_LIMIT:
            break
        if item not in selected and item.situation_type not in {prior.situation_type for prior in selected}:
            selected.append(item)
    for item in ordered:
        if len(selected) >= EXAMPLE_LIMIT:
            break
        if item not in selected:
            selected.append(item)
    return selected


def synthesis_traits(profile: TraitProfile, evidence: list[TraitEvidence]) -> list[dict]:
    details = trait_history_details(profile, evidence)
    result = []
    for key in DIRECTIONAL_TRAITS:
        estimate, detail = profile.estimate(key), details[key]
        ids = set(estimate.observation_ids)
        items = directional_items(key, [item for item in evidence if item.perspective_id in ids]) \
            if detail.availability == 'available' else []
        result.append({
            'name': key, 'value': estimate.point, 'status': estimate.status,
            **detail.model_dump(mode='json'),
            'evidence_examples': [{
                'id': item.id, 'perspective_id': item.perspective_id,
                'evidence_ids': item.evidence_ids, 'polarity': item.polarity,
                'situation_type': item.situation_type,
                'justification': item.justification[:EXAMPLE_CHARS],
            } for item in representative_evidence(items)],
        })
    return result


def synthesis_perspectives(perspectives: list) -> list[dict]:
    """Recent validated subjective context; never independent behavioral evidence."""
    return [{
        'scene_id': item.scene_id, 'perspective_id': item.id,
        'source_type': item.source_type.value, 'perspective': item.perspective[:900],
    } for item in perspectives[-PERSPECTIVE_LIMIT:]]


def synthesis_steadiness(profile: TraitProfile, evidence: list[TraitEvidence]) -> dict:
    """Separate effective consistency from reproducible contributing inference.

    Only recorded contributing perspectives may establish availability. Reuse the
    scoring owner so manual values never masquerade as inferred consistency.
    """
    effective = profile.steadiness
    inferred = profile.inferred_traits.get('steadiness') if effective.status == 'manual' else effective
    result = {
        'value': effective.point, 'status': effective.status,
        'observation_count': effective.observation_count,
        'availability': 'unavailable', 'reason': None, 'inferred_value': None,
    }
    if inferred is None or inferred.point is None:
        result['reason'] = 'No inferred consistency estimate from comparable behavior.'
        return result
    if effective.observation_ids != inferred.observation_ids:
        result['reason'] = 'Effective and inferred contributing perspectives disagree.'
        return result
    expected = set(inferred.observation_ids)
    items = [item for item in evidence if item.evidence_kind == 'behavior'
             and item.perspective_id in expected]
    if {item.perspective_id for item in items} != expected:
        result['reason'] = 'Contributing evidence is missing.'
        return result
    try:
        for key in DIRECTIONAL_TRAITS:
            directional_items(key, items)
        reproduced = steadiness_estimate(items)
    except ValueError:
        result['reason'] = 'Contributing evidence is ambiguous.'
        return result
    if reproduced != inferred:
        result['reason'] = 'Evidence does not reproduce the recorded consistency estimate.'
        return result
    result.update(availability='available', inferred_value=inferred.point)
    return result


def diagnostic_report(characters: list[dict]) -> dict:
    """Compare ledger attribution without treating absent observations as processing."""
    summaries, scenes = [], defaultdict(dict)
    totals = {key: {'high': 0, 'low': 0} for key in DIRECTIONAL_TRAITS}
    seen_characters = set()
    for character in characters:
        character_id = character['character_id']
        if character_id in seen_characters:
            raise ValueError('Each character must occur once in a diagnostic input.')
        seen_characters.add(character_id)
        raw = [TraitEvidence.model_validate(item) for item in character.get('evidence', [])]
        evidence = merge_evidence([], raw)
        behavior = [item for item in evidence if item.evidence_kind == 'behavior']
        # Also reject conflicting trait evidence bound to the same perspective.
        for key in DIRECTIONAL_TRAITS:
            directional_items(key, behavior)
        counts = {key: {'high': 0, 'low': 0} for key in DIRECTIONAL_TRAITS}
        known_processed = character.get('processed_scene_ids')
        processed = set(known_processed or [])
        observed_scenes = set()
        for scene_id in processed:
            scenes[scene_id][character_id] = {'processing': 'processed', 'observations': []}
        for item in behavior:
            counts[item.trait][item.polarity] += 1
            totals[item.trait][item.polarity] += 1
            for reference in item.evidence_ids:
                if not reference.startswith('scene:'):
                    continue
                scene_id = reference.removeprefix('scene:')
                observed_scenes.add(scene_id)
                row = scenes[scene_id].setdefault(character_id, {'processing': 'observed', 'observations': []})
                row['observations'].append({
                    'id': item.id, 'perspective_id': item.perspective_id,
                    'trait': item.trait, 'polarity': item.polarity,
                    'justification': item.justification,
                })
        summaries.append({
            'character_id': character_id, 'name': character.get('name'),
            'extraction_version': character.get('extraction_version'),
            'observation_count': len(behavior), 'counts_by_trait': counts,
            'authored_disposition_count': len(evidence) - len(behavior),
            'duplicate_record_count': len(raw) - len(evidence),
            'processed_scene_count': len(processed) if known_processed is not None else None,
            'observed_scene_count': len(observed_scenes),
            'scenes_without_observations': len(processed - observed_scenes) if known_processed is not None else None,
            'observations_per_processed_scene': len(behavior) / len(processed)
                if processed and observed_scenes <= processed else None,
        })
    return {
        'report_version': 1, 'counts_by_trait': totals, 'characters': summaries,
        'shared_scenes': [{'scene_id': scene_id, 'characters': [
            {'character_id': character['character_id'], **participants.get(character['character_id'],
                {'processing': 'unknown', 'observations': []})} for character in characters
        ]} for scene_id, participants in sorted(scenes.items()) if len(participants) >= 2],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    args.output.write_text(json.dumps(diagnostic_report(payload['characters']), indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
