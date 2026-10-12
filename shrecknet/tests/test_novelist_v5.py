"""V5 passage resolution, global planning, bounded fallback, and beat writing."""
from __future__ import annotations
import json
import pytest
from novelist_test_support import candidate_payload, directed_payload, director_selection
from pydantic import ValidationError
from app.core.config_store import Settings
from app.integrations.llm.model_policy import ModelPolicy
from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.context_budget import fits_context, input_cost
from app.jobs.novelist.orchestrator import NovelistOrchestrator
from app.jobs.novelist.prose_quality import validate_prose_text
from app.jobs.novelist.source_interpreter import interpret_source, segment_source
from app.jobs.novelist.story_plan import (
    EVIDENCE_INVENTORY_JSON_SCHEMA as INVENTORY_JSON_SCHEMA, CANDIDATE_JSON_SCHEMA as STORY_PLAN_JSON_SCHEMA, CandidateExtraction, DirectedStoryPlan, NarrativeStoryPlan,
    SourceRange, coverage_diagnostics, resolve_ranges,
)
from app.schemas.novelist import NovelistRunCreate, SourcePage


def plan(start='passage-000001', end=None):
    return candidate_payload({'title': 'Consequences', 'cast': [], 'continuity_notes': None, 'beats': [{
        'beat_id': 'beat-001', 'summary': 'A discovery changes the stakes.',
        'importance': 'major', 'source_ranges': [{'start_id': start, 'end_id': end or start}],
    }]})


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
    assert 'candidate developments' in calls[0][0]
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
            value = {'observations': [{'summary': 'A conflict creates consequences.', 'source_ranges': [{'start_id': window[0]['id'], 'end_id': window[-1]['id']}], 'material': 'fiction', 'confirmed_facts': [], 'unresolved_facts': []}], 'identities': []}
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
    value = CandidateExtraction.model_validate(plan(end='passage-000003'))
    blocks = plan_blocks(DirectedStoryPlan.model_validate(directed_payload(value.model_dump())), value, passages, max_source_bytes=60)
    assert len(blocks) == 3
    assert all(b.beat_ids == ['beat-001'] for b in blocks)
    assert [b.source_ids for b in blocks] == [[p.id] for p in passages]
    assert [b.is_final for b in blocks] == [False, False, True]
    assert coverage_diagnostics(value, passages)['unreferenced_passages'] == []
    with pytest.raises(ValueError, match='one source passage'):
        plan_blocks(DirectedStoryPlan.model_validate(directed_payload(value.model_dump())), value, passages, max_source_bytes=1)


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
async def test_writer_gets_only_assigned_passages_with_compact_outline_and_accepted_tail(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    value = plan()
    value['beats'].append({**plan('passage-000003')['beats'][0], 'beat_id': 'beat-002', 'importance': 'transition'})
    class WriterClient:
        calls = []
        async def chat(self, **request):
            self.calls.append(request)
            if request['usage_tag'] == 'novelist.analysis.director':
                text = json.dumps(director_selection(directed_payload(value)))
            elif request['usage_tag'].startswith('novelist.analysis'):
                text = json.dumps(value)
            else:
                text = ('Accepted literary prose. ' if len(self.calls) == 3 else 'Different ending follows. ') * 100
            return {'text': text, 'response_metadata': {'finish_reason': 'stop'}}
    client = WriterClient()
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    result = await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Arrival\n\nTable chatter\n\nUnresolved ending'))
    assert result['artifacts']['pipeline_version'] == 'v5'
    assert result['artifacts']['efficiency']['calls_missing_usage'] == len(client.calls)
    assert all(call['input_tokens'] is None for call in result['artifacts']['llm_calls'])
    writers = [call for call in client.calls if call['usage_tag'].startswith('novelist.writer')]
    assert len(writers) == 2
    first, second = [call['messages'][0]['content'] for call in writers]
    for prompt, expected in [(first, 'Arrival\n\n'), (second, 'Unresolved ending')]:
        selected = json.loads(prompt.split('supporting_source_segments=', 1)[1].split('\nprevious_accepted_tail=', 1)[0])
        assert [p['text'] for p in selected] == [expected]
        assert 'complete_story_plan=' not in prompt
        assert 'candidate_extraction=' not in prompt
        assert 'chapter_outline=' in prompt
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
        return {'observations': [{'summary': 's' * 600, 'source_ranges': [{'start_id': window[0]['id'], 'end_id': window[-1]['id']}]}] * 30, 'identities': []}
    with pytest.raises(ValueError, match='context budget'):
        await interpret_source(text='\n\n'.join('x' * 1500 for _ in range(40)), source_type='notes', language='', instructions='', call_json=call, context_tokens=32000)
    assert calls and all(tag == 'novelist.analysis.inventory' for tag in calls)


@pytest.mark.asyncio
async def test_writer_exhausted_retry_does_not_publish_partial_chapter(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    class BrokenWriter:
        async def chat(self, **request):
            return {'text': json.dumps(director_selection(directed_payload(plan())) if request['usage_tag'] == 'novelist.analysis.director' else plan()) if request['usage_tag'].startswith('novelist.analysis') else '', 'response_metadata': {'finish_reason': 'stop'}}
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
        'beat_id': f'beat-{index:03d}', 'summary': summary, 'importance': 'major', 'material': 'fiction', 'confirmed_facts': [], 'unresolved_facts': [],
        'source_ranges': [{'start_id': f'passage-{start:06d}', 'end_id': f'passage-{end:06d}'}],
    } for index, (summary, start, end) in enumerate(annotations, start=1)]
    result = CandidateExtraction.model_validate(value)
    diagnostics = coverage_diagnostics(result, passages)
    assert len(result.beats) == 9
    assert diagnostics['unreferenced_passages'] == ['passage-000002']
    assert len(diagnostics['beat_passages']['beat-005']) == 3
    assert diagnostics['beat_passages']['beat-009'] == [passages[-1].id]
    assert ''.join(p.text for p in passages) == text


