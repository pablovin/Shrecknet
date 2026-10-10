"""Replay frozen diagnostic scenes without profile writes, or score captured outputs.

Model replay is opt-in through --live; offline scoring never calls an LLM.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path


def score_cases(cases: list[dict], outputs: list[dict]) -> dict:
    indexed = {row['case_id']: row for row in outputs}
    if len(indexed) != len(outputs) or set(indexed) != {case['case_id'] for case in cases}:
        raise ValueError('Captured outputs must contain exactly one record per fixture case.')
    results, tp, fp, fn = [], 0, 0, 0
    for case in cases:
        observations = indexed[case['case_id']]['observations']
        expected = {tuple(pair) for pair in case['expected']}
        allowed = {tuple(pair) for pair in case.get('allowed', case['expected'])}
        actual = {(item['trait'], item['polarity']) for item in observations}
        matched, unsupported, missed = actual & expected, actual - allowed, expected - actual
        tp, fp, fn = tp + len(matched), fp + len(unsupported), fn + len(missed)
        results.append({
            'case_id': case['case_id'], 'target': case['target'],
            'passed': not unsupported and not missed and len(observations) <= 3
                and len({item['trait'] for item in observations}) == len(observations),
            'unsupported': sorted(unsupported), 'missed': sorted(missed),
            'observations': observations, 'review_note': case['review_note'],
        })
    return {'report_version': 1, 'case_count': len(cases),
            'passed_cases': sum(row['passed'] for row in results),
            'matched_required': tp, 'unsupported_candidates': fp, 'missed_required': fn,
            'cases': results,
            'limitation': 'Synthetic fixtures evaluate candidate attribution/polarity; justifications and psychological claims require human review.'}


async def replay_cases(cases: list[dict], prompt_file: Path | None = None) -> tuple[list[dict], dict]:
    # Import the runtime only for explicit model replay, keeping scoring offline.
    from app.core.config_store import get_settings
    from app.integrations.llm.shreckllm_client import ShreckLLMClient
    from app.jobs.character_agent import embody_agent as runtime
    from app.jobs.character_agent.embody_agent import EmbodyAgent, ScenePerspectiveOutput
    from app.jobs.character_agent.embody_agent_prompts import PROMPT_VERSION

    settings = get_settings()
    original_prompt = runtime.TRAIT_INTERPRETATION_PROMPT
    prompt = prompt_file.read_text() if prompt_file else original_prompt
    client = ShreckLLMClient(base_url=settings.shreckllm_base_url,
                            timeout=settings.shreckllm_request_timeout_s,
                            max_retries=settings.shreckllm_max_retries,
                            chat_job_timeout_s=settings.shreckllm_chat_job_timeout_s)
    output, calls, tokens = [], 0, 0
    try:
        runtime.TRAIT_INTERPRETATION_PROMPT = prompt
        for case in cases:
            job = EmbodyAgent(llm_client=client,
                character_incorporation_model=settings.model_character_agent_character_incorporation,
                scene_interpretation_model=settings.model_character_agent_scene_interpretation,
                validation_retries=settings.character_agent_embodiment_validation_retries)
            perspective = ScenePerspectiveOutput(
                scene_id=case['scene_id'], evidence_ids=[f"scene:{case['scene_id']}"],
                source_type=case.get('source_type', 'participated'),
                perspective=case['perspective'],
            )
            observations = await job._interpret_traits_batch(
                source_entity_id='offline-evaluation', source_entity_alias='Frozen evaluation',
                identity={'alias': case['target'], 'identity_description': case.get('identity_description') or {}},
                perspectives=[perspective], scene_contexts=[{
                    'position': 1, 'scene': {'name': case['scene_id'], 'description': case['scene']},
                }],
            )
            output.append({'case_id': case['case_id'], 'observations': observations[0]})
            calls += len(job.llm_calls)
            tokens += sum(call.total_tokens_est for call in job.llm_calls)
    finally:
        runtime.TRAIT_INTERPRETATION_PROMPT = original_prompt
        await client.aclose()
    return output, {'prompt_version': PROMPT_VERSION if not prompt_file else 'custom-baseline',
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                    'model': settings.model_character_agent_scene_interpretation.model_dump(mode='json'),
                    'llm_calls': calls, 'total_tokens_est': tokens}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixtures', type=Path)
    parser.add_argument('report', type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--outputs', type=Path, help='Offline captured case_id/observations JSON array')
    source.add_argument('--live', action='store_true', help='Call configured extraction model; never persist profiles')
    parser.add_argument('--prompt-file', type=Path, help='Complete baseline extraction prompt including trait contract')
    args = parser.parse_args()
    if args.prompt_file and not args.live:
        parser.error('--prompt-file requires --live')
    fixture_bytes = args.fixtures.read_bytes()
    fixture = json.loads(fixture_bytes)
    outputs, provenance = (asyncio.run(replay_cases(fixture['cases'], args.prompt_file)) if args.live else
                           (json.loads(args.outputs.read_text()), {'mode': 'captured-outputs'}))
    report = score_cases(fixture['cases'], outputs)
    report['provenance'] = {**provenance, 'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
                            'fixture_version': fixture['version']}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
