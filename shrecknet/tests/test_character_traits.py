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
    assert state.steadiness.point == 5 and state.steadiness.status == "baseline"
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


@pytest.mark.parametrize('n,expected', [(1, 6), (5, 8), (10, 8), (28, 9)])
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
    assert state.steadiness.point == 5 and state.steadiness.status == "baseline"


def test_cross_scene_steadiness_ignores_legacy_contexts():
    friends = evidence((1, 1, 1), trait='forbearance',
        situation_type='retaliation:friend:ordinary')
    enemies = evidence((-1, -1, -1), trait='forbearance', offset=3,
        situation_type='retaliation:enemy:ordinary')
    state, _ = update_profile(TraitProfile(), friends)
    assert state.steadiness.point == 7 and state.steadiness.status == 'provisional'
    state, _ = update_profile(TraitProfile(), friends + enemies)
    assert state.steadiness.point == 4 and state.steadiness.status == 'provisional'
    assert state.steadiness.observation_count == 6
    assert state.dispositional_traits['forbearance'].point in (4, 5, 6)
    mixed = evidence((1, -1, 1, -1, 1, -1), trait='forbearance')
    state, _ = update_profile(TraitProfile(), mixed)
    assert state.steadiness.point < 5


def test_legacy_steadiness_full_scale_remains_reproducible():
    from app.services.character_trait_service import legacy_steadiness_estimate
    contexts = ('retaliation:friend:ordinary', 'retaliation:enemy:ordinary',
                'retaliation:other:ordinary')
    consistently_low = [item for index, context in enumerate(contexts)
        for item in evidence((-1,) * 12, trait='forbearance', offset=12 * index,
                             situation_type=context)]
    assert legacy_steadiness_estimate(consistently_low).point == 9
    balanced = (0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1)
    varied = [item for index, context in enumerate(contexts)
        for item in evidence(tuple(1 if value else -1 for value in balanced),
                             trait='forbearance', offset=12 * index,
                             situation_type=context)]
    assert legacy_steadiness_estimate(varied).point == 1


def test_authored_description_does_not_seed_points_and_manual_override():
    baseline = TraitObservation(trait='integrity', evidence_kind='authored_disposition',
        polarity='high', situation_type='exploitation', justification='Explicit authored disposition.',
        evidence_ids=['identity:e'])
    prior = ground_observations([baseline], scene_ids=[], source_group_id='e',
        authored_evidence_ids={'identity:e'})
    state, _ = update_profile(TraitProfile(), prior)
    assert state.estimate('integrity').point == 5
    assert state.estimate('integrity').status == 'baseline'
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


def test_weighted_sources_and_weak_only_support():
    from app.services.character_trait_service import directional_details
    strong = evidence((1,))
    weak = [item.model_copy(update={'source': 'perspective'}) for item in evidence((1, 1, 1))]
    assert directional_details('integrity', strong).inferred_continuous_value == pytest.approx(6.0)
    assert directional_details('integrity', weak[:1]).inferred_continuous_value == pytest.approx(5 + 4/7)
    assert point(strong).observation_count == 1 and point(strong).status == 'provisional'
    assert point(weak[:2]).status == 'provisional'
    assert point(weak).status == 'supported'
    mixed = [weak[0], weak[1].model_copy(update={'source': 'canonical', 'polarity': 'low'})]
    expected_mean = (0.9 - 2)/(0.9 + 2)
    assert directional_details('integrity', mixed).inferred_continuous_value == pytest.approx(5 + 4*expected_mean*3/9)
    assert point(mixed).status == 'supported' and point(mixed).observation_count == 2


def test_event_groups_deduplicate_retellings_and_use_strongest_evidence():
    from app.services.character_trait_service import directional_items, steadiness_estimate
    items = evidence((1, 1, 1))
    items[0].observation_group_id = items[1].observation_group_id = 'same-event'
    items[0].source = 'perspective'
    items[1].polarity = 'low'  # canonical takes precedence over the weak retelling
    chosen = directional_items('integrity', items)
    assert len(chosen) == 2 and chosen[0].polarity == 'low'
    assert chosen[0].chronological_position == 0
    assert point(items).observation_count == 2
    assert steadiness_estimate(items).observation_count == 2
    items[0].source = 'canonical'  # conflicting equal-strength retellings are excluded
    assert point(items).observation_count == 1
    assert steadiness_estimate(items).status == 'baseline'
    items[0].polarity = 'low'
    items[1].independence_status = 'unresolved'
    assert point(items).observation_count == 1
    assert steadiness_estimate(items).status == 'baseline'


