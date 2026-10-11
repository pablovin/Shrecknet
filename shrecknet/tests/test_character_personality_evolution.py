"""Continuous revision inference, evidence-bound synthesis, and ledger diagnostics."""
import json
from pathlib import Path

import pytest

from app.schemas.character_traits import DIRECTIONAL_TRAITS, TraitEdit, TraitHistoryEstimate, TraitProfile
from app.services.character_trait_service import (
    _rounded, apply_manual_edits, directional_details, trait_history_details, update_profile,
)
from app.services.character_trait_analysis import (
    bounded_previous_identity, diagnostic_report, representative_evidence, synthesis_traits, synthesis_steadiness, SYNTHESIS_VERSION,
)
from test_character_traits import evidence


@pytest.mark.parametrize('n', [1, 7, 10, 20, 27, 28])
def test_continuous_precision_preserves_integer_and_pole_symmetry(n):
    high, low = evidence((1,) * n), evidence((-1,) * n)
    raw = directional_details('integrity', high)
    opposite = directional_details('integrity', low)
    profile, _ = update_profile(TraitProfile(), high)
    assert raw.inferred_continuous_value == pytest.approx(5 + 4 * (2*n) / (2*n + 4))
    assert raw.inferred_continuous_value + opposite.inferred_continuous_value == pytest.approx(10)
    assert profile.estimate('integrity').point == _rounded(raw.inferred_continuous_value)
    assert raw.high_count == n and raw.low_count == 0
    assert opposite.low_count == n and opposite.high_count == 0
    assert 'inferred_continuous_value' not in profile.storage_json()


def test_plateaus_unknowns_rounding_and_overrides_remain_distinct():
    seven, twentyseven = evidence((1,) * 7), evidence((1,) * 10)
    first, _ = update_profile(TraitProfile(), seven)
    later, _ = update_profile(TraitProfile(), twentyseven)
    assert first.estimate('integrity').point == later.estimate('integrity').point == 8
    assert directional_details('integrity', seven).inferred_continuous_value < directional_details('integrity', twentyseven).inferred_continuous_value
    assert _rounded(8.5) == 9 and _rounded(1.5) == 1
    assert directional_details('curiosity', []).inferred_continuous_value is None
    manual = apply_manual_edits(later, {'integrity': TraitEdit(point=2, reason='Author decision')})
    details = trait_history_details(manual, twentyseven)['integrity']
    assert manual.estimate('integrity').point == 2
    assert details.inferred_continuous_value > 8
    assert details.high_count == 10
    assert trait_history_details(TraitProfile(), [])['integrity'].high_count == 0


def test_snapshot_selection_excludes_later_evidence_and_detects_missing_ledger():
    first = evidence((1,) * 7)
    future = evidence((-1,) * 20, source='future', offset=7)
    profile, _ = update_profile(TraitProfile(), first)
    details = trait_history_details(profile, first + future)['integrity']
    assert details.observation_count == 7 and details.low_count == 0
    missing = trait_history_details(profile, first[:-1])['integrity']
    assert missing.availability == 'unavailable' and missing.inferred_continuous_value is None
    assert missing.high_count is None
    ambiguous = trait_history_details(profile, first + [first[0]])['integrity']
    assert ambiguous.availability == 'available'  # identical replay is idempotent


def test_synthesis_examples_are_bounded_diverse_contrasting_and_deterministic():
    items = evidence((1, -1, 1, 1, 1, 1, 1, 1))
    items[1].justification = 'Recorded retaliation. ' * 1000
    items[2].source = 'perspective'
    profile, _ = update_profile(TraitProfile(), items)
    traits = synthesis_traits(profile, items)
    integrity = next(trait for trait in traits if trait['name'] == 'integrity')
    examples = integrity['evidence_examples']
    assert len(examples) == 4
    assert {example['polarity'] for example in examples} == {'high', 'low'}
    assert items[2].id in {example['id'] for example in examples}
    assert max(len(example['justification']) for example in examples) <= 600
    assert integrity['observation_count'] == 8 and integrity['low_count'] == 1
    assert representative_evidence(items) == representative_evidence(list(reversed(items)))
    assert synthesis_traits(profile, items[:-1])[0]['evidence_examples'] == []
    previous = bounded_previous_identity({'identity_summary': 'x' * 10000, 'personality_traits': []})
    assert len(previous['identity_summary']) == 1800