def session9():
    from pathlib import Path
    return json.loads((Path(__file__).parent / 'fixtures/novelist/session9.json').read_text())


@pytest.mark.parametrize('mutation,match', [
    ('unknown', 'Every candidate beat'), ('duplicate', 'Every candidate beat'),
    ('reorder', 'chronology'), ('resolve_ambiguous', 'restriction'),
    ('invent_mapping', 'confirmed mappings'), ('drop_fact', 'confirmed_facts'),
    ('wrong_fact', 'unresolved_facts'), ('include_gameplay', 'Gameplay-only'),
    ('drop_mixed', 'fictional developments'), ('duplicate_arc', 'arc_id'),
])
def test_director_rejects_invalid_assignments_identities_and_fact_references(mutation, match):
    from app.jobs.novelist.story_plan import validate_directed_plan
    fixture = session9()
    candidate = CandidateExtraction.model_validate(fixture['candidate'])
    value = directed_payload(fixture['candidate'], fixture['arc_groups'])
    if mutation == 'unknown':
        value['arcs'][0]['beat_ids'][0] = 'unknown'
    elif mutation == 'duplicate':
        value['arcs'][0]['beat_ids'].append('beat-001')
    elif mutation == 'reorder':
        value['arcs'].reverse()
    elif mutation == 'resolve_ambiguous':
        value['identity_restrictions'] = value['identity_restrictions'][:1]
    elif mutation == 'invent_mapping':
        value['canonical_cast'].append({'player': 'Enrico', 'character': 'Asterius'})
    elif mutation == 'drop_fact':
        value['arcs'][0]['confirmed_facts'].pop()
    elif mutation == 'wrong_fact':
        value['arcs'][0]['unresolved_facts'][0]['fact_index'] = 99
    elif mutation == 'include_gameplay':
        value['excluded_beat_ids'] = []
        value['arcs'][2]['beat_ids'].insert(0, 'beat-007')
    elif mutation == 'drop_mixed':
        value['arcs'][0]['beat_ids'].remove('beat-001')
        value['excluded_beat_ids'].append('beat-001')
    else:
        value['arcs'][1]['arc_id'] = value['arcs'][0]['arc_id']
    with pytest.raises(ValueError, match=match):
        validate_directed_plan(DirectedStoryPlan.model_validate(value), candidate)


