"""Large draft acceptance keeps graph round trips bounded and history intact."""
from unittest.mock import AsyncMock
import json

import pytest

from app.schemas.character_agent import CharacterTimelineProjection, ProjectedCharacterImpact
from app.services.character_agent_service import CharacterAgentService


def timeline(size=260, baseline=0):
    revisions = [dict(revision_number=baseline, name='Mara')]
    sources = []
    for i in range(size):
        revision = dict(revision_number=baseline+i+1, name='Mara', subtitle=f'Chapter {i}',
                        scene_ids=[f's{i}'], source_group_id=f'g{i}')
        revisions.append(revision)
        sources.append(dict(source_group_id=f'g{i}', starting_revision_number=baseline+i,
            resulting_revision=revision,
            subtitle_change=dict(operation='set', subtitle=f'Chapter {i}', confidence=1,
                                 justification='New chapter', evidence_ids=[f'scene:s{i}']),
            perspectives=[dict(id=f'p{i}', scene_id=f's{i}', source_type='participated',
                perspective=f'I remember choice {i}.',
                scene=dict(id=f's{i}', type='scene', name='Choice'),
                emotions=[dict(description='Hope', arousal=50, valence=70)],
                beliefs=[dict(statement='Choices matter', confidence=80)])]))
    return CharacterTimelineProjection.model_validate(dict(revisions=revisions, source_projections=sources))


class Result:
    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, rows
    async def single(self):
        return self.row
    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield row
        return iterate()


class Graph:
    def __init__(self, baseline=0, missing_impact=False):
        self.calls = []
        self.baseline = baseline
        self.missing_impact = missing_impact
    async def run(self, query, **params):
        self.calls.append((query, params))
        if 'scoped_scene_ids' in query:
            return Result(dict(scoped_scene_ids=params['scene_ids']))
        if 'RETURN r ORDER' in query:
            return Result(dict(r=dict(id='existing-revision', revision_number=self.baseline)))
        if 'RETURN count(node)' in query:
            return Result(dict(created=0 if self.missing_impact else len(params['rows'])))
        if 'RETURN p.id AS id' in query:
            return Result(rows=[dict(id=id, document=f'Memory {id}') for id in params['ids']])
        return Result()


def rows_for(graph, fragment):
    return [row for query, params in graph.calls if fragment in query for row in params['rows']]


@pytest.mark.asyncio
@pytest.mark.parametrize('append', [False, True])
async def test_large_timeline_batches_records_and_preserves_revision_links(append):
    value = timeline(baseline=5 if append else 0)
    graph = Graph(baseline=value.revisions[0].revision_number)
    await CharacterAgentService(None, graph)._persist_timeline_tx(graph,
        dict(id='agent', ontology_id=1), value, 'now',
        append=append, provider='provider', model='model', prompt_version='version')
    revisions = rows_for(graph, 'CREATE (revision:CharacterIdentityRevision)')
    changes = rows_for(graph, 'CREATE (change:CharacterIdentityChange)')
    perspectives = rows_for(graph, 'CREATE (perspective:ScenePerspective)')
    assert len(revisions) == (260 if append else 261)
    assert len(changes) == len(perspectives) == 260
    assert len(rows_for(graph, 'CREATE (node:EmotionalInterpretation)')) == 260
    assert len(rows_for(graph, 'CREATE (node:CharacterBelief)')) == 260
    assert len(graph.calls) <= 20  # thousands of individual queries previously
    assert all(len(params['rows']) <= 128 for _, params in graph.calls if 'rows' in params)
    revision_ids = {r['props']['revision_number']: r['props']['id'] for r in revisions}
    revision_ids[value.revisions[0].revision_number] = (
        'existing-revision' if append else revisions[0]['props']['id'])
    for i, perspective in enumerate(perspectives):
        assert perspective['revision_id'] == revision_ids[value.revisions[0].revision_number+i]
        assert perspective['props']['id'] == f'p{i}'
        assert 'scene' not in perspective['props']
        assert 'Choices matter' in perspective['props']['memory_document']
        assert changes[i]['revision_id'] == revision_ids[value.revisions[0].revision_number+i+1]
        assert json.loads(changes[i]['props']['evidence_ids']) == [f'scene:s{i}']
    assert all(r['props']['provider'] == 'provider' for r in revisions)


@pytest.mark.asyncio
async def test_bulk_legacy_impact_rejects_unassigned_target():
    value = timeline(1)
    value.source_projections[0].perspectives[0].impacts = [ProjectedCharacterImpact(
        impact_type='goal_change', direction='advanced', magnitude=30,
        description='Progress', target_id='foreign-goal')]
    graph = Graph(missing_impact=True)
    with pytest.raises(ValueError, match='not active on the CharacterAgent'):
        await CharacterAgentService(None, graph)._persist_timeline_tx(graph,
            dict(id='agent', ontology_id=1), value, 'now',
            provider=None, model=None, prompt_version=None)
    query, params = graph.calls[-1]
    assert '(agent:CharacterAgent {id:$agent_id})-[:PURSUES]->' in query
    assert params['agent_id'] == 'agent'


@pytest.mark.asyncio
async def test_memory_batches_only_timeline_documents_and_scopes_writes(monkeypatch):
    from app.services import character_agent_service as module
    batches = []
    class Encoder:
        model_id = 'encoder'
        def embed_texts(self, texts):
            batches.append(texts)
            return [[float(i)] for i in range(len(texts))]
    monkeypatch.setattr(module, 'EmbeddingService', Encoder)
    graph = Graph()
    service = CharacterAgentService(None, graph)
    service.get_perspective = AsyncMock(side_effect=AssertionError('No per-perspective reads'))
    await service.refresh_timeline_memory('agent', timeline())
    assert [len(batch) for batch in batches] == [128, 128, 4]
    assert len(graph.calls) == 6
    writes = [(q, p) for q, p in graph.calls if 'SET p.memory_embedding=' in q]
    assert len(writes) == 3
    assert all(p['agent_id'] == 'agent' and 'WHERE p.memory_document=row.document' in q for q, p in writes)
    assert {r['id'] for _, p in writes for r in p['rows']} == {f'p{i}' for i in range(260)}


@pytest.mark.asyncio
async def test_embedding_failure_keeps_persisted_lexical_documents(monkeypatch, caplog):
    from app.services import character_agent_service as module
    class Encoder:
        def embed_texts(self, texts):
            raise RuntimeError('Encoder unavailable')
    monkeypatch.setattr(module, 'EmbeddingService', Encoder)
    graph = Graph()
    await CharacterAgentService(None, graph).refresh_timeline_memory('agent', timeline(1))
    assert len(graph.calls) == 1
    assert 'character_timeline_embedding_deferred' in caplog.text


@pytest.mark.asyncio
async def test_optional_legacy_cause_lookup_retains_null_and_ownership_checks():
    graph = Graph()
    service = CharacterAgentService(None, graph)
    service._require_perspective_owner = AsyncMock()
    assert await service.list_perspective_children('agent', 'p0', 'impacts') == []
    service._require_perspective_owner.assert_awaited_once_with('agent', 'p0', False)
    query, params = graph.calls[-1]
    assert 'OPTIONAL MATCH (node)-[cause]->' in query
    assert 'WHERE type(cause)=$cause_type' in query
    assert params['cause_type'] == 'CAUSED_BY'