@pytest.mark.parametrize('values', [(1, 1, 1), (1, -1, 1, -1, 1, -1)])
def test_synthesis_steadiness_reproduces_comparable_context_without_directional_entry(values):
    items = evidence(values, trait='forbearance')
    profile, _ = update_profile(TraitProfile(), items)
    context = synthesis_steadiness(profile, items)
    assert context == {
        'value': profile.steadiness.point, 'status': 'provisional',
        'calculation_version': 'cross-scene-steadiness-v2',
        'observation_count': len(items), 'availability': 'available',
        'reason': None, 'inferred_value': profile.steadiness.point,
    }
    assert (context['value'] > 5) == (len(values) == 3)
    assert 'steadiness' not in {row['name'] for row in synthesis_traits(profile, items)}


def test_synthesis_steadiness_unknown_and_manual_without_behavior_remain_unavailable():
    unknown = synthesis_steadiness(TraitProfile(), [])
    assert unknown['value'] == 5 and unknown['status'] == 'baseline'
    assert unknown['availability'] == 'unavailable' and unknown['inferred_value'] is None
    profile = apply_manual_edits(TraitProfile(), {'steadiness': TraitEdit(point=9, reason='Author choice')})
    manual = synthesis_steadiness(profile, [])
    assert manual['value'] == 9 and manual['status'] == 'manual'
    assert manual['availability'] == 'unavailable' and manual['observation_count'] == 0


def test_synthesis_steadiness_manual_preserves_conflicting_inference_and_snapshot_scope():
    first = evidence((1,) * 3, trait='forbearance', situation_type='retaliation:friend:ordinary')
    second = evidence((-1,) * 3, trait='forbearance', offset=3, situation_type='retaliation:enemy:ordinary')
    items = first + second
    profile, _ = update_profile(TraitProfile(), items)
    manual = apply_manual_edits(profile, {'steadiness': TraitEdit(point=1, reason='Author choice')})
    future = evidence((1, -1, 1), trait='forbearance', offset=6)
    context = synthesis_steadiness(manual, items + future)
    assert context['value'] == 1 and context['inferred_value'] == 4
    assert context['status'] == 'manual' and context['availability'] == 'available'
    assert context['observation_count'] == 6
    assert synthesis_steadiness(profile, items)['status'] == 'provisional'
    assert synthesis_steadiness(manual, items[:-1])['reason'] == 'Contributing evidence is missing.'
    assert synthesis_steadiness(manual, items + [first[0]]) == context
    altered = [item.model_copy(deep=True) for item in items]
    altered[0].situation_type = 'unspecified'
    assert synthesis_steadiness(manual, altered) == context  # contexts no longer affect inference


def test_synthesis_steadiness_reproduces_multi_trait_windows_and_deduplicated_perspectives():
    items = evidence((1,) * 16) + evidence((-1,) * 16, trait='caution')
    profile, _ = update_profile(TraitProfile(), items)
    context = synthesis_steadiness(profile, items)
    assert context['availability'] == 'available'
    assert context['observation_count'] == 12
    assert context['inferred_value'] == profile.steadiness.point


@pytest.mark.asyncio
async def test_identity_generation_uses_one_existing_call_with_evidence_and_continuity():
    from test_character_embodiment import FakeLLM, _agent, _canonical
    from app.jobs.character_agent.embody_agent import ScenePerspectiveOutput
    items = evidence((1, -1))
    profile, _ = update_profile(TraitProfile(), items)
    old = {'identity_summary': 'Old identity', 'psychological_summary': 'Old psychology',
           'personality_traits': []}
    llm = FakeLLM(json.dumps(old))
    perspectives = [ScenePerspectiveOutput(scene_id=f's{i}', evidence_ids=[f'scene:s{i}'],
                    source_type='participated', perspective='Grounded subjective context') for i in range(10)]
    await _agent(llm).generate_identity_description(
        canonical_identity=_canonical({'identity_description': old}), current_profile=profile,
        current_aspects=[], current_goals=[], current_trait_evidence=items,
        current_perspectives=perspectives,
    )
    assert len(llm.calls) == 1
    payload = json.loads(llm.calls[0]['messages'][1]['content'])
    assert payload['traits'][0]['low_count'] == 1
    assert payload['previous_identity_description']['role'] == 'continuity_only_not_evidence'
    assert payload['previous_identity_description']['description'] == old
    assert len(payload['grounded_perspectives']) == 6
    assert payload['grounded_perspectives'][0]['scene_id'] == 's4'
    assert payload['synthesis_version'] == SYNTHESIS_VERSION
    assert payload['steadiness'] == synthesis_steadiness(profile, items)
    assert len(payload['traits']) == 8


