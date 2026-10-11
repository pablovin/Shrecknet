"""Source boundaries must propagate complete identity and survive partial failure."""
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config_store import Settings
from app.jobs.character_agent.embody_agent import EmbodimentGenerationError
from app.schemas.character_agent import IdentityDescription
from app.schemas.character_traits import TraitProfile
from app.services.character_embodiment_service import CharacterEmbodimentService
from app.services.character_agent_service import CharacterAgentService
from app.tasks import character_embodiment as task
from test_character_embodiment import BatchLLM, _canonical, scenes


def identity(number):
    return dict(identity_summary=f"Identity {number}", psychological_summary=f"Psychology {number}",
                personality_traits=[dict(trait="integrity", description=f"Integrity {number}")])


class EvolutionLLM(BatchLLM):
    def __init__(self, fail_at=None):
        super().__init__()
        self.syntheses = 0
        self.fail_at = fail_at

    async def aclose(self):
        pass

    async def chat(self, **kwargs):
        if kwargs['usage_tag'].endswith('identity_description'):
            self.calls.append(kwargs)
            self.syntheses += 1
            if self.syntheses == self.fail_at:
                raise TimeoutError('identity synthesis failed')
            return json.dumps(identity(self.syntheses))
        return await super().chat(**kwargs)


def inputs(first_size=6, existing=True):
    values = [s.model_dump(mode='json') for s in scenes(first_size + 1)]
    return dict(agent_id='a', processed_scene_ids=set(), scenes=values,
        source_groups=[dict(source_id='source-a', source_alias='A', scenes=values[:-1]),
                       dict(source_id='source-b', source_alias='B', scenes=values[-1:])],
        trait_profile=TraitProfile(), trait_evidence=[], current_aspects=[], current_goals=[],
        latest_revision=0, canonical_identity=_canonical(),
        identity_description=identity(0) if existing else None, source_entity_alias='Mara')


def payloads(llm, suffix):
    return [json.loads(call['messages'][1]['content']) for call in llm.calls
            if call['usage_tag'].endswith(suffix)]


def assert_evolution(llm, first_size=6):
    perspectives = payloads(llm, 'character_incorporation')
    first_chunks = (first_size + 4) // 5
    assert [len(p['scenes']) for p in perspectives] == ([5, 1, 1] if first_size == 6 else [1, 1])
    assert all(p['identity']['identity_description'] == identity(0) for p in perspectives[:first_chunks])
    assert perspectives[-1]['identity']['identity_description'] == identity(1)
    psychology = payloads(llm, 'scene_interpretation')
    assert psychology[-1]['identity_description'] == identity(1)
    traits = payloads(llm, 'trait_interpretation')
    assert traits[-1]['target']['identity_summary'] == 'Identity 1'
    assert traits[-1]['target']['psychological_summary'] == 'Psychology 1'
    synthesis = payloads(llm, 'identity_description')
    assert len(synthesis) == 2
    assert synthesis[-1]['previous_identity_description']['description'] == identity(1)
    integrity = next(t for t in synthesis[-1]['traits'] if t['name'] == 'integrity')
    assert integrity['observation_count'] == first_size + 1
    assert synthesis[-1]['steadiness']['observation_count'] > 0
    assert len(synthesis[0]['grounded_perspectives']) <= first_size


@pytest.fixture
def draft_run(monkeypatch):
    data = inputs()
    draft = SimpleNamespace(source_entity_id='e1', ontology_id=1, generation_revision=1,
                            generation_checkpoints=None, status=None, error_message=None)
    sql = SimpleNamespace(get=AsyncMock(return_value=draft), commit=AsyncMock(), refresh=AsyncMock())

    @asynccontextmanager
    async def sql_context():
        yield sql

    @asynccontextmanager
    async def graph_context(**kwargs):
        yield None

    monkeypatch.setattr(task, 'AsyncSessionMaker', sql_context)
    monkeypatch.setattr(task, 'get_driver', lambda: SimpleNamespace(session=graph_context))
    monkeypatch.setattr(task, 'get_settings', lambda: Settings(character_agent_embodiment_debug_artifacts_enabled=False))
    # Load fresh inputs for every attempt, as the real service does.
    monkeypatch.setattr(CharacterEmbodimentService, 'load_embodiment_input',
                        AsyncMock(side_effect=lambda **kwargs: copy.deepcopy(data)))
    monkeypatch.setattr(task, 'update_job_progress', AsyncMock())

    async def run(llm):
        monkeypatch.setattr(task, 'ShreckLLMClient', lambda **kwargs: llm)
        return await task._generate(draft_id='draft', revision=1, job_id=1)

    return data, draft, run


