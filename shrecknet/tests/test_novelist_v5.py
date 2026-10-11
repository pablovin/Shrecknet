"""V5 passage resolution, global planning, bounded fallback, and beat writing."""
from __future__ import annotations
import json
import pytest
from pydantic import ValidationError
from app.core.config_store import Settings
from app.integrations.llm.model_policy import ModelPolicy
from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.context_budget import fits_context, input_cost
from app.jobs.novelist.orchestrator import NovelistOrchestrator
from app.jobs.novelist.prose_quality import validate_prose_text
from app.jobs.novelist.source_interpreter import interpret_source, segment_source
from app.jobs.novelist.story_plan import (
    INVENTORY_JSON_SCHEMA, STORY_PLAN_JSON_SCHEMA, NarrativeStoryPlan,
    SourceRange, coverage_diagnostics, resolve_ranges,
)
from app.schemas.novelist import NovelistRunCreate, SourcePage


def plan(start='passage-000001', end=None):
    return {'title': 'Consequences', 'cast': [], 'continuity_notes': None, 'beats': [{
        'beat_id': 'beat-001', 'summary': 'A discovery changes the stakes.',
        'importance': 'major', 'source_ranges': [{'start_id': start, 'end_id': end or start}],
    }]}


class Client:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    async def chat(self, **request):
        self.calls.append(request)
        return {'text': json.dumps(next(self.values)), 'response_metadata': {'finish_reason': 'stop'}}


@pytest.mark.parametrize('text', ['First.\n\nSecond.\n\n', '  Leading\n\n\nText\n', '長い物語 ' * 500, 'x' * 7000])
def test_passage_index_preserves_every_character_and_offsets(text):
    passages = segment_source(text, max_chars=80)
    assert ''.join(p.text for p in passages) == text
    assert all(p.text == text[p.start:p.end] and len(p.text) <= 80 for p in passages)
    assert passages == segment_source(text, max_chars=80)
    assert [p.id for p in passages] == [f'passage-{i:06d}' for i in range(1, len(passages) + 1)]


def test_pages_map_to_original_nonempty_page_numbers():
    passages = segment_source('First\n\nLast', pages=[SourcePage(page=1, start=0, end=5), SourcePage(page=3, start=7, end=11)])
    assert [p.pages for p in passages] == [[1], [3]]


def test_noncontiguous_ranges_deduplicate_shared_evidence_in_source_order():
    passages = segment_source('One\n\nTwo\n\nThree\n\nFour')
    spans = [SourceRange(start_id=passages[2].id, end_id=passages[3].id), SourceRange(start_id=passages[0].id, end_id=passages[2].id)]
    assert resolve_ranges(spans, passages) == passages
    spans = [SourceRange(start_id=passages[0].id, end_id=passages[0].id), SourceRange(start_id=passages[3].id, end_id=passages[3].id)]
    assert resolve_ranges(spans, passages) == [passages[0], passages[3]]


@pytest.mark.parametrize('ranges,match', [
    ([{'start_id': 'missing', 'end_id': 'passage-000001'}], 'Unknown'),
    ([{'start_id': 'passage-000002', 'end_id': 'passage-000001'}], 'Reversed'),
    ([{'start_id': 'passage-000001', 'end_id': 'passage-000001'}] * 2, 'Duplicate'),
])
def test_invalid_ranges_fail(ranges, match):
    with pytest.raises(ValueError, match=match):
        resolve_ranges([SourceRange(**span) for span in ranges], segment_source('One\n\nTwo'))


@pytest.mark.asyncio
async def test_more_than_four_passages_still_get_one_global_read():
    text = '\n\n'.join(f'Paragraph {i}' for i in range(20))
    calls = []
    async def call(prompt, schema, tag, *, validator):
        calls.append((prompt, tag))
        value = plan(end='passage-000020')
        validator(value)
        return value
    result, passages = await interpret_source(text=text, source_type='notes', language='en', instructions='', call_json=call)
    assert len(calls) == 1
    assert json.loads(calls[0][0].split('source_segments=', 1)[1]) == [p.model_dump() for p in passages]
    assert 'approximately 8–10 beats' in calls[0][0]
    assert 'final fictional development' in calls[0][0]
    assert len(result.beats) == 1  # A soft editorial target, never a hard quota.