@pytest.mark.asyncio
async def test_director_repairs_invalid_output_without_seeing_original_passages(tmp_path, monkeypatch):
    from app.jobs.novelist.narrative_director import direct_story
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    fixture = session9()
    valid = director_selection(directed_payload(fixture['candidate'], fixture['arc_groups']))
    invalid = json.loads(json.dumps(valid))
    invalid['arcs'][0]['confirmed_facts'][0]['fact_index'] = 99
    client = Client([invalid, valid])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    directed = await direct_story(candidate=CandidateExtraction.model_validate(fixture['candidate']), instructions=fixture['instructions'], language='en', call_json=runner._json)
    assert len(directed.arcs) == 5
    assert len(client.calls) == 2
    for call in client.calls:
        prompt = call['messages'][0]['content']
        assert fixture['source_text'][:150] not in prompt
        assert 'source_segments=' not in prompt
        assert 'previous_accepted_tail=' not in prompt
        assert fixture['instructions'] in prompt
        assert call['model'] == runner.analysis_model
    assert client.calls[-1]['usage_tag'].endswith('.repair')


@pytest.mark.asyncio
async def test_session9_arc_pipeline_context_usage_and_accepted_only_continuity(tmp_path, monkeypatch):
    """Replays annotated evidence; it does not claim model discovery or semantic fidelity."""
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    fixture = session9()
    class ReplayClient:
        def __init__(self):
            self.calls = []
            self.writers = 0
        async def chat(self, **request):
            self.calls.append(request)
            tag = request['usage_tag']
            if tag == 'novelist.analysis.story_plan':
                text = json.dumps(fixture['candidate'])
            elif tag == 'novelist.analysis.director':
                text = json.dumps(director_selection(directed_payload(fixture['candidate'], fixture['arc_groups'])))
            else:
                self.writers += 1
                prompt = request['messages'][0]['content']
                assignment = json.loads(prompt.split('current_assignment=', 1)[1].split('\nsupporting_source_segments=', 1)[0])
                block_id = assignment['block_id']
                # Deliberately leak the GM once; that response must never advance continuity.
                text = ('Pablo REJECTED_RENDERING ' if self.writers == 1 else '') + (f'{block_id} proceeds through its own events. ' * 75)
            return {'text': text, 'usage': {'prompt_tokens': 123, 'completion_tokens': 456}, 'response_metadata': {'finish_reason': 'stop'}}
    client = ReplayClient()
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    result = await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text=fixture['source_text'], instructions=fixture['instructions'], language='en'))
    artifacts = result['artifacts']
    assert len(artifacts['directed_story_plan']['arcs']) == 5
    assert artifacts['output_summary']['block_count'] == 5
    assert artifacts['efficiency']['total_calls'] == 8  # 7 normal calls + deliberate writer retry.
    assert artifacts['efficiency']['retry_count'] == 1
    assert artifacts['efficiency']['calls_missing_usage'] == 0
    assert artifacts['efficiency']['reported_input_tokens'] == 8 * 123
    assert artifacts['efficiency']['reported_output_tokens'] == 8 * 456
    assert all(call['elapsed_ms'] >= 0 for call in artifacts['llm_calls'])
    assert artifacts['efficiency']['writer_prompt_bytes'] < fixture['baseline']['writer_prompt_bytes'] * .6
    writers = [call for call in client.calls if call['usage_tag'].startswith('novelist.writer')]
    for i, call in enumerate(writers):
        prompt = call['messages'][0]['content']
        assert 'candidate_extraction=' not in prompt and 'complete_story_plan=' not in prompt
        tail = prompt.split('previous_accepted_tail=', 1)[1].split('\nStage 3r', 1)[0]
        assert len(tail) <= 700
        assert 'REJECTED_RENDERING' not in tail
        assert fixture['source_text'] not in prompt
        assignment = json.loads(prompt.split('current_assignment=', 1)[1].split('\nsupporting_source_segments=', 1)[0])
        assert len(assignment['beat_ids']) < len(fixture['candidate']['beats'])
    assert 'Pablo' not in result['final_text_html']
    assert artifacts['story_plan']['beats'][0].keys() == {'beat_id', 'summary', 'importance', 'source_ranges'}
    assert artifacts['coverage']['semantic_coverage'] == 'not_independently_verified'