def test_diagnostics_count_duplicates_separate_authored_and_compare_shared_scenes():
    items = evidence((1, -1))
    report = diagnostic_report([
        {'character_id': 'a', 'processed_scene_ids': ['s0', 's1', 's2'],
         'evidence': [item.model_dump() for item in items + [items[0]]]},
        {'character_id': 'b', 'processed_scene_ids': ['s0'], 'evidence': []},
        {'character_id': 'c', 'evidence': []},
    ])
    assert report['counts_by_trait']['integrity'] == {'high': 1, 'low': 1}
    assert report['characters'][0]['duplicate_record_count'] == 1
    assert report['characters'][0]['scenes_without_observations'] == 1
    assert report['characters'][2]['processed_scene_count'] is None
    scene = report['shared_scenes'][0]
    assert scene['scene_id'] == 's0'
    assert scene['characters'][0]['observations'][0]['justification']
    assert scene['characters'][1]['processing'] == 'processed'
    assert scene['characters'][1]['observations'] == []
    assert scene['characters'][2]['processing'] == 'unknown'


@pytest.mark.asyncio
async def test_paginated_revision_read_uses_prior_ledger_without_future_leakage(monkeypatch):
    from app.services.character_agent_service import CharacterAgentService
    first, future = evidence((1,) * 7), evidence((-1,) * 10, offset=7)
    early, _ = update_profile(TraitProfile(), first)
    late, _ = update_profile(early, first + future)
    def node(number, profile):
        return {'node': dict(id=f'r{number}', character_agent_id='a', revision_number=number,
            name='Character', trait_profile=profile.storage_json(), provenance_type='generated',
            created_at='2026-10-11T00:00:00Z')}
    class Graph:
        async def run(self, query, **params):
            async def rows():
                if 'RETURN revision AS node' in query:
                    assert params['skip'] == 500 and params['public_only'] is True
                    yield node(501, early)
                    yield node(502, late)
                else:
                    assert params['revision'] == 502 and params['public_only'] is True
                    assert "coalesce(agent.visibility,'private')='public'" in query
                    yield {'revision_number': 2, 'evidence': json.dumps([item.model_dump() for item in first])}
                    yield {'revision_number': 502, 'evidence': json.dumps([item.model_dump() for item in future])}
            return rows()
    revisions = await CharacterAgentService(None, Graph()).list_identity_revisions('a', 500, 100, public_only=True)
    assert revisions[0].trait_history['integrity'].observation_count == 7
    assert revisions[0].trait_history['integrity'].low_count == 0
    assert revisions[1].trait_history['integrity'].low_count == 10
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'python_sdk'))
    from shrecknet_client.models import CharacterIdentityRevisionRead as SDKRevision
    assert SDKRevision.model_validate(revisions[0].model_dump()).trait_history['integrity'].high_count == 7


def test_prompt_contracts_explain_gates_poles_and_continuity():
    from app.jobs.character_agent.embody_agent_prompts import IDENTITY_DESCRIPTION_PROMPT, TRAIT_INTERPRETATION_PROMPT
    assert 'Evaluate all eight traits independently' in TRAIT_INTERPRETATION_PROMPT
    assert 'Prefer canonical evidence' in TRAIT_INTERPRETATION_PROMPT
    assert 'perspective:' in TRAIT_INTERPRETATION_PROMPT
    assert 'Steadiness' not in TRAIT_INTERPRETATION_PROMPT
    assert 2500 <= len(TRAIT_INTERPRETATION_PROMPT) <= 4000
    assert '1–2 strong low-pole' in IDENTITY_DESCRIPTION_PROMPT
    assert '8–9 strong' in IDENTITY_DESCRIPTION_PROMPT
    assert 'continuity context only, never new evidence' in IDENTITY_DESCRIPTION_PROMPT


