"""Regression coverage for successful and failed Novelist debug traces."""

import json
from pathlib import Path

import pytest
from novelist_test_support import candidate_payload, directed_payload, director_selection

from app.integrations.llm.model_policy import ModelPolicy
from app.jobs.novelist.debug_artifacts import NovelistDebugArtifacts
from app.jobs.novelist.orchestrator import NovelistOrchestrator
from app.schemas.novelist import NovelistRunCreate


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def snapshots(directory):
    return [json.loads(path.read_text()) for path in sorted(directory.glob('[0-9]*.json'))]


@pytest.mark.asyncio
async def test_truncated_run_keeps_exact_prompts_responses_and_errors(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    rejected = '{"title":"Unfinished story'
    client = Client([{'text': rejected, 'response_metadata': {'finish_reason': 'length'}}] * 2)
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    with pytest.raises(RuntimeError, match='truncated'):
        await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Original source.'), conversation_id='novelist_run:123')
    directory = runner.debug.output_dir
    assert directory.parent == tmp_path / 'local_tests' / 'novelist'
    files = snapshots(directory)
    requests = [item['request'] for item in files if item['step'].endswith('.request')]
    responses = [item['response'] for item in files if item['step'].endswith('.response')]
    assert len(requests) == len(responses) == 2
    for request, call in zip(requests, client.calls):
        assert request['messages'] == call['messages']
        assert request['response_format'] == call['response_format']
        assert request['max_tokens'] == 6000
    assert 'Original source.' in requests[0]['messages'][0]['content']
    assert rejected in requests[1]['messages'][0]['content']
    assert all(item['text'] == rejected for item in responses)
    validations = [item for item in files if item['step'].endswith('.validation')]
    assert len(validations) == 2
    assert all(not item['accepted'] and 'truncated' in item['error'] for item in validations)
    manifest = json.loads((directory / 'manifest.json').read_text())
    assert manifest['status'] == 'failed'
    assert manifest['stage'] == 'interpretation'
    assert manifest['run_id'] == 'novelist_run:123'
    assert manifest['files'] == runner.debug.files
    assert files[-1]['traceback']


@pytest.mark.asyncio
async def test_provider_exception_keeps_request_and_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    runner = NovelistOrchestrator(llm_client=Client([RuntimeError('provider unavailable')]), model_policy=ModelPolicy())
    with pytest.raises(RuntimeError, match='provider unavailable'):
        await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Source'))
    files = snapshots(runner.debug.output_dir)
    assert any(item['step'].endswith('.request') for item in files)
    assert any(item['step'].endswith('.error') for item in files)
    assert json.loads((runner.debug.output_dir / 'manifest.json').read_text())['status'] == 'failed'


@pytest.mark.asyncio
async def test_success_records_writer_retry_and_final_html(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    plan = {'title': 'Arrival', 'cast': [], 'beats': [{'beat_id': 'beat-001', 'summary': 'Arrival', 'importance': 'major', 'source_ranges': [{'start_id': 'passage-000001', 'end_id': 'passage-000001'}]}], 'continuity_notes': None}
    plan = candidate_payload(plan)
    client = Client([
        {'text': json.dumps(plan), 'response_metadata': {'finish_reason': 'stop'}},
        {'text': json.dumps(director_selection(directed_payload(plan))), 'response_metadata': {'finish_reason': 'stop'}},
        {'text': 'Truncated prose', 'response_metadata': {'finish_reason': 'length'}},
        {'text': 'word ' * 300, 'response_metadata': {'finish_reason': 'stop'}},
    ])
    runner = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    result = await runner.execute(agent=None, payload=NovelistRunCreate(unstructured_text='Arrival', previous_session_text='Prior text'))
    files = snapshots(runner.debug.output_dir)
    assert files[0]['payload']['previous_session_text'] == 'Prior text'
    requests = [item['request'] for item in files if item['step'].endswith('.request')]
    assert len(requests) == 4
    assert 'response_format' not in requests[2]
    assert 'Prior text' in requests[2]['messages'][0]['content']
    assert requests[3]['usage_tag'] == 'novelist.writer.section_retry'
    assert [item['accepted'] for item in files if item['step'] == 'writer.validation'] == [False, True]
    assert files[-1]['result']['final_text_html'] == result['final_text_html']
    assert json.loads((runner.debug.output_dir / 'manifest.json').read_text())['status'] == 'completed'


def test_unwritable_storage_is_non_fatal(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError('read only')
    monkeypatch.setattr(Path, 'mkdir', denied)
    assert NovelistDebugArtifacts.create('../../outside').output_dir is None
    debug = NovelistDebugArtifacts(tmp_path)
    monkeypatch.setattr(Path, 'write_text', denied)
    debug.write('input', payload='text')
    debug.manifest(status='failed')
    assert debug.files == []


def test_run_directories_are_isolated_and_ids_cannot_escape(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    first = NovelistDebugArtifacts.create('../../outside')
    second = NovelistDebugArtifacts.create('../../outside')
    assert first.output_dir != second.output_dir
    assert first.output_dir.parent == second.output_dir.parent == tmp_path / 'local_tests' / 'novelist'


@pytest.mark.asyncio
async def test_stage_callback_failure_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    runner = NovelistOrchestrator(llm_client=Client([]), model_policy=ModelPolicy())
    observed = []

    async def callback(stage, data):
        observed.append(stage.value)
        assert data['artifacts']['pipeline_version'] == 'v5'
        raise RuntimeError('stage persistence failed')

    with pytest.raises(RuntimeError, match='stage persistence failed'):
        await runner.execute(
            agent=None, payload=NovelistRunCreate(unstructured_text='Source'),
            stage_callback=callback,
        )
    assert observed == ['ingest']
    manifest = json.loads((runner.debug.output_dir / 'manifest.json').read_text())
    assert manifest['status'] == 'failed'
    assert manifest['stage'] == 'ingest'
    assert manifest['error'] == 'stage persistence failed'
