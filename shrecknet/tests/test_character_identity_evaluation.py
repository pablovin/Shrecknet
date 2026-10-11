"""Frozen identity comparisons reuse runtime contracts without graph persistence."""
import copy
import json
from pathlib import Path

import pytest

from app.schemas.character_traits import DIRECTIONAL_TRAITS, TraitEvidence, TraitProfile
from app.services.character_identity_evaluation import (
    decision_report, index_portraits, main, replay, synthesis_report,
)
from app.services.character_trait_analysis import synthesis_steadiness

FIXTURE = Path(__file__).parent / 'fixtures/identity_synthesis.json'
PORTRAIT = {'identity_summary': 'A traveler.', 'psychological_summary': 'Established psychology only.',
            'personality_traits': []}


def cases():
    return json.loads(FIXTURE.read_text())['cases']


def test_frozen_cases_cover_poles_interactions_motives_state_and_consistency():
    fixture = cases()
    assert {f'{trait}-{pole}' for trait in DIRECTIONAL_TRAITS for pole in ('low', 'high')} <= {
        case['case_id'] for case in fixture}
    assert {'competing-goals', 'non-diagnostic', 'supported-interaction', 'unsupported-interaction',
            'constrained-forbearance-low', 'constrained-forbearance-high', 'manual-stale-portrait'} <= {
        case['case_id'] for case in fixture}
    contexts = {}
    for case in fixture:
        profile = TraitProfile.model_validate(case['input']['current_profile'])
        evidence = [TraitEvidence.model_validate(row) for row in case['input']['current_trait_evidence']]
        contexts[case['case_id']] = synthesis_steadiness(profile, evidence)
    assert contexts['unknown']['value'] is None
    assert contexts['sparse']['value'] is None
    assert contexts['mixed']['value'] < 5
    assert contexts['comparable-contexts']['status'] == 'supported'
    assert contexts['manual-conflict']['value'] != contexts['manual-conflict']['inferred_value']
    assert contexts['missing-ledger']['availability'] == 'unavailable'
    a, b = [case for case in fixture if case['case_id'].startswith('equal-diligence')]
    assert a['input']['current_profile'] == b['input']['current_profile']
    assert a['input']['canonical_identity'] != b['input']['canonical_identity']


def test_offline_portrait_report_requires_exact_case_coverage_and_valid_contracts():
    fixture = cases()[:2]
    outputs = [{'case_id': case['case_id'], 'identity_description': PORTRAIT} for case in fixture]
    report = synthesis_report(fixture, outputs)
    assert len(report['outputs']) == 2
    assert 'passed' not in report  # No fabricated psychological quality score.
    for bad in (outputs[:-1], outputs + outputs[:1]):
        with pytest.raises(ValueError, match='exactly one'):
            index_portraits(fixture, bad)
    invalid = copy.deepcopy(outputs)
    invalid[0]['identity_description']['personality_traits'] = [{'trait': 'steadiness', 'description': 'Consistent'}]
    with pytest.raises(ValueError):
        synthesis_report(fixture, invalid)


def test_decision_report_validates_trials_and_options_without_scoring_character_fit():
    from app.jobs.character_agent.decision_making import CharacterGenerationError
    fixture = cases()[:1]
    outputs = [{'case_id': fixture[0]['case_id'], 'variant': variant, 'trial': trial,
                'result': {'type': 'json', 'content': {'action': 'refuse'}, 'decision_basis': 'Public explanation'}}
               for variant in ('baseline', 'candidate', 'neutral') for trial in range(2)]
    report = decision_report(fixture, outputs, 2)
    assert report['action_counts'][fixture[0]['case_id']]['candidate'] == {'refuse': 2}
    for bad in (outputs[:-1], outputs + outputs[:1]):
        with pytest.raises(ValueError, match='exactly one'):
            decision_report(fixture, bad, 2)
    invalid = copy.deepcopy(outputs)
    invalid[0]['result']['content']['action'] = 'invented'
    with pytest.raises(CharacterGenerationError):
        decision_report(fixture, invalid, 2)


