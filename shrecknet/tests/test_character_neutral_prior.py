"""Neutral priors are computational values, never observations or probabilities."""
import json

import pytest
from pydantic import ValidationError

from app.schemas.character_traits import (
    DIRECTIONAL_TRAITS, LEGACY_SPEC_VERSION, WEIGHTED_SPEC_VERSION, SPEC_VERSION,
    TraitEstimate, TraitProfile, TraitEdit,
)
from app.services.character_trait_service import (
    update_profile, apply_manual_edits, directional_details, trait_history_details,
    steadiness_estimate,
)
from app.services.character_trait_analysis import synthesis_traits, decision_personality_context
from app.jobs.character_agent.embody_agent import _IdentityDescriptionOutput, EmbodimentGenerationError
from test_character_traits import evidence
from test_character_embodiment import complete_portrait, FakeLLM, _agent, _canonical


def test_complete_neutral_prior_without_synthetic_evidence():
    profile = TraitProfile()
    assert profile.version == SPEC_VERSION
    for key in (*DIRECTIONAL_TRAITS, 'steadiness'):
        assert profile.estimate(key) == TraitEstimate(point=5, status='baseline')
    recalculated, changes = update_profile(profile, [])
    assert recalculated == profile and changes == []
    for row in synthesis_traits(profile, []):
        assert row['value'] == row['inferred_continuous_value'] == 5
        assert row['status'] == 'baseline' and row['observation_count'] == 0
        assert row['high_count'] == row['low_count'] == 0 and row['evidence_examples'] == []
        assert row['availability'] == 'available' and 'prior' in row['reason']
    context = decision_personality_context(profile, [])
    assert all(row.value == 5 and row.status == 'baseline' for row in context.dispositions.values())
    assert context.steadiness.availability == 'unavailable'


@pytest.mark.parametrize('values,continuous,point', [
    ((), 5, 5), ((1,), 6, 6), ((1, 1), 6.6, 7), ((1, 1, 1), 7, 7),
    ((1, 1, 1, -1), 5.9564242097, 6), ((-1, -1, 1), 4.4760147601, 4),
    ((1,) * 10, 8.0769230769, 8),
])
def test_checked_canonical_examples_and_symmetry(values, continuous, point):
    items = evidence(values)
    profile, _ = update_profile(TraitProfile(), items)
    details = directional_details('integrity', items)
    assert details.inferred_continuous_value == pytest.approx(continuous)
    assert profile.estimate('integrity').point == point
    opposite = directional_details('integrity', evidence(tuple(-v for v in values)))
    assert opposite.inferred_continuous_value + details.inferred_continuous_value == pytest.approx(10)


@pytest.mark.parametrize('version,continuous,point', [
    (LEGACY_SPEC_VERSION, 5.8, 6), (WEIGHTED_SPEC_VERSION, 6.3333333333, 6),
    (SPEC_VERSION, 6, 6),
])
def test_frozen_version_calculation_and_history_replay(version, continuous, point):
    items = evidence((1,))
    profile = TraitProfile(version=version)
    profile.dispositional_traits['integrity'] = TraitEstimate(point=point, status='provisional',
        observation_count=1, observation_ids=[items[0].perspective_id])
    details = trait_history_details(profile, items)['integrity']
    assert details.availability == 'available'
    assert details.inferred_continuous_value == pytest.approx(continuous)
    assert profile.version == version  # Read-only verification must not upgrade.
    empty = TraitProfile(version=version)
    expected = 5 if version == SPEC_VERSION else None
    assert empty.estimate('curiosity').point == expected
    assert trait_history_details(empty, [])['curiosity'].inferred_continuous_value == expected


def test_baseline_supported_center_exclusions_and_manual_clear_remain_distinct():
    # A rounded observed centre remains supported, never baseline.
    observed, _ = update_profile(TraitProfile(), evidence((1, -1)))
    assert observed.estimate('integrity').point == 5
    assert observed.estimate('integrity').status == 'supported'
    excluded = evidence((1,))
    excluded[0].independence_status = 'unresolved'
    baseline, _ = update_profile(TraitProfile(), excluded)
    assert baseline.estimate('integrity').status == 'baseline'
    assert excluded[0].independence_status == 'unresolved'
    manual = apply_manual_edits(baseline, {'integrity': TraitEdit(point=8, reason='Author')})
    manual, _ = update_profile(manual, [])
    assert manual.estimate('integrity').point == 8
    assert manual.inferred_traits['integrity'].status == 'baseline'
    cleared = apply_manual_edits(manual, {'integrity': TraitEdit(point=None, reason='Resume')})
    assert cleared.estimate('integrity').status == 'baseline' and cleared.estimate('integrity').point == 5


