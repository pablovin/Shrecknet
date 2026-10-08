"""Point trait contracts, perspective provenance, recency and consistency."""
import json
import pytest
from pydantic import ValidationError
from app.schemas.character_traits import (
    DIRECTIONAL_TRAITS, TRAIT_DEFINITIONS, TRAIT_BY_KEY, TraitEdit, TraitEstimate,
    TraitObservation, TraitProfile,
)
from app.services.character_trait_service import (
    apply_manual_edits, chunk_source_scenes, ground_observations, merge_evidence,
    update_profile,
)


def observation(scene='s1', trait='integrity', *,
                polarity='high', situation_type=None, perspective_id=None, **changes):
    data = dict(trait=trait,
        situation_type=situation_type or f'{TRAIT_BY_KEY[trait].diagnostic_situations[0]}:other:ordinary',
        polarity=polarity,
        justification='A meaningful voluntary choice reveals this trait.',
        perspective_id=perspective_id or f'perspective:{scene}',
        evidence_ids=[f'scene:{scene}'])
    data.update(changes)
    return TraitObservation(**data)


def evidence(values=(1, 1, 1), trait='integrity', source='source', offset=0,
             situation_type=None):
    scenes = [f's{offset+i}' for i in range(len(values))]
    items = [observation(scene, trait, polarity='high' if value > 0 else 'low',
                         situation_type=situation_type)
             for scene, value in zip(scenes, values)]
    return ground_observations(items, scene_ids=scenes,
        perspective_ids=[f'perspective:{scene}' for scene in scenes],
        source_group_id=source, offset=offset)


def point(items, trait='integrity'):
    state, _ = update_profile(TraitProfile(), items)
    return state.estimate(trait)


def test_registry_and_point_contract():
    assert len(TRAIT_DEFINITIONS) == 9 and len(DIRECTIONAL_TRAITS) == 8
    assert 'steadiness' not in DIRECTIONAL_TRAITS
    state = TraitProfile()
    assert state.steadiness.point is None
    assert 'z' not in json.loads(state.storage_json())['steadiness']
    assert TraitProfile.model_validate_json(state.storage_json()) == state
    with pytest.raises(ValidationError):
        TraitEstimate(point=True, status='supported')
    with pytest.raises(ValidationError):
        TraitEstimate(point=10, status='supported')
    with pytest.raises(ValidationError):
        TraitEstimate(point=5, status='unknown')
    with pytest.raises(ValidationError):
        TraitEstimate(point=5, status='supported', observation_count=1)
    with pytest.raises(ValidationError):
        TraitProfile.model_validate({**state.model_dump(), 'version': 'dispositions-v2-bipolar-evidence'})
    item = evidence((1,))[0].model_dump()
    item['policy_version'] = 'point-evidence-v0'
    from app.schemas.character_traits import TraitEvidence
    with pytest.raises(ValidationError):
        TraitEvidence.model_validate(item)


@pytest.mark.parametrize('n,expected', [(1, 6), (5, 7), (10, 8), (28, 9)])
def test_same_pole_reaches_full_high_scale(n, expected):
    estimate = point(evidence((1,) * n))
    assert estimate.point == expected
    assert estimate.observation_count == n
    assert estimate.observation_ids == [f'perspective:s{i}' for i in range(n)]


def test_full_low_scale_and_complete_reversal():
    assert point(evidence((-1,) * 28)).point == 1
    first = point(evidence((1,) * 30))
    reversed_estimate = point(evidence((1,) * 30 + (-1,) * 30))
    assert first.point == 9
    assert reversed_estimate.point <= 2
    assert point(evidence((1,) * 5 + (-1,) * 5)).point == 4


def test_perspective_binding_and_replay():
    items = evidence((1, -1, 1))
    assert items[0].perspective_id == 'perspective:s0'
    assert items[0].evidence_ids == ['scene:s0']
    assert len(merge_evidence(items, items)) == 3
    state, _ = update_profile(TraitProfile(), items)
    replay, changes = update_profile(state, items)
    assert replay == state and changes == []
    with pytest.raises(ValueError):
        ground_observations([observation()], scene_ids=['s1'],
                            perspective_ids=['another-perspective'], source_group_id='source')
    with pytest.raises(ValueError):
        ground_observations([observation(), observation()], scene_ids=['s1'],
                            perspective_ids=['perspective:s1'], source_group_id='source')