def test_arc_output_subdivisions_preserve_budget_source_and_order():
    fixture = session9()
    candidate = CandidateExtraction.model_validate(fixture['candidate'])
    directed = DirectedStoryPlan.model_validate(directed_payload(fixture['candidate'], [[beat.beat_id for beat in candidate.beats if beat.material != 'gameplay']]))
    segments = segment_source(fixture['source_text'])
    blocks = plan_blocks(directed, candidate, segments, target_words=4000)
    assert len(blocks) == 3
    assert all(block.arc_id == 'arc-001' and block.target_words <= 1400 for block in blocks)
    assert sum(block.target_words for block in blocks) == 4000
    assert [block.part_index for block in blocks] == [1, 2, 3]
    selected = {s.id for beat in candidate.beats if beat.material != 'gameplay' for s in resolve_ranges(beat.source_ranges, segments)}
    ids = [source_id for block in blocks for source_id in block.source_ids]
    assert len(ids) == len(set(ids)) and set(ids) == selected
    assert ids == [s.id for s in segments if s.id in selected]


@pytest.mark.parametrize('text,forbidden,canonical,expected', [
    ('Pietro walked into the hall.', ['Pietro'], ['Tamura'], True),
    ('Pietro walked into the hall.', ['Pietro'], ['Pietro'], False),
    ('Pietroson walked into the hall.', ['Pietro'], ['Tamura'], False),
    ('He reviewed the character sheet.', [], [], True),
    ('Calcolarono i punti gloria.', [], [], True),
    ('Ils lisent la fiche de personnage.', [], [], True),
    ('The dice of fate rolled toward glory.', [], [], False),
])
def test_contextual_checks_avoid_broad_vocabulary_and_name_collisions(text, forbidden, canonical, expected):
    from app.jobs.novelist.prose_quality import validate_contextual_prose
    assert bool(validate_contextual_prose(text, forbidden_names=forbidden, canonical_names=canonical, accepted_sections=[])) is expected