@pytest.mark.asyncio
async def test_draft_propagates_full_identity_and_reuses_completed_sources(draft_run):
    data, draft, run = draft_run
    llm = EvolutionLLM()
    result = await run(llm)
    assert result['status'] == 'ready'
    assert_evolution(llm)
    assert json.loads(draft.generated_proposal)['identity_description'] == identity(2)
    timeline = json.loads(draft.timeline_projection)
    assert len(timeline['revisions']) == 3
    assert result['llm_calls'] == len(llm.calls)
    cached = EvolutionLLM()
    await run(cached)
    assert cached.calls == []


@pytest.mark.asyncio
async def test_failed_synthesis_keeps_source_analysis_and_reuses_prior_transition(draft_run):
    data, draft, run = draft_run
    failed = EvolutionLLM(fail_at=2)
    with pytest.raises(EmbodimentGenerationError):
        await run(failed)
    checkpoints = json.loads(draft.generation_checkpoints)
    assert 'completed' in checkpoints['source:0']
    assert 'completed' not in checkpoints['source:1']
    assert draft.status != 'ready'
    retry = EvolutionLLM()
    await run(retry)
    assert len(retry.calls) == 1
    assert retry.calls[0]['usage_tag'].endswith('identity_description')
    p = payloads(retry, 'identity_description')[0]
    assert p['previous_identity_description']['description'] == identity(1)
    assert next(t for t in p['traits'] if t['name'] == 'integrity')['observation_count'] == 7


@pytest.mark.asyncio
async def test_initial_identity_checkpoint_and_upstream_invalidation(draft_run):
    data, draft, run = draft_run
    data['identity_description'] = None
    # Initial synthesis succeeds, source A synthesis fails.
    with pytest.raises(EmbodimentGenerationError):
        await run(EvolutionLLM(fail_at=2))
    retry = EvolutionLLM()
    await run(retry)
    assert retry.syntheses == 2  # No repeated initial synthesis.
    data['source_groups'][0]['scenes'][0]['description'] = 'A changed canonical choice.'
    changed = EvolutionLLM()
    await run(changed)
    assert changed.syntheses == 2
    assert len(payloads(changed, 'character_incorporation')) == 3


class Rows:
    def __aiter__(self):
        async def rows():
            for row in []:
                yield row
        return rows()


@pytest.fixture
def append_run(monkeypatch):
    data = inputs()
    committed = []
    timeline_write = AsyncMock()
    monkeypatch.setattr(CharacterAgentService, '_persist_timeline_tx', timeline_write)
    monkeypatch.setattr(CharacterAgentService, 'get_agent',
        AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kwargs: {'id': 'a'})))

    async def execute_write(callback):
        tx = SimpleNamespace(run=AsyncMock())
        await callback(tx)
        committed.append((timeline_write.call_args, tx.run.call_args))

    graph = SimpleNamespace(run=AsyncMock(return_value=Rows()), execute_write=AsyncMock(side_effect=execute_write))
    service = CharacterEmbodimentService(None, graph)
    service.load_embodiment_input = AsyncMock(side_effect=lambda *args: copy.deepcopy(data))

    async def run(llm):
        await service.append_created_scenes(agent_id='a', entity_id='e1', ontology_id=1,
            created_scene_ids={s['scene_id'] for s in data['scenes']}, llm_client=llm, settings=Settings())

    return data, committed, graph, run


@pytest.mark.asyncio
async def test_append_chunks_sources_and_commits_identity_with_each_revision(append_run):
    data, committed, graph, run = append_run
    llm = EvolutionLLM()
    await run(llm)
    assert_evolution(llm)
    assert len(committed) == 2
    for number, (timeline_call, identity_call) in enumerate(committed, 1):
        assert len(timeline_call.args[2].revisions) == 2
        assert json.loads(identity_call.kwargs['identity']) == identity(number)
        assert timeline_call.args[0] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_at', [1, 2])
async def test_append_synthesis_failure_prevents_failed_source_commit(append_run, fail_at):
    data, committed, graph, run = append_run
    with pytest.raises(EmbodimentGenerationError) as raised:
        await run(EvolutionLLM(fail_at=fail_at))
    assert raised.value.source_entity_id == ('source-a' if fail_at == 1 else 'source-b')
    assert len(committed) == fail_at - 1
    assert graph.execute_write.await_count == fail_at - 1
    # Candidate reducers must not mutate the loaded upstream lists.
    assert data['current_aspects'] == data['current_goals'] == []