def test_frozen_evaluation_covers_both_poles_and_scores_misattribution_and_omissions():
    from app.schemas.character_traits import DIRECTIONAL_TRAITS
    from app.services.character_trait_evaluation import score_cases
    fixture = json.loads((Path(__file__).parent / 'fixtures/trait_discrimination.json').read_text())
    cases = fixture['cases']
    poles = {tuple(pair) for case in cases for pair in case['expected']}
    assert poles == {(key, pole) for key in DIRECTIONAL_TRAITS for pole in ('high', 'low')}
    outputs = [{'case_id': case['case_id'], 'observations': [
        {'trait': trait, 'polarity': pole, 'justification': 'Fixture-only test output'}
        for trait, pole in case['expected']]} for case in cases]
    scored = score_cases(cases, outputs)
    assert scored['passed_cases'] == len(cases)
    assert scored['counts_by_pole']['low']['matched_required'] > 0
    assert scored['counts_by_pole']['low']['missed_required'] == 0
    assert scored['counts_by_pole']['high']['unsupported_candidates'] == 0
    assert any(len(case['expected']) == 8 for case in cases)
    outputs[-1]['observations'] = [{'trait': 'curiosity', 'polarity': 'low', 'justification': 'Unsupported'}]
    outputs[0]['observations'] = []
    report = score_cases(cases, outputs)
    assert report['unsupported_candidates'] == 1 and report['missed_required'] == 1
    assert report['counts_by_pole']['low']['unsupported_candidates'] == 1
    assert report['counts_by_pole']['high']['missed_required'] == 1
    assert report['passed_cases'] == len(cases) - 2
    with pytest.raises(ValueError, match='exactly one'):
        score_cases(cases, outputs[:-1])


@pytest.mark.asyncio
async def test_model_replay_reuses_extractor_without_profile_writes(monkeypatch):
    from app.services.character_trait_evaluation import replay_cases
    from app.jobs.character_agent import embody_agent as runtime
    from app.integrations.llm import shreckllm_client
    from test_character_embodiment import FakeLLM
    fixture = json.loads((Path(__file__).parent / 'fixtures/trait_discrimination.json').read_text())
    class ReplayLLM(FakeLLM):
        async def aclose(self):
            self.closed = True
    client = ReplayLLM(json.dumps({'scene_trait_interpretations': [dict.fromkeys(DIRECTIONAL_TRAITS)]}))
    monkeypatch.setattr(shreckllm_client, 'ShreckLLMClient', lambda **kwargs: client)
    original = runtime.TRAIT_INTERPRETATION_PROMPT
    output, provenance = await replay_cases([fixture['cases'][-1]])
    assert output == [{'case_id': 'fatal-danger', 'observations': []}]
    assert provenance['llm_calls'] == 1 and client.closed
    assert runtime.TRAIT_INTERPRETATION_PROMPT == original


@pytest.mark.asyncio
async def test_identity_initialization_accepts_empty_evidence_without_invented_counts():
    from test_character_embodiment import FakeLLM, _agent, _canonical
    llm = FakeLLM(json.dumps({'identity_summary': 'Identity', 'psychological_summary': 'Psychology', 'personality_traits': []}))
    await _agent(llm).generate_identity_description(canonical_identity=_canonical(),
        current_profile=TraitProfile(), current_aspects=[], current_goals=[])
    payload = json.loads(llm.calls[0]['messages'][1]['content'])
    assert all(trait['value'] is None and trait['evidence_examples'] == []
               and trait['observation_count'] == 0 for trait in payload['traits'])
    assert payload['previous_identity_description']['description'] is None
    assert payload['steadiness']['value'] == 5
    assert payload['steadiness']['status'] == 'baseline'
    assert payload['steadiness']['availability'] == 'unavailable'


@pytest.mark.asyncio
async def test_identity_validation_retry_preserves_separate_consistency_context():
    from test_character_embodiment import _agent, _canonical
    from test_character_agent_decision_making import FakeLLM
    portrait = {'identity_summary': 'Identity', 'psychological_summary': 'Psychology', 'personality_traits': []}
    llm = FakeLLM(['{}', json.dumps(portrait)])
    items = evidence((1,) * 3)
    profile, _ = update_profile(TraitProfile(), items)
    result = await _agent(llm).generate_identity_description(canonical_identity=_canonical(),
        current_profile=profile, current_aspects=[], current_goals=[], current_trait_evidence=items)
    first, retry = [json.loads(call['messages'][1]['content']) for call in llm.calls]
    assert result.model_dump(mode='json') == portrait
    assert retry['original_input'] == first
    assert first['steadiness']['value'] == 7
    assert first['steadiness']['availability'] == 'available'
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_invalid_legacy_ledger_keeps_snapshots_readable_with_unavailable_inference():
    from app.services.character_agent_service import CharacterAgentService
    class Graph:
        async def run(self, query, **params):
            async def rows():
                if 'RETURN revision AS node' in query:
                    yield {'node': dict(id='r1', character_agent_id='a', revision_number=1,
                        name='Character', trait_profile=TraitProfile().storage_json(),
                        provenance_type='generated', created_at='2026-10-11T00:00:00Z')}
                else:
                    yield {'revision_number': 1, 'evidence': 'invalid json'}
            return rows()
    row = (await CharacterAgentService(None, Graph()).list_identity_revisions('a', 0, 100))[0]
    assert row.trait_profile.estimate('integrity').point is None
    assert all(detail.availability == 'unavailable' for detail in row.trait_history.values())