@pytest.mark.asyncio
async def test_oversized_source_gets_factual_windows_then_one_global_plan():
    text = '\n\n'.join(f'Event {i}: ' + 'x' * 1500 for i in range(40))
    calls = []
    async def call(prompt, schema, tag, *, validator):
        assert fits_context(prompt, schema, context_tokens=32000, output_tokens=6000)
        calls.append((prompt, schema, tag))
        if schema == INVENTORY_JSON_SCHEMA:
            window = json.loads(prompt.split('source_segments=', 1)[1])
            value = {'observations': [{'summary': 'A conflict creates consequences.', 'source_ranges': [{'start_id': window[0]['id'], 'end_id': window[-1]['id']}]}], 'cast': []}
        else:
            value = plan(end='passage-000040')
        validator(value)
        return value
    result, passages = await interpret_source(text=text, source_type='transcript', language='fr', instructions='Preserve ambiguity', call_json=call, context_tokens=32000)
    assert len(calls) > 2
    assert all(schema == INVENTORY_JSON_SCHEMA for _, schema, _ in calls[:-1])
    assert calls[-1][2] == 'novelist.analysis.story_plan_reconcile'
    assert 'source_segments=' not in calls[-1][0]
    assert 'Preserve ambiguity' in calls[-1][0]
    assert len(resolve_ranges(result.beats[0].source_ranges, passages)) == 40


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['unknown', 'reverse', 'duplicate_beat'])
async def test_semantic_reference_errors_use_the_bounded_repair(bad, tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    rejected = plan()
    if bad == 'unknown':
        rejected['beats'][0]['source_ranges'][0]['start_id'] = 'unknown'
    elif bad == 'reverse':
        rejected = plan('passage-000002', 'passage-000001')
    else:
        rejected['beats'] *= 2
    client = Client([rejected, plan()])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    result, _ = await interpret_source(text='Arrival\n\nDiscovery', source_type='notes', language='', instructions='', call_json=runner._json)
    assert result.beats[0].beat_id == 'beat-001'
    assert len(client.calls) == 2
    assert client.calls[1]['usage_tag'].endswith('.repair')


def test_beat_units_keep_identity_and_split_only_for_context():
    passages = segment_source('One\n\nTwo\n\nThree')
    value = NarrativeStoryPlan.model_validate(plan(end='passage-000003'))
    blocks = plan_blocks(value.beats, passages, max_source_bytes=160)
    assert len(blocks) == 3
    assert all(b.beat_ids == ['beat-001'] for b in blocks)
    assert [b.source_ids for b in blocks] == [[p.id] for p in passages]
    assert [b.is_final for b in blocks] == [False, False, True]
    assert coverage_diagnostics(value, passages)['unreferenced_passages'] == []
    with pytest.raises(ValueError, match='one source passage'):
        plan_blocks(value.beats, passages, max_source_bytes=1)


def test_short_transition_is_not_forced_to_major_beat_length():
    assert validate_prose_text('word ' * 75, target_words=200, finish_reason='stop') == []
    assert validate_prose_text('word ' * 75, target_words=700, finish_reason='stop')


def test_context_preflight_counts_utf8_schema_and_output_reservation():
    assert input_cost('字') > input_cost('a')
    assert input_cost('a', STORY_PLAN_JSON_SCHEMA) > input_cost('a')
    assert not fits_context('x' * 1000, None, context_tokens=2000, output_tokens=1000)
    with pytest.raises(ValidationError):
        Settings(novelist_analysis_context_tokens=1000)


@pytest.mark.asyncio
async def test_writer_gets_only_assigned_passages_with_full_plan_and_previous_prose(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    value = plan()
    value['beats'].append({**plan('passage-000003')['beats'][0], 'beat_id': 'beat-002', 'importance': 'transition'})
    class WriterClient:
        calls = []
        async def chat(self, **request):
            self.calls.append(request)
            if request['usage_tag'].startswith('novelist.analysis'):
                text = json.dumps(value)
            else:
                text = 'Accepted literary prose. ' * 100
            return {'text': text, 'response_metadata': {'finish_reason': 'stop'}}
    client = WriterClient()
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    result = await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Arrival\n\nTable chatter\n\nUnresolved ending'))
    assert result['artifacts']['pipeline_version'] == 'v5'
    writers = [call for call in client.calls if call['usage_tag'].startswith('novelist.writer')]
    assert len(writers) == 2
    first, second = [call['messages'][0]['content'] for call in writers]
    for prompt, expected in [(first, 'Arrival\n\n'), (second, 'Unresolved ending')]:
        selected = json.loads(prompt.split('supporting_source_segments=', 1)[1].split('\ncontinuity=', 1)[0])
        assert [p['text'] for p in selected] == [expected]
        assert 'complete_story_plan=' in prompt
    assert 'Accepted literary prose.' in second
    assert result['artifacts']['coverage']['unreferenced_passages'] == ['passage-000002']
    assert result['final_text_html'].startswith('<h1>Consequences</h1>')


@pytest.mark.asyncio
async def test_over_budget_synthesis_fails_without_sending_it():
    calls = []
    async def call(prompt, schema, tag, *, validator):
        calls.append(tag)
        window = json.loads(prompt.split('source_segments=', 1)[1])
        # Deliberately verbose but structurally valid inventories exceed synthesis capacity.
        return {'observations': [{'summary': 's' * 600, 'source_ranges': [{'start_id': window[0]['id'], 'end_id': window[-1]['id']}]}] * 30, 'cast': []}
    with pytest.raises(ValueError, match='context budget'):
        await interpret_source(text='\n\n'.join('x' * 1500 for _ in range(40)), source_type='notes', language='', instructions='', call_json=call, context_tokens=32000)
    assert calls and all(tag == 'novelist.analysis.inventory' for tag in calls)


@pytest.mark.asyncio
async def test_writer_exhausted_retry_does_not_publish_partial_chapter(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    class BrokenWriter:
        async def chat(self, **request):
            return {'text': json.dumps(plan()) if request['usage_tag'].startswith('novelist.analysis') else '', 'response_metadata': {'finish_reason': 'stop'}}
    runner = NovelistOrchestrator(llm_client=BrokenWriter(), model_policy=ModelPolicy())
    with pytest.raises(RuntimeError, match='remained unusable'):
        await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Source'))
    manifest = json.loads((runner.debug.output_dir / 'manifest.json').read_text())
    assert manifest['status'] == 'failed'
    assert manifest['stage'] == 'writing'
    assert not any('result.json' in name for name in manifest['files'])


@pytest.mark.asyncio
async def test_context_failure_keeps_generated_prompt_without_provider_call(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    from app.jobs.novelist.debug_artifacts import NovelistDebugArtifacts
    client = Client([])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy(), analysis_context_tokens=24000)
    runner.debug = NovelistDebugArtifacts.create('oversized')
    oversized = 'generated prompt ' * 3000
    with pytest.raises(ValueError, match='context budget'):
        await runner._json(oversized, STORY_PLAN_JSON_SCHEMA, 'novelist.analysis.story_plan')
    assert client.calls == []
    requests = [json.loads(path.read_text()) for path in runner.debug.output_dir.glob('*_request.json')]
    assert requests[0]['request']['messages'][0]['content'] == oversized
    assert list(runner.debug.output_dir.glob('*_error.json'))


@pytest.mark.asyncio
async def test_upload_page_metadata_is_persisted_without_changing_request_payload(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.api.routers import novelist as router
    page = SourcePage(page=2, start=0, end=6)
    run = SimpleNamespace(id='run-id')
    service = SimpleNamespace(
        resolve_and_save_preferences=AsyncMock(return_value=('en', '')),
        create_run=AsyncMock(return_value=run), get_run=AsyncMock(return_value=run),
    )
    queued = []
    monkeypatch.setattr(router, 'get_settings', lambda: SimpleNamespace(celery_expires_novelist_seconds=60))
    monkeypatch.setattr(router.generate_draft, 'apply_async', lambda **kwargs: queued.append(kwargs))
    monkeypatch.setattr(router.NovelistRunRead, 'model_validate', lambda value: value)
    await router._create_and_queue_run(
        agent_id='agent', payload=NovelistRunCreate(unstructured_text='Source'),
        current_user=SimpleNamespace(id='user'), session=None, service=service,
        source_pages=[page],
    )
    saved = service.create_run.call_args.kwargs
    assert saved['settings']['source_pages'] == [page.model_dump()]
    assert 'source_pages' not in saved['request_payload']
    assert 'source_pages' not in queued[0]['kwargs']['request_payload']


def test_annotated_session_preserves_major_turns_and_groups_combat():
    """A human-authored fixture for regression and later live model evaluation.

    This validates indexing/resolution of the annotated reference plan, not the
    model's ability to discover its nine dramatic developments independently.
    """
    from pathlib import Path
    text = (Path(__file__).parent / 'fixtures' / 'novelist' / 'session.txt').read_text()
    passages = segment_source(text)
    annotations = [
        ('Arrival creates a rescue obligation.', 1, 1),
        ('The royal seal turns the search into a political investigation.', 3, 3),
        ('The collector admits hiring smugglers.', 4, 4),
        ('Mira chooses pursuit over immediate arrest.', 5, 5),
        ('A bridge ambush ends in victory at the cost of Dain being wounded.', 6, 8),
        ('The prisoner reveals that the supposed abduction concealed a voluntary escape.', 9, 9),
        ('Mira commits to protecting the witness despite the political risk.', 10, 10),
        ('The ferryman stays behind so the witness can escape the soldiers.', 11, 11),
        ('The travelers reach safety while the ferryman’s fate remains unknown.', 12, 12),
    ]
    value = plan()
    value['beats'] = [{
        'beat_id': f'beat-{index:03d}', 'summary': summary, 'importance': 'major',
        'source_ranges': [{'start_id': f'passage-{start:06d}', 'end_id': f'passage-{end:06d}'}],
    } for index, (summary, start, end) in enumerate(annotations, start=1)]
    result = NarrativeStoryPlan.model_validate(value)
    diagnostics = coverage_diagnostics(result, passages)
    assert len(result.beats) == 9
    assert diagnostics['unreferenced_passages'] == ['passage-000002']
    assert len(diagnostics['beat_passages']['beat-005']) == 3
    assert diagnostics['beat_passages']['beat-009'] == [passages[-1].id]
    assert ''.join(p.text for p in passages) == text
