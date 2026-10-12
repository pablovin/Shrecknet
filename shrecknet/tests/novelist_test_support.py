"""Explicit evidence/Director fixtures for transport and orchestration regressions."""
from copy import deepcopy


def candidate_payload(legacy):
    value = deepcopy(legacy)
    value['identities'] = [dict(member, status='confirmed') for member in value['cast']]
    for beat in value['beats']:
        beat.update(material='fiction', confirmed_facts=[beat['summary']], unresolved_facts=[])
    return value


def directed_payload(candidate, groups=None):
    by_id = {beat['beat_id']: beat for beat in candidate['beats']}
    groups = groups or [[beat_id] for beat_id in by_id if by_id[beat_id]['material'] != 'gameplay']
    return {
        'title': candidate['title'], 'canonical_cast': candidate['cast'],
        'identity_restrictions': [identity for identity in candidate['identities'] if identity['status'] != 'confirmed'],
        'arcs': [{
            'arc_id': f'arc-{i:03d}', 'outline': f'Narrative development {i}.',
            'importance': by_id[ids[0]]['importance'], 'beat_ids': ids,
            'confirmed_facts': [{'beat_id': beat_id, 'fact_index': j} for beat_id in ids for j in range(len(by_id[beat_id]['confirmed_facts']))],
            'unresolved_facts': [{'beat_id': beat_id, 'fact_index': j} for beat_id in ids for j in range(len(by_id[beat_id]['unresolved_facts']))],
        } for i, ids in enumerate(groups, 1)],
        'excluded_beat_ids': [beat_id for beat_id in by_id if by_id[beat_id]['material'] == 'gameplay'],
    }
