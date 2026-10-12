"""Compare recorded Session 9 baseline with annotated contexts or a real new run.

Run from shrecknet/: PYTHONPATH=. ../.venv/bin/python scripts/evaluate_novelist_v5.py
Pass --result /path/to/numbered_result.json to compare actual new provider metrics.
Offline projection never dispatches a provider or asserts semantic quality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.prompts import build_writer_prompt
from app.jobs.novelist.source_interpreter import segment_source
from app.jobs.novelist.story_plan import CandidateExtraction, DirectedStoryPlan, validate_directed_plan


def compare(fixture: dict, result: dict | None = None) -> dict:
    candidate = CandidateExtraction.model_validate(fixture['candidate'])
    directed = validate_directed_plan(DirectedStoryPlan.model_validate(fixture['directed_plan']), candidate)
    segments = segment_source(fixture['source_text'])
    blocks = plan_blocks(directed, candidate, segments)
    prompt_bytes = 0
    for i, block in enumerate(blocks):
        selected = [segment for segment in segments if segment.id in block.source_ids]
        prompt = build_writer_prompt(
            plan=directed, block=block.as_prompt_dict(), source_segments=selected,
            language=fixture['language'], instructions=fixture['instructions'],
            continuity={'prior_context': '', 'authority': 'lower_than_current_source'},
            previous_tail='x' * 700 if i else '',
        )
        prompt_bytes += len(prompt.encode('utf-8'))
    after = {
        'mode': 'annotated_context_projection', 'normal_calls': 2 + len(blocks),
        'writing_units': len(blocks), 'writer_prompt_bytes': prompt_bytes,
        'chapter_target_words': sum(block.target_words for block in blocks),
        'tail_assumption': '700 ASCII characters after the first unit',
        'input_tokens': None, 'output_tokens': None, 'elapsed_ms': None,
        'narrative_quality': 'Live model generation and human review pending.',
    }
    if result is not None:
        artifacts = result['artifacts']
        inputs = artifacts['inputs']
        if inputs.get('unstructured_text') != fixture['source_text'] or inputs.get('instructions') != fixture['instructions'] or inputs.get('language') != fixture['language']:
            raise ValueError('New run must use the same Session 9 source, instructions and language')
        efficiency = artifacts['efficiency']
        after = {
            'mode': 'recorded_new_run', **efficiency,
            'writer_prompt_bytes': efficiency['writer_prompt_bytes'],
            'elapsed_ms': artifacts['timings_ms']['total'],
            'input_tokens': efficiency['reported_input_tokens'] if not efficiency['calls_missing_usage'] else None,
            'output_tokens': efficiency['reported_output_tokens'] if not efficiency['calls_missing_usage'] else None,
            'output_words': sum(len(block['prose'].split()) for block in artifacts['blocks']),
            'models_match_baseline': all(call['model'] == fixture['baseline']['models'] for call in artifacts['llm_calls']),
            'narrative_quality': 'Requires human review against Session 9 annotations.',
        }
    return {
        'provenance': fixture['provenance'], 'baseline': fixture['baseline'], 'after': after,
        'writer_prompt_byte_reduction_percent': round(100 * (1 - after['writer_prompt_bytes'] / fixture['baseline']['writer_prompt_bytes']), 2),
        'limitations': 'Projection uses annotated extraction/arc groups, not model-discovered plans. Token or latency savings require a real new run; prose fidelity is not independently verified.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/fixtures/novelist/session9.json')
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    result = json.loads(args.result.read_text()) if args.result else None
    if result and 'result' in result:
        result = result['result']
    print(json.dumps(compare(fixture, result), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