@pytest.mark.asyncio
async def test_synthesis_replay_uses_local_baseline_override_and_closes_client(monkeypatch):
    from app.integrations.llm import shreckllm_client
    from app.jobs.character_agent import embody_agent as runtime
    from test_character_embodiment import FakeLLM

    class Client(FakeLLM):
        async def aclose(self):
            self.closed = True

    client = Client(json.dumps(PORTRAIT))
    monkeypatch.setattr(shreckllm_client, 'ShreckLLMClient', lambda **kwargs: client)
    original = runtime.IDENTITY_DESCRIPTION_PROMPT
    baseline = (FIXTURE.parent / 'identity_synthesis_baseline.txt').read_text()
    outputs, provenance = await replay(cases()[:1], mode='synthesis', prompt=baseline)
    assert outputs[0]['identity_description'] == PORTRAIT
    assert client.calls[0]['messages'][0]['content'] == baseline
    assert 'steadiness' in json.loads(client.calls[0]['messages'][1]['content'])
    assert provenance['llm_calls'] == 1 and provenance['temperature'] == 0
    assert client.closed and runtime.IDENTITY_DESCRIPTION_PROMPT == original


@pytest.mark.asyncio
async def test_decision_replay_changes_only_portrait_with_fixed_context_and_empty_memory(monkeypatch):
    from app.integrations.llm import shreckllm_client
    from test_character_agent_decision_making import FakeLLM

    class Client(FakeLLM):
        async def aclose(self):
            self.closed = True

    result = json.dumps({'content': {'action': 'refuse'}, 'decision_basis': 'Public explanation'})
    client = Client([result] * 6)
    monkeypatch.setattr(shreckllm_client, 'ShreckLLMClient', lambda **kwargs: client)
    fixture = cases()[:1]
    key = fixture[0]['case_id']
    candidate = {**PORTRAIT, 'psychological_summary': 'Different supported psychology.'}
    outputs, provenance = await replay(fixture, mode='decisions', trials=2,
        portraits={'baseline': {key: PORTRAIT}, 'candidate': {key: candidate}})
    assert len(outputs) == provenance['llm_calls'] == 6
    payloads = [json.loads(call['messages'][1]['content']) for call in client.calls]
    assert payloads[0]['identity_description'] != payloads[1]['identity_description']
    assert {key: value for key, value in payloads[0].items() if key != 'identity_description'} == {
        key: value for key, value in payloads[1].items() if key != 'identity_description'}
    assert payloads[0]['memories'] == payloads[1]['memories'] == []
    assert all(payload['context'] == fixture[0]['decision']['context'] for payload in payloads)
    assert 'identity_description' not in payloads[2] and 'memories' not in payloads[2]
    assert [row['variant'] for row in outputs[3:]] == ['neutral', 'candidate', 'baseline']
    assert client.closed
    decision_report(fixture, outputs, 2)


@pytest.mark.asyncio
async def test_failed_replay_closes_client(monkeypatch):
    from app.integrations.llm import shreckllm_client

    class BrokenClient:
        async def chat(self, **kwargs):
            raise RuntimeError('Unavailable evaluation provider')

        async def aclose(self):
            self.closed = True

    client = BrokenClient()
    monkeypatch.setattr(shreckllm_client, 'ShreckLLMClient', lambda **kwargs: client)
    with pytest.raises(RuntimeError):
        await replay(cases()[:1], mode='synthesis')
    assert client.closed


def test_captured_cli_stays_offline_and_does_not_publish_invalid_report(tmp_path, monkeypatch):
    captured, target = tmp_path / 'outputs.json', tmp_path / 'report.json'
    captured.write_text(json.dumps([{'case_id': case['case_id'], 'identity_description': PORTRAIT} for case in cases()]))
    monkeypatch.setattr('sys.argv', ['evaluation', 'synthesis', str(FIXTURE), str(target), '--outputs', str(captured)])
    main()
    report = json.loads(target.read_text())
    assert len(report['outputs']) == 32
    assert report['provenance']['mode'] == 'captured-outputs'
    target.unlink()
    captured.write_text('[]')
    with pytest.raises(ValueError):
        main()
    assert not target.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('comparison', ['prompt', 'context', 'reasoning'])