def test_source_aware_evaluation_reports_wrong_source():
    from app.services.character_trait_evaluation import score_cases
    fixture = json.loads((Path(__file__).parent / 'fixtures/trait_weighted_sources.json').read_text())
    outputs = [{'case_id': case['case_id'], 'observations': [
        {'trait': trait, 'polarity': pole, 'source': case['expected_sources'][trait],
         'justification': 'Authored fixture contract, not live output.'}
        for trait, pole in case['expected']]} for case in fixture['cases']]
    report = score_cases(fixture['cases'], outputs)
    assert report['passed_cases'] == 21 and report['source_error_count'] == 0
    outputs[0]['observations'][0]['source'] = 'canonical'
    report = score_cases(fixture['cases'], outputs)
    assert report['passed_cases'] == 20 and report['source_error_count'] == 1


def test_snapshot_group_mapping_reproduces_retained_legacy_evidence():
    from app.services.character_trait_service import steadiness_estimate
    original = evidence((1, 1, 1, 1), trait='caution')
    resolved = [item.model_copy(update={'observation_group_id': 'same' if i < 2 else f'event-{i}'})
                for i, item in enumerate(original)]
    profile, _ = update_profile(TraitProfile(), resolved)
    assert profile.estimate('caution').observation_count == 3
    assert trait_history_details(profile, original)['caution'].availability == 'available'
    assert synthesis_steadiness(profile, original)['inferred_value'] == steadiness_estimate(resolved).point
    assert all(item.observation_group_id is None for item in original)
    assert synthesis_steadiness(profile, original[:-1])['availability'] == 'unavailable'


def test_representative_retelling_preserves_original_event_recency_in_history():
    original = evidence((1, -1, 1))
    original[0].source = 'perspective'
    original[0].observation_group_id = original[2].observation_group_id = 'retold-event'
    profile, _ = update_profile(TraitProfile(), original)
    details = trait_history_details(profile, original)['integrity']
    from app.services.character_trait_service import directional_details
    assert details.availability == 'available'
    assert details.inferred_continuous_value == directional_details('integrity', original).inferred_continuous_value


def test_mixed_retellings_and_windows_reproduce_revision_snapshots():
    """History and synthesis must reconstruct every estimate, including event anchors."""
    import random
    from app.schemas.character_traits import TraitObservation
    from app.services.character_trait_service import ground_observations
    rng = random.Random(17)
    for _ in range(24):
        scene_ids = [f'scene-{i}' for i in range(40)]
        perspective_ids = [f'perspective-{i}' for i in range(40)]
        observations = []
        for i, (scene, perspective) in enumerate(zip(scene_ids, perspective_ids)):
            event = f'event-{rng.randrange(24)}'
            for trait in ('caution', 'integrity', 'presence'):
                if rng.random() < .75:
                    observations.append(TraitObservation(trait=trait,
                        polarity=rng.choice(('high', 'low')), source=rng.choice(('canonical', 'perspective')),
                        observation_group_id=event, perspective_id=perspective,
                        evidence_ids=[f'scene:{scene}'], justification='Frozen synthetic event evidence.'))
        ledger = ground_observations(observations, scene_ids=scene_ids,
            perspective_ids=perspective_ids, source_group_id='source')
        profile, _ = update_profile(TraitProfile(), ledger)
        assert all(detail.availability == 'available' for detail in trait_history_details(profile, ledger).values())
        consistency = synthesis_steadiness(profile, ledger)
        assert consistency['availability'] == ('unavailable' if profile.steadiness.status == 'baseline' else 'available')


def test_event_recency_anchor_can_come_from_another_trait():
    from app.services.character_trait_service import directional_details
    ledger = evidence((1,), trait='presence') + evidence((-1, 1), trait='caution', offset=1)
    ledger[0].observation_group_id = ledger[2].observation_group_id = 'original-event'
    profile, _ = update_profile(TraitProfile(), ledger)
    assert profile.observation_group_positions['original-event'] == 0
    history = trait_history_details(profile, ledger)['caution']
    assert history.availability == 'available'
    assert history.inferred_continuous_value == directional_details('caution', ledger).inferred_continuous_value
