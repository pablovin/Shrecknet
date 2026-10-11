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
import time
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
        if row.get('error') is not None:
            if row.get('result') is not None or not isinstance(row['error'], dict) or not row['error'].get('type'):
                raise ValueError('Failed trials require an error type and no result.')
            normalized.append(row)
            continue
        result = CharacterAgentQueryResult.model_validate(row['result'])
        if result.type != 'json':
            raise ValueError('Controlled decisions require JSON results.')
        request = decision_request(indexed[row['case_id']], row['variant'], .7)
        CharacterAgentDecisionMakingJob._validate_content(request, result.content)
        counts[row['case_id']][row['variant']][result.content['action']] += 1
        normalized.append({**row, 'result': result.model_dump(mode='json')})
    return {'report_version': 2, 'mode': 'decisions', 'trials': trials, 'outputs': normalized,
            'action_counts': counts,
            'failed_trials': sum(row.get('error') is not None for row in outputs),
            'limitation': 'Choice counts do not prove character fit or statistical improvement. Review repeated trials and uncertainty; identical choices can be appropriate.'}


async def replay(cases: list[dict], *, mode: str, prompt: str | None = None,
                 portraits: dict | None = None, trials: int = 1, temperature: float = .7,
                 comparison: str = 'portraits', decision_baseline_prompt: str | None = None,
                 deliberation_target=None) -> tuple[list, dict]:
    from app.core.config_store import get_settings
    from app.integrations.llm.shreckllm_client import ShreckLLMClient
    from app.jobs.character_agent.decision_making import CharacterAgentDecisionMakingJob
    from app.jobs.character_agent.embody_agent import EmbodyAgent, ScenePerspectiveOutput
    from app.jobs.character_agent.embody_agent_prompts import IDENTITY_DESCRIPTION_PROMPT, PROMPT_VERSION
    from app.jobs.character_agent.prompts import DECISION_MAKING_PROMPT, GENERIC_DECISION_MAKING_PROMPT, DECISION_PROMPT_VERSION
    from app.schemas.character_traits import TraitEvidence, TraitProfile
    from app.services.character_trait_analysis import SYNTHESIS_VERSION, decision_personality_context

    # Keep baseline overrides local to this evaluation instance. Never mutate the
    # production prompt module or introduce a second synthesis implementation.
    class SynthesisReplay(EmbodyAgent):
        async def _call(self, **kwargs):
            if prompt is not None:
                kwargs['prompt'] = prompt
                # Explicit offline prompt overrides retain the historical wire
                # contract, which permits sparse portraits. Production synthesis
                # always uses its complete eight-trait generation schema.
                kwargs['schema'] = IdentityDescription
            return await super()._call(**kwargs)

    if comparison not in {'portraits', 'prompt', 'context', 'reasoning'}:
        raise ValueError('Unknown decision comparison.')
    if mode == 'decisions' and comparison == 'prompt' and not decision_baseline_prompt:
        raise ValueError('Prompt comparison requires a complete baseline decision prompt.')
    settings = get_settings()
    target = deliberation_target or settings.model_character_agent_decision_making
    client = ShreckLLMClient(base_url=settings.shreckllm_base_url,
                            timeout=settings.shreckllm_request_timeout_s,
                            max_retries=settings.shreckllm_max_retries,
                            chat_job_timeout_s=settings.shreckllm_chat_job_timeout_s)
    # Count requests including validation replacements and decision JSON repair.
    class RecordedClient:
        def __init__(self):
            self.calls = 0
            self.prompt_override = None
            self.omit_personality_context = False
            self.input_hashes = []
            self.input_tokens_est = 0

        async def chat(self, **kwargs):
            if self.prompt_override is not None and kwargs.get('usage_tag') == 'character_agent.decision_making':
                kwargs['messages'] = [dict(message) for message in kwargs['messages']]
                kwargs['messages'][0]['content'] = self.prompt_override
            if self.omit_personality_context and kwargs.get('usage_tag') == 'character_agent.decision_making':
                # Portrait-only ablations exist only in this offline comparison.
                # The production job always validates and supplies numerical context.
                kwargs['messages'] = [dict(message) for message in kwargs['messages']]
                value = json.loads(kwargs['messages'][1]['content'])
                value.pop('personality_context', None)
                kwargs['messages'][1]['content'] = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            self.calls += 1
            encoded = json.dumps(kwargs['messages'], sort_keys=True, ensure_ascii=False)
            self.input_hashes.append(digest(encoded))
            self.input_tokens_est += sum(max(1, len(message['content']) // 4) for message in kwargs['messages'])
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
                    psychological_interpretation_model=settings.model_character_agent_psychological_interpretation,
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
                        portrait_arm = variant if comparison == 'portraits' else 'candidate'
                        snapshot = None if variant == 'neutral' else {
                            'character_agent': {'identity_description': portraits[portrait_arm][case['case_id']]},
                            'memories': [],
                        }
                        include_context = variant != 'neutral' and (
                            comparison == 'reasoning' or (comparison == 'context' and variant == 'candidate'))
                        if variant != 'neutral':
                            value = case['input']
                            snapshot['personality_context'] = decision_personality_context(
                                TraitProfile.model_validate(value['current_profile']),
                                [TraitEvidence.model_validate(item) for item in value['current_trait_evidence']],
                            ).model_dump(mode='json')
                        arm_target = target
                        if comparison == 'reasoning' and variant != 'neutral':
                            arm_target = target.model_copy(update={'reasoning': variant == 'candidate'})
                        recorded.prompt_override = decision_baseline_prompt if comparison == 'prompt' and variant == 'baseline' else None
                        recorded.omit_personality_context = not include_context
                        job = CharacterAgentDecisionMakingJob(llm_client=recorded,
                            decision_making_model=arm_target,
                            repair_model=settings.model_agents_repair_json)
                        started = time.monotonic()
                        calls_before, tokens_before = recorded.calls, recorded.input_tokens_est
                        row = {'case_id': case['case_id'], 'variant': variant, 'trial': trial,
                               'model': arm_target.model_dump(mode='json'),
                               'personality_context': include_context}
                        try:
                            result = await job.run(request, snapshot)
                            row['result'] = result.model_dump(mode='json')
                        except Exception as exc:
                            # Preserve every failed arm/trial rather than selectively
                            # retrying or publishing a success-only comparison.
                            row['error'] = {'type': type(exc).__name__, 'message': str(exc)}
                        row.update(latency_s=time.monotonic() - started,
                                   llm_calls=recorded.calls - calls_before,
                                   input_tokens_est=recorded.input_tokens_est - tokens_before,
                                   request_sha256=recorded.input_hashes[calls_before:])
                        outputs.append(row)
    finally:
        await client.aclose()
    return outputs, {
        'prompt_version': PROMPT_VERSION, 'synthesis_version': SYNTHESIS_VERSION,
        'decision_prompt_version': DECISION_PROMPT_VERSION if mode == 'decisions' else None,
        'personality_context_version': 'decision-personality-v1' if mode == 'decisions' else None,
        'comparison': comparison if mode == 'decisions' else None,
        'decision_baseline_prompt_sha256': digest(decision_baseline_prompt) if decision_baseline_prompt else None,
        'prompt_sha256': digest(prompt if prompt is not None else IDENTITY_DESCRIPTION_PROMPT)
            if mode == 'synthesis' else digest(DECISION_MAKING_PROMPT),
        'neutral_prompt_sha256': digest(GENERIC_DECISION_MAKING_PROMPT) if mode == 'decisions' else None,
        'custom_synthesis_prompt': prompt is not None,
        'model': (settings.model_character_agent_character_incorporation if mode == 'synthesis'
                  else target).model_dump(mode='json'),
        'repair_model': settings.model_agents_repair_json.model_dump(mode='json') if mode == 'decisions' else None,
        'validation_retries': settings.character_agent_embodiment_validation_retries if mode == 'synthesis' else 1,
        'input_tokens_est': recorded.input_tokens_est,
        'llm_calls': recorded.calls, 'synthesis_total_tokens_est': tokens if mode == 'synthesis' else None,
        'temperature': temperature if mode == 'decisions' else 0.0,
        'seed': None, 'memory_control': 'identical empty memories; all situational knowledge in frozen context',
    }


def _load_target(path: Path):
    from app.core.config_store import LLMModelTarget
    return LLMModelTarget.model_validate_json(path.read_text())


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
    parser.add_argument('--comparison', choices=['portraits', 'prompt', 'context', 'reasoning'], default='portraits')
    parser.add_argument('--decision-baseline-prompt', type=Path, help='Complete old decision prompt for --comparison prompt')
    parser.add_argument('--deliberation-target', type=Path, help='Evaluation-only LLMModelTarget JSON file')
    parser.add_argument('--trials', type=int, default=20)
    parser.add_argument('--temperature', type=float, default=.7)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error('--trials must be positive')
    if args.prompt_file and (args.mode != 'synthesis' or not args.live):
        parser.error('--prompt-file requires live synthesis')
    if args.mode == 'decisions' and args.live and (not args.baseline or not args.candidate):
        parser.error('Live decisions require --baseline and --candidate synthesis reports')
    if args.mode != 'decisions' and (args.comparison != 'portraits' or args.decision_baseline_prompt or args.deliberation_target):
        parser.error('Decision overrides require decisions mode')
    if args.live and args.mode == 'decisions' and args.comparison == 'prompt' and not args.decision_baseline_prompt:
        parser.error('--comparison prompt requires --decision-baseline-prompt')
    if not args.live and (args.decision_baseline_prompt or args.deliberation_target):
        parser.error('Prompt/model overrides require --live')
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
            portraits=portraits, trials=args.trials, temperature=args.temperature,
            comparison=args.comparison,
            decision_baseline_prompt=args.decision_baseline_prompt.read_text() if args.decision_baseline_prompt else None,
            deliberation_target=_load_target(args.deliberation_target) if args.deliberation_target else None))
    else:
        content = args.outputs.read_text()
        outputs, provenance = json.loads(content), {'mode': 'captured-outputs', 'outputs_sha256': digest(content), 'comparison': args.comparison if args.mode == 'decisions' else None}
    report = synthesis_report(cases, outputs) if args.mode == 'synthesis' else decision_report(cases, outputs, args.trials)
    report['provenance'] = {**provenance, 'fixture_sha256': digest(fixture_text),
                            'fixture_version': fixture['version'], 'portrait_report_sha256': portrait_hashes}
    # Synthesis failures abort; decision failures remain explicit trial records.
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