async def test_decision_experiments_isolate_prompt_context_and_reasoning(monkeypatch, comparison):
    from app.integrations.llm import shreckllm_client
    from app.jobs.character_agent import prompts
    from app.core.config_store import LLMModelTarget
    from test_character_agent_decision_making import FakeLLM

    class Client(FakeLLM):
        async def aclose(self):
            self.closed = True

    result = json.dumps({'content': {'action': 'refuse'}, 'decision_basis': 'Public explanation'})
    client = Client([result] * 3)
    monkeypatch.setattr(shreckllm_client, 'ShreckLLMClient', lambda **kwargs: client)
    fixture = cases()[:1]
    key = fixture[0]['case_id']
    target = LLMModelTarget(provider='test', name='frozen', reasoning=False)
    baseline_prompt = (FIXTURE.parent / 'decision_making_baseline.txt').read_text()
    original = prompts.DECISION_MAKING_PROMPT
    outputs, provenance = await replay(fixture, mode='decisions', comparison=comparison,
        decision_baseline_prompt=baseline_prompt if comparison == 'prompt' else None,
        deliberation_target=target,
        portraits={'baseline': {key: {**PORTRAIT, 'identity_summary': 'Unused old portrait'}},
                   'candidate': {key: PORTRAIT}})
    payloads = [json.loads(call['messages'][1]['content']) for call in client.calls]
    assert payloads[0]['identity_description'] == payloads[1]['identity_description'] == PORTRAIT
    if comparison == 'context':
        assert 'personality_context' not in payloads[0]
        assert 'personality_context' in payloads[1]
        assert {k: v for k, v in payloads[1].items() if k != 'personality_context'} == payloads[0]
    else:
        assert payloads[0] == payloads[1]
    assert client.calls[0]['messages'][0]['content'] == (baseline_prompt if comparison == 'prompt' else original)
    assert client.calls[1]['messages'][0]['content'] == original
    assert client.calls[0]['model'].reasoning is False
    assert client.calls[1]['model'].reasoning is (comparison == 'reasoning')
    assert set(payloads[2]) == {'query', 'context', 'instruction', 'response_format'}
    assert all(row['llm_calls'] == 1 and row['latency_s'] >= 0 and row['request_sha256'] for row in outputs)
    assert provenance['comparison'] == comparison and provenance['input_tokens_est'] > 0
    assert prompts.DECISION_MAKING_PROMPT == original and target.reasoning is False
    assert client.closed
    assert decision_report(fixture, outputs, 1)['failed_trials'] == 0


@pytest.mark.asyncio
async def test_decision_replay_retains_failed_trials_without_retrying_or_mutating_globals(monkeypatch):
    from app.integrations.llm import shreckllm_client
    class BrokenClient:
        async def chat(self, **kwargs):
            raise RuntimeError('Unavailable evaluation provider')
        async def aclose(self):
            self.closed = True
    client = BrokenClient()
    monkeypatch.setattr(shreckllm_client, 'ShreckLLMClient', lambda **kwargs: client)
    fixture = cases()[:1]
    key = fixture[0]['case_id']
    outputs, provenance = await replay(fixture, mode='decisions',
        portraits={arm: {key: PORTRAIT} for arm in ('baseline', 'candidate')})
    assert provenance['llm_calls'] == 3
    report = decision_report(fixture, outputs, 1)
    assert report['failed_trials'] == 3 and report['action_counts'][key]['candidate'] == {}
    assert all(row['error']['type'] == 'RuntimeError' and 'result' not in row for row in outputs)
    assert client.closed


def test_all_frozen_profiles_produce_typed_compact_context_without_evidence_ids():
    from app.services.character_trait_analysis import decision_personality_context
    for case in cases():
        value = case['input']
        context = decision_personality_context(TraitProfile.model_validate(value['current_profile']),
            [TraitEvidence.model_validate(item) for item in value['current_trait_evidence']])
        assert set(context.dispositions) == set(DIRECTIONAL_TRAITS)
        encoded = context.model_dump_json()
        assert 'observation_ids' not in encoded and 'evidence_ids' not in encoded