class LifecycleLLM(EvolutionLLM):
    """Create profile items in A, retire them in B, checking both consolidations."""
    async def chat(self, **kwargs):
        tag = kwargs['usage_tag']
        if tag.endswith('scene_interpretation'):
            self.calls.append(kwargs)
            payload = json.loads(kwargs['messages'][1]['content'])
            return json.dumps({'scene_enrichments': [dict(emotions=[], beliefs=[], profile_events=[
                dict(kind='aspect', description='An enduring duty changes.'),
                dict(kind='goal', description='A specific undertaking changes.'),
            ]) for _ in payload['scenes']]})
        if tag.endswith('psychological_consolidation'):
            self.calls.append(kwargs)
            payload = json.loads(kwargs['messages'][1]['content'])
            output = dict(aspect_operations=[], goal_operations=[], new_aspects=[], new_goals=[],
                          focused_aspects=[], focused_goals=[])
            assert payload['identity_description'] == identity(self.syntheses)
            if self.syntheses == 0:
                output['new_aspects'] = [dict(name='I guard the archive', description='An enduring duty.',
                    category='identity', in_focus=True, changes=[], justification='Assumes responsibility.',
                    event_references=['event-001'])]
                output['new_goals'] = [dict(title='Find the missing ledger', description='Recover a specific ledger.',
                    goal_type='desire', in_focus=True, changes=[], justification='Makes a commitment.',
                    event_references=['event-002'])]
            else:
                assert payload['aspects'][0]['status'] == payload['goals'][0]['status'] == 'active'
                assert payload['aspects'][0]['in_focus'] and payload['goals'][0]['in_focus']
                output['aspect_operations'] = [dict(operation='status', target=dict(scope='existing', index=1),
                    status='inactive', justification='The duty ends.', event_references=['event-001'])]
                output['goal_operations'] = [dict(operation='status', target=dict(scope='existing', index=1),
                    status='completed', justification='The ledger is recovered.', event_references=['event-002'])]
            return json.dumps(dict(consolidation=output))
        return await super().chat(**kwargs)


@pytest.mark.asyncio
async def test_draft_lifecycle_updates_survive_and_synthesis_uses_active_items(draft_run):
    data, draft, run = draft_run
    data['source_groups'][0]['scenes'] = data['source_groups'][0]['scenes'][:1]
    llm = LifecycleLLM()
    await run(llm)
    synthesis = payloads(llm, 'identity_description')
    assert len(synthesis[0]['aspects']) == len(synthesis[0]['goals']) == 1
    assert synthesis[1]['aspects'] == synthesis[1]['goals'] == []
    proposal = json.loads(draft.generated_proposal)
    assert proposal['aspects'][0]['status'] == 'inactive'
    assert proposal['goals'][0]['status'] == 'completed'
    assert not proposal['aspects'][0]['in_focus'] and not proposal['goals'][0]['in_focus']
    assert proposal['identity_description'] == identity(2)


@pytest.mark.asyncio
async def test_append_lifecycle_and_consolidation_receive_previous_identity(append_run):
    data, committed, graph, run = append_run
    # One scene in A ensures one event pair and one source consolidation.
    data['source_groups'][0]['scenes'] = data['source_groups'][0]['scenes'][:1]
    llm = LifecycleLLM()
    await run(llm)
    assert len(committed) == 2
    assert payloads(llm, 'identity_description')[1]['goals'] == []
    revision = committed[-1][0].args[2].revisions[-1]
    assert revision.active_aspects[0].status.value == 'inactive'
    assert revision.active_goals[0].status.value == 'completed'


@pytest.mark.asyncio
async def test_append_write_failure_stops_before_next_source(append_run):
    data, committed, graph, run = append_run
    graph.execute_write.side_effect = RuntimeError('transaction failed')
    llm = EvolutionLLM()
    with pytest.raises(RuntimeError, match='transaction failed'):
        await run(llm)
    assert committed == []
    assert llm.syntheses == 1
    assert len(payloads(llm, 'character_incorporation')) == 2  # A's 5 + 1; no B.


@pytest.mark.asyncio
async def test_empty_append_does_no_model_work(append_run):
    data, committed, graph, run = append_run
    data['source_groups'] = []
    llm = EvolutionLLM()
    await run(llm)
    assert llm.calls == [] and committed == []


@pytest.mark.asyncio
async def test_corrupt_completed_identity_is_regenerated(draft_run):
    data, draft, run = draft_run
    await run(EvolutionLLM())
    checkpoints = json.loads(draft.generation_checkpoints)
    checkpoints['source:1']['completed']['identity'] = {'identity_summary': 'Incomplete'}
    draft.generation_checkpoints = json.dumps(checkpoints)
    retry = EvolutionLLM()
    await run(retry)
    assert len(retry.calls) == 1
    assert retry.calls[0]['usage_tag'].endswith('identity_description')