def test_context_validation_and_unspecified_directional_support():
    with pytest.raises(ValidationError):
        observation(situation_type='resource_allocation:other:ordinary')
    with pytest.raises(ValidationError):
        observation(situation_type='exploitation:friend:unknown')
    item = evidence((1,), situation_type='unspecified')
    state, _ = update_profile(TraitProfile(), item)
    assert state.dispositional_traits['integrity'].point == 6
    assert state.steadiness.point is None


def test_contextual_steadiness_and_small_sample_smoothing():
    friends = evidence((1, 1, 1), trait='forbearance',
        situation_type='retaliation:friend:ordinary')
    enemies = evidence((-1, -1, -1), trait='forbearance', offset=3,
        situation_type='retaliation:enemy:ordinary')
    state, _ = update_profile(TraitProfile(), friends)
    assert state.steadiness.point == 7 and state.steadiness.status == 'provisional'
    state, _ = update_profile(TraitProfile(), friends + enemies)
    assert state.steadiness.point == 7 and state.steadiness.status == 'supported'
    assert state.steadiness.observation_count == 6
    assert state.dispositional_traits['forbearance'].point in (4, 5, 6)
    mixed = evidence((1, -1, 1, -1, 1, -1), trait='forbearance')
    state, _ = update_profile(TraitProfile(), mixed)
    assert state.steadiness.point < 5


def test_steadiness_full_scale_remains_accessible_with_enough_comparable_evidence():
    contexts = ('retaliation:friend:ordinary', 'retaliation:enemy:ordinary',
                'retaliation:other:ordinary')
    consistently_low = [item for index, context in enumerate(contexts)
        for item in evidence((-1,) * 12, trait='forbearance', offset=12 * index,
                             situation_type=context)]
    state, _ = update_profile(TraitProfile(), consistently_low)
    assert state.steadiness.point == 9
    balanced = (0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1)
    varied = [item for index, context in enumerate(contexts)
        for item in evidence(tuple(1 if value else -1 for value in balanced),
                             trait='forbearance', offset=12 * index,
                             situation_type=context)]
    state, _ = update_profile(TraitProfile(), varied)
    assert state.steadiness.point == 1


def test_authored_baseline_and_manual_override():
    baseline = TraitObservation(trait='integrity', evidence_kind='authored_disposition',
        polarity='high', situation_type='exploitation', justification='Explicit authored disposition.',
        evidence_ids=['identity:e'])
    prior = ground_observations([baseline], scene_ids=[], source_group_id='e',
        authored_evidence_ids={'identity:e'})
    state, _ = update_profile(TraitProfile(), prior)
    assert state.estimate('integrity').point == 6
    assert state.estimate('integrity').observation_count == 0
    state = apply_manual_edits(state, {'integrity': TraitEdit(point=2, reason='Author choice')})
    items = evidence((1,) * 10)
    state, _ = update_profile(state, prior + items)
    assert state.estimate('integrity').point == 2
    assert state.estimate('integrity').observation_count == 10
    assert state.inferred_traits['integrity'].point == 8
    state = apply_manual_edits(state, {'integrity': TraitEdit(point=None, reason='Resume inference')})
    assert state.estimate('integrity').point == 8


@pytest.mark.parametrize('count', [0, 1, 9, 10, 11, 21])
def test_source_bundle_contains_every_scene(count):
    scenes = [{'scene_id': f's{i:02}', 'created_at': f'{i:02}',
               'description': 'x', 'name': 'Scene'} for i in range(count)]
    chunks = chunk_source_scenes([{'source_id': 'a', 'source_alias': 'A', 'scenes': scenes}])
    assert [len(c['scenes']) for c in chunks] == ([count] if count else [])
    assert chunks == chunk_source_scenes([{'source_id': 'a', 'source_alias': 'A', 'scenes': scenes}])


def test_definition_documentation_is_generated_from_registry():
    from pathlib import Path
    page = Path(__file__).resolve().parents[2] / 'Documentation/Agents/CharacterAgent/Dispositional Traits.md'
    table = page.read_text().split('<!-- BEGIN GENERATED TRAIT DEFINITIONS -->')[1].split('<!-- END GENERATED TRAIT DEFINITIONS -->')[0]
    for definition in TRAIT_DEFINITIONS:
        for value in (definition.display_name, definition.construct, definition.definition,
                      definition.left_pole, definition.right_pole, definition.boundary_notes):
            assert value in table