def test_cross_trait_repeat_caps_support_and_polarities():
    from app.services.character_trait_service import steadiness_estimate
    caution = evidence((1,) * 20, trait='caution')
    result = steadiness_estimate(caution)
    assert result.point == 7 and result.status == 'provisional'
    assert result.observation_count == 12  # latest independent observations only
    singleton = evidence((-1,), trait='integrity', offset=20)
    assert steadiness_estimate(caution + singleton) == result
    repeated = evidence((-1,) * 3, trait='integrity', offset=20)
    combined = steadiness_estimate(caution + repeated)
    assert combined.point == 8 and combined.status == 'supported'
    # Low Integrity agrees with itself; it is never compared against high Caution.
    same_events = evidence((-1,) * 3, trait='integrity')
    assert steadiness_estimate(evidence((1,) * 3, trait='caution') + same_events).status == 'provisional'
    all_traits = [item for i, trait in enumerate(DIRECTIONAL_TRAITS)
                  for item in evidence((1,) * 3, trait=trait, offset=i*3)]
    assert steadiness_estimate(all_traits).point == 9


def test_steadiness_recency_weights_and_baseline_override():
    from app.services.character_trait_service import steadiness_estimate
    baseline = TraitProfile()
    assert baseline.steadiness.status == 'baseline' and baseline.steadiness.point == 5
    manual = apply_manual_edits(baseline, {'steadiness': TraitEdit(point=9, reason='Authored')})
    assert manual.inferred_traits['steadiness'] == baseline.steadiness
    cleared = apply_manual_edits(manual, {'steadiness': TraitEdit(point=None, reason='Resume')})
    assert cleared.steadiness == baseline.steadiness
    items = evidence((1, -1), trait='caution')
    items[0].source = 'perspective'
    # H=.9, L=2, repeat mass=2; D=1.8/2.9, then shrink around 5.
    from app.services.character_trait_service import _rounded
    expected = _rounded(5 + 4*(1-2*(1.8/2.9))*2/6)
    assert steadiness_estimate(items).point == expected
    inferred_five = steadiness_estimate([item.model_copy(update={'source': 'perspective'}) for item in items])
    assert inferred_five.point == 4  # recency plus repeat-mass smoothing retains pole disagreement
    # An inferred rounded 5 remains distinct from an unevaluated baseline.
    mixed = evidence((1, 1, 1, -1), trait='caution')
    result = steadiness_estimate(mixed)
    assert result.point == 5 and result.status == 'provisional'


def test_legacy_revision_math_and_steadiness_are_versioned():
    from app.schemas.character_traits import LEGACY_SPEC_VERSION, SPEC_VERSION
    from app.services.character_trait_service import (
        directional_details, legacy_steadiness_estimate, trait_history_details,
    )
    items = evidence((1,) * 5)
    legacy = TraitProfile(version=LEGACY_SPEC_VERSION)
    legacy.dispositional_traits['integrity'] = TraitEstimate(point=7, status='supported',
        observation_count=5, observation_ids=[item.perspective_id for item in items])
    legacy.steadiness = legacy_steadiness_estimate(items)
    details = trait_history_details(legacy, items)['integrity']
    assert details.inferred_continuous_value == pytest.approx(5 + 4*5/9)
    assert details.calculation_version == 'recency-points-v1'
    updated, _ = update_profile(legacy, items)
    assert legacy.version == LEGACY_SPEC_VERSION and updated.version == SPEC_VERSION
    assert updated.estimate('integrity').point == 8
    assert directional_details('integrity', items).calculation_version == 'recency-weighted-scenes-v3-neutral-prior'
    assert TraitProfile.model_validate({'version': LEGACY_SPEC_VERSION}).steadiness.status == 'unknown'


def test_directional_baseline_is_versioned():
    with pytest.raises(ValidationError):
        TraitEstimate(point=4, status='baseline')
    from app.schemas.character_traits import WEIGHTED_SPEC_VERSION
    assert TraitProfile().estimate('integrity').status == 'baseline'
    profile = TraitProfile(version=WEIGHTED_SPEC_VERSION).model_dump()
    profile['dispositional_traits']['integrity'] = {'point': 5, 'status': 'baseline'}
    with pytest.raises(ValidationError):
        TraitProfile.model_validate(profile)


def test_weighted_ledger_requires_source_but_legacy_rows_default_to_canonical():
    from app.schemas.character_traits import TraitEvidence
    record = evidence((1,))[0].model_dump()
    record.pop('source')
    with pytest.raises(ValidationError, match='explicit source'):
        TraitEvidence.model_validate(record)
    record['policy_version'] = 'point-evidence-v1'
    assert TraitEvidence.model_validate(record).source == 'canonical'
