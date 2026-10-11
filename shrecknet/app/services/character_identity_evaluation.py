"""Compare frozen identity synthesis and controlled decisions without graph writes.

Run from shrecknet/: python -m app.services.character_identity_evaluation --help.
Live replay is explicit. Captured-output reports validate structure and summarize
choices; psychological fidelity requires human review, not keyword scoring.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

from app.schemas.character_agent import IdentityDescription


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def index_portraits(cases: list[dict], outputs: list[dict]) -> dict:
    indexed = {row['case_id']: row['identity_description'] for row in outputs}
    if len(indexed) != len(outputs) or set(indexed) != {case['case_id'] for case in cases}:
        raise ValueError('Portraits must contain exactly one record per fixture case.')
    return {key: IdentityDescription.model_validate(value).model_dump(mode='json')
            for key, value in indexed.items()}


def synthesis_report(cases: list[dict], outputs: list[dict]) -> dict:
    portraits = index_portraits(cases, outputs)
    return {
        'report_version': 1, 'mode': 'synthesis',
        'outputs': [{'case_id': case['case_id'], 'identity_description': portraits[case['case_id']],
                     'review_note': case['review_note']} for case in cases],
        'review_rubric': ['evidence fidelity', 'pole fidelity', 'conditional specificity',
                          'unsupported causes or exceptions', 'repetition'],
        'limitation': 'Schema validity is not evidence of psychological quality. Blind human comparison is required.',
    }


def decision_request(case: dict, variant: str, temperature: float):
    from app.schemas.character_agent import CharacterAgentQueryRequest

    decision = case['decision']
    return CharacterAgentQueryRequest(
        query=decision['query'], context=decision['context'],
        use_character_identity=variant != 'neutral', generation={'temperature': temperature},
        response_format={'type': 'json', 'schema': {
            'type': 'object', 'additionalProperties': False, 'required': ['action'],
            'properties': {'action': {'type': 'string', 'enum': decision['options']}},
        }},
    )


def decision_report(cases: list[dict], outputs: list[dict], trials: int) -> dict:
    from app.jobs.character_agent.decision_making import CharacterAgentDecisionMakingJob
    from app.schemas.character_agent import CharacterAgentQueryResult

    expected = {(case['case_id'], variant, trial) for case in cases
                for variant in ('baseline', 'candidate', 'neutral') for trial in range(trials)}
    keys = [(row['case_id'], row['variant'], row['trial']) for row in outputs]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError('Decisions must contain exactly one record per case, variant, and trial.')
    indexed = {case['case_id']: case for case in cases}
    counts = {key: {variant: Counter() for variant in ('baseline', 'candidate', 'neutral')}
              for key in indexed}
    normalized = []
    for row in outputs:
        result = CharacterAgentQueryResult.model_validate(row['result'])
        if result.type != 'json':
            raise ValueError('Controlled decisions require JSON results.')
        request = decision_request(indexed[row['case_id']], row['variant'], .7)
        CharacterAgentDecisionMakingJob._validate_content(request, result.content)
        counts[row['case_id']][row['variant']][result.content['action']] += 1
        normalized.append({**row, 'result': result.model_dump(mode='json')})
    return {'report_version': 1, 'mode': 'decisions', 'trials': trials, 'outputs': normalized,
            'action_counts': counts,
            'limitation': 'Choice counts do not prove character fit or statistical improvement. Review repeated trials and uncertainty; identical choices can be appropriate.'}


async def replay(cases: list[dict], *, mode: str, prompt: str | None = None,
                 portraits: dict | None = None, trials: int = 1, temperature: float = .7) -> tuple[list, dict]:
    from app.core.config_store import get_settings
    from app.integrations.llm.shreckllm_client import ShreckLLMClient
    from app.jobs.character_agent.decision_making import CharacterAgentDecisionMakingJob
    from app.jobs.character_agent.embody_agent import EmbodyAgent, ScenePerspectiveOutput
    from app.jobs.character_agent.embody_agent_prompts import IDENTITY_DESCRIPTION_PROMPT, PROMPT_VERSION
    from app.jobs.character_agent.prompts import DECISION_MAKING_PROMPT, GENERIC_DECISION_MAKING_PROMPT
    from app.schemas.character_traits import TraitEvidence, TraitProfile
    from app.services.character_trait_analysis import SYNTHESIS_VERSION

    # Keep baseline overrides local to this evaluation instance. Never mutate the
    # production prompt module or introduce a second synthesis implementation.
    class SynthesisReplay(EmbodyAgent):
        async def _call(self, **kwargs):
            if prompt is not None:
                kwargs['prompt'] = prompt
            return await super()._call(**kwargs)

    settings = get_settings()
    client = ShreckLLMClient(base_url=settings.shreckllm_base_url,
                            timeout=settings.shreckllm_request_timeout_s,
                            max_retries=settings.shreckllm_max_retries,
                            chat_job_timeout_s=settings.shreckllm_chat_job_timeout_s)
    # Count requests including validation replacements and decision JSON repair.
    class RecordedClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return await client.chat(**kwargs)

        def __getattr__(self, name):
            return getattr(client, name)

    recorded = RecordedClient()
    outputs, tokens = [], 0
    try:
        for case in cases:
            if mode == 'synthesis':
                value = case['input']
                job = SynthesisReplay(llm_client=recorded,
                    character_incorporation_model=settings.model_character_agent_character_incorporation,
                    scene_interpretation_model=settings.model_character_agent_scene_interpretation,
                    validation_retries=settings.character_agent_embodiment_validation_retries)
                result = await job.generate_identity_description(
                    canonical_identity=value['canonical_identity'],
                    current_profile=TraitProfile.model_validate(value['current_profile']),
                    current_trait_evidence=[TraitEvidence.model_validate(item) for item in value['current_trait_evidence']],
                    current_perspectives=[ScenePerspectiveOutput.model_validate(item)
                                          for item in value.get('current_perspectives', [])],
                    current_goals=value.get('current_goals', []), current_aspects=value.get('current_aspects', []),
                )
                outputs.append({'case_id': case['case_id'], 'identity_description': result.model_dump(mode='json')})
                tokens += sum(call.total_tokens_est for call in job.llm_calls)
            else:
                if portraits is None:
                    raise ValueError('Decision replay requires baseline and candidate portraits.')
                for trial in range(trials):
                    # Alternate arm order to reduce simple ordering effects.
                    variants = ('baseline', 'candidate', 'neutral') if trial % 2 == 0 else ('neutral', 'candidate', 'baseline')
                    for variant in variants:
                        request = decision_request(case, variant, temperature)
                        snapshot = None if variant == 'neutral' else {
                            'character_agent': {'identity_description': portraits[variant][case['case_id']]},
                            'memories': [],
                        }
                        job = CharacterAgentDecisionMakingJob(llm_client=recorded,
                            deliberation_model=settings.model_character_agent_deliberation,
                            repair_model=settings.model_agents_repair_json)
                        result = await job.run(request, snapshot)
                        outputs.append({'case_id': case['case_id'], 'variant': variant, 'trial': trial,
                                        'result': result.model_dump(mode='json')})
    finally:
        await client.aclose()
    return outputs, {
        'prompt_version': PROMPT_VERSION, 'synthesis_version': SYNTHESIS_VERSION,
        'prompt_sha256': digest(prompt if prompt is not None else IDENTITY_DESCRIPTION_PROMPT)
            if mode == 'synthesis' else digest(DECISION_MAKING_PROMPT),
        'neutral_prompt_sha256': digest(GENERIC_DECISION_MAKING_PROMPT) if mode == 'decisions' else None,
        'custom_synthesis_prompt': prompt is not None,
        'model': (settings.model_character_agent_scene_interpretation if mode == 'synthesis'
                  else settings.model_character_agent_deliberation).model_dump(mode='json'),
        'repair_model': settings.model_agents_repair_json.model_dump(mode='json') if mode == 'decisions' else None,
        'validation_retries': settings.character_agent_embodiment_validation_retries if mode == 'synthesis' else 1,
        'llm_calls': recorded.calls, 'synthesis_total_tokens_est': tokens if mode == 'synthesis' else None,
        'temperature': temperature if mode == 'decisions' else 0.0,
        'seed': None, 'memory_control': 'identical empty memories; all situational knowledge in frozen context',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['synthesis', 'decisions'])
    parser.add_argument('fixtures', type=Path)
    parser.add_argument('report', type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--live', action='store_true')
    source.add_argument('--outputs', type=Path, help='Captured JSON array; no inference calls')
    parser.add_argument('--prompt-file', type=Path, help='Complete baseline synthesis prompt including registry contract')
    parser.add_argument('--baseline', type=Path, help='Baseline synthesis report for live decisions')
    parser.add_argument('--candidate', type=Path, help='Candidate synthesis report for live decisions')
    parser.add_argument('--trials', type=int, default=20)
    parser.add_argument('--temperature', type=float, default=.7)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error('--trials must be positive')
    if args.prompt_file and (args.mode != 'synthesis' or not args.live):
        parser.error('--prompt-file requires live synthesis')
    if args.mode == 'decisions' and args.live and (not args.baseline or not args.candidate):
        parser.error('Live decisions require --baseline and --candidate synthesis reports')
    fixture_text = args.fixtures.read_text()
    fixture = json.loads(fixture_text)
    cases = fixture['cases']
    if not cases or len({case['case_id'] for case in cases}) != len(cases):
        parser.error('Fixtures require nonempty cases with unique case_id values')
    portraits, portrait_hashes = None, {}
    if args.mode == 'decisions' and args.live:
        portraits = {}
        for variant, path in [('baseline', args.baseline), ('candidate', args.candidate)]:
            content = path.read_text()
            report = json.loads(content)
            if report['provenance']['fixture_sha256'] != digest(fixture_text):
                parser.error('Portrait reports must use the identical fixture file')
            portraits[variant] = index_portraits(cases, report['outputs'])
            portrait_hashes[variant] = digest(content)
    if args.live:
        outputs, provenance = asyncio.run(replay(cases, mode=args.mode,
            prompt=args.prompt_file.read_text() if args.prompt_file else None,
            portraits=portraits, trials=args.trials, temperature=args.temperature))
    else:
        content = args.outputs.read_text()
        outputs, provenance = json.loads(content), {'mode': 'captured-outputs', 'outputs_sha256': digest(content)}
    report = synthesis_report(cases, outputs) if args.mode == 'synthesis' else decision_report(cases, outputs, args.trials)
    report['provenance'] = {**provenance, 'fixture_sha256': digest(fixture_text),
                            'fixture_version': fixture['version'], 'portrait_report_sha256': portrait_hashes}
    # Failed inference/validation never publishes a successful report.
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