@pytest.mark.asyncio
async def test_director_failure_never_dispatches_writer(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    client = Client([plan(), {}, {}])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    with pytest.raises(RuntimeError, match='failed after one retry'):
        await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Source'))
    assert len(client.calls) == 3
    assert all(call['usage_tag'].startswith('novelist.analysis') for call in client.calls)
    assert json.loads((runner.debug.output_dir / 'manifest.json').read_text())['status'] == 'failed'


def test_new_structured_schemas_require_complete_objects_and_match_models():
    from jsonschema import validate
    from app.jobs.novelist.story_plan import CANDIDATE_JSON_SCHEMA, DIRECTED_PLAN_JSON_SCHEMA, DIRECTOR_SELECTION_JSON_SCHEMA, EVIDENCE_INVENTORY_JSON_SCHEMA
    fixture = session9()
    validate(fixture['candidate'], CANDIDATE_JSON_SCHEMA)
    validate(fixture['directed_plan'], DIRECTED_PLAN_JSON_SCHEMA)
    validate(director_selection(fixture['directed_plan']), DIRECTOR_SELECTION_JSON_SCHEMA)
    assert 'cast' not in CANDIDATE_JSON_SCHEMA['properties']
    assert 'cast' not in EVIDENCE_INVENTORY_JSON_SCHEMA['properties']
    assert 'canonical_cast' not in DIRECTOR_SELECTION_JSON_SCHEMA['properties']
    def check(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node.get('additionalProperties') is False
                assert set(node['required']) == set(node['properties'])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    for schema in (CANDIDATE_JSON_SCHEMA, DIRECTED_PLAN_JSON_SCHEMA, DIRECTOR_SELECTION_JSON_SCHEMA, EVIDENCE_INVENTORY_JSON_SCHEMA):
        check(schema)
    invalid = json.loads(json.dumps(fixture['candidate']))
    invalid['identities'][-1]['character'] = 'Asterius'
    with pytest.raises(ValueError, match='null character'):
        CandidateExtraction.model_validate(invalid)
    invalid = json.loads(json.dumps(fixture['candidate']))
    invalid['identities'].append(invalid['identities'][0])
    with pytest.raises(ValueError, match='unique player names'):
        CandidateExtraction.model_validate(invalid)


def test_repeated_narrative_paragraphs_within_unit_are_rejected():
    from app.jobs.novelist.prose_quality import validate_contextual_prose
    paragraph = 'The riders crossed the river beneath the empty winter sky. ' * 5
    errors = validate_contextual_prose(paragraph + '\n\n' + paragraph, forbidden_names=[], canonical_names=[], accepted_sections=[])
    assert 'narrative section repeats within the current unit' in errors


@pytest.mark.asyncio
async def test_extraction_repairs_wrong_mapping_against_explicit_user_instructions(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    valid = plan()
    valid['identities'] = [{'player': 'Enrico America', 'character': None, 'status': 'ambiguous'}]
    invalid = json.loads(json.dumps(valid))
    invalid['identities'][0].update(status='confirmed', character='Asterius')
    client = Client([invalid, valid])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    candidate, _ = await interpret_source(text='A combatant falls.', source_type='notes', language='en', instructions='Enrico is Cwenhild or Asterius', call_json=runner._json)
    assert candidate.identities[0].character is None
    assert len(client.calls) == 2
    assert 'explicit user mapping' in client.calls[1]['messages'][0]['content']


def test_identity_instruction_validation_ignores_style_prose_and_requires_missing_mappings():
    from app.jobs.novelist.story_plan import validate_instruction_identities
    candidate = CandidateExtraction.model_validate(plan())
    validate_instruction_identities(candidate, 'There is a lot of spelling to fix.\nKeep the story in the present tense.')
    with pytest.raises(ValueError, match='Pietro'):
        validate_instruction_identities(candidate, 'Pietro plays Tamura. Do not refer to him as Pietro')


@pytest.mark.asyncio
@pytest.mark.parametrize('response_index', [0, 1])
async def test_recorded_identity_conflict_is_eliminated_at_analysis_boundaries(tmp_path, monkeypatch, response_index):
    from pathlib import Path
    from app.jobs.novelist.narrative_director import direct_story
    from app.jobs.novelist.story_plan import EvidenceInventory
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    recorded = json.loads((Path(__file__).parent / 'fixtures/novelist/identity_conflict.json').read_text())['responses'][response_index]
    value = plan()
    value['identities'] = recorded['identities']
    # Replay the evidence the failed model already got right under the new contract.
    instructions = 'Pablo is the DM\nPietro is Tamura\nEnrico is Cwenhild or Asterius\nLaura is Lynelle'
    client = Client([value, director_selection(directed_payload(value))])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    candidate, _ = await interpret_source(text='Source', source_type='notes', language='en', instructions=instructions, call_json=runner._json)
    directed = await direct_story(candidate=candidate, instructions=instructions, language='en', call_json=runner._json)
    expected = [{'player': 'Pietro', 'character': 'Tamura'}, {'player': 'Laura', 'character': 'Lynelle'}]
    assert candidate.legacy_plan().model_dump()['cast'] == expected
    assert [member.model_dump() for member in directed.canonical_cast] == expected
    assert {item.player: item.status for item in directed.identity_restrictions} == {'Enrico': 'ambiguous', 'Pablo': 'gm'}
    inventory = EvidenceInventory.model_validate({'identities': recorded['identities'], 'observations': [{key: value['beats'][0][key] for key in ('summary', 'source_ranges', 'material', 'confirmed_facts', 'unresolved_facts')}]})
    assert inventory.cast == candidate.cast
    assert len(client.calls) == 2
    assert all(not call['usage_tag'].endswith('.repair') for call in client.calls)
    # Redundant legacy model output is forbidden rather than silently trusted.
    with pytest.raises(ValueError, match='Extra inputs'):
        CandidateExtraction.model_validate(dict(value, cast=recorded['cast']))