def test_steadiness_policy_is_unchanged_across_weighted_versions():
    items = evidence((1, 1, -1)) + evidence((-1, -1, -1), trait='caution', offset=3)
    assert steadiness_estimate(items, version=WEIGHTED_SPEC_VERSION) == steadiness_estimate(items, version=SPEC_VERSION)
    old = TraitProfile(version=WEIGHTED_SPEC_VERSION)
    upgraded, _ = update_profile(old, [])
    assert old.estimate('integrity').point is None
    assert upgraded.version == SPEC_VERSION and upgraded.estimate('integrity').status == 'baseline'


@pytest.mark.parametrize('mutation', ['empty', 'duplicate', 'missing', 'blank'])
def test_generated_portraits_require_each_trait_once_but_readers_allow_legacy(mutation):
    from app.schemas.character_agent import IdentityDescription
    value = complete_portrait()
    if mutation == 'empty':
        value['personality_traits'] = []
    elif mutation == 'duplicate':
        value['personality_traits'][-1] = dict(value['personality_traits'][0])
    elif mutation == 'blank':
        value['personality_traits'][0]['description'] = '   '
    else:
        value['personality_traits'].pop()
    IdentityDescription.model_validate(value)  # Historical public contract.
    with pytest.raises(ValidationError):
        _IdentityDescriptionOutput.model_validate(value)


@pytest.mark.asyncio
async def test_incomplete_generation_retries_with_original_character_context():
    from test_character_agent_decision_making import FakeLLM as Responses
    bad = complete_portrait()
    bad['personality_traits'] = []
    good = complete_portrait('Johnny seeks his maker.', 'No established consistency yet.')
    llm = Responses([json.dumps(bad), json.dumps(good)])
    output = await _agent(llm).generate_identity_description(
        canonical_identity=_canonical({'alias': 'Johnny', 'authored_text': 'Johnny seeks his maker.'}),
        current_profile=TraitProfile(), current_aspects=[], current_goals=[])
    assert output.model_dump() == good
    retry = json.loads(llm.calls[1]['messages'][1]['content'])
    assert retry['original_input']['name'] == 'Johnny'
    assert all(t['status'] == 'baseline' for t in retry['original_input']['traits'])
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_equal_baselines_preserve_different_character_context_in_synthesis():
    captured = []
    for name, text, goal in [('Johnny', 'Johnny seeks his maker.', 'Find my maker'),
                             ('Cwenhild', 'Cwenhild commands the northern guard.', 'Protect the city')]:
        llm = FakeLLM(json.dumps(complete_portrait(text)))
        await _agent(llm).generate_identity_description(canonical_identity=_canonical({'alias': name, 'authored_text': text}),
            current_profile=TraitProfile(), current_aspects=[],
            current_goals=[dict(title=goal, description=goal, status='active')])
        captured.append(json.loads(llm.calls[0]['messages'][1]['content']))
    assert captured[0]['traits'] == captured[1]['traits']
    assert captured[0]['text'] != captured[1]['text'] and captured[0]['goals'] != captured[1]['goals']
    # Semantic character fidelity needs human evaluation; model doubles verify
    # that complete, distinct grounding actually reaches synthesis at equal scores.


def test_equal_baseline_offline_fixtures_use_real_context_without_evidence():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / 'fixtures/neutral_prior_identity_synthesis.json').read_text())
    a, b = fixture['cases']
    assert a['input']['current_profile'] == b['input']['current_profile']
    assert a['input']['canonical_identity'] != b['input']['canonical_identity']
    assert a['input']['current_goals'] != b['input']['current_goals']
    for case in (a, b):
        profile = TraitProfile.model_validate(case['input']['current_profile'])
        assert case['input']['current_trait_evidence'] == []
        assert all(row['status'] == 'baseline' and not row['evidence_examples']
                   for row in synthesis_traits(profile, []))


@pytest.mark.parametrize('version', [LEGACY_SPEC_VERSION, WEIGHTED_SPEC_VERSION])
@pytest.mark.parametrize('field', ['dispositional_traits', 'inferred_traits'])
def test_historical_directional_baseline_is_rejected(version, field):
    value = TraitProfile(version=version).model_dump()
    value[field]['integrity'] = TraitEstimate(point=5, status='baseline').model_dump()
    with pytest.raises(ValidationError, match='only valid for Steadiness'):
        TraitProfile.model_validate(value)
