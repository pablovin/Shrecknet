from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_current_user, get_ontology_instance_service
from app.api.routers.ontologies import router
from app.models.user import UserRole
from app.services.atlas_discovery_service import AtlasDiscoveryService


class Result:
    def __init__(self, rows): self.rows = rows
    async def data(self): return self.rows
    async def single(self): return self.rows[0] if self.rows else None


class Graph:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []
    async def run(self, query, **arguments):
        self.calls.append((query, arguments))
        return Result(next(self.results))


def source(i):
    return dict(id=f'source-{i}', display_name=f'Chapter {i}', avatar_url=None,
                scene_count=200, latest_activity='2026-10-17')


@pytest.mark.asyncio
async def test_sources_page_uses_one_projection_and_lookahead():
    graph = Graph([source(i) for i in range(11)])
    page = await AtlasDiscoveryService(graph).sources(ontology_id=7, skip=10, limit=10)
    assert len(page.results) == 10 and page.has_more and page.skip == 10
    assert len(graph.calls) == 1
    query, arguments = graph.calls[0]
    assert arguments == dict(ontology_id=7, skip=10, fetch_limit=11)
    assert 'DERIVED_FROM' in query
    assert 'WITH source, count(DISTINCT scene)' in query
    assert 'ORDER BY latest_activity DESC, id ASC' in query


@pytest.mark.asyncio
async def test_sources_exhaustion_and_empty_world():
    for rows in ([], [source(1)]):
        page = await AtlasDiscoveryService(Graph(rows)).sources(ontology_id=7)
        assert not page.has_more and len(page.results) == len(rows)


@pytest.mark.asyncio
async def test_scene_search_paginates_before_hydration_and_preserves_provenance():
    graph = Graph([{'total': 301}], [{
        'id': 'scene-201', 'name': 'Arrival', 'description': 'Beyond the old cutoff.',
        'instance_id': 'owning-container', 'instance_name': 'Scenes',
        'source_entity_id': 'anchor-201', 'source_page': source(201),
        'milestone_count': 3, 'perspective_count': 1,
    }])
    page = await AtlasDiscoveryService(graph).scenes(ontology_id=7, query='  Arrival ', source_entity_id='anchor-201', skip=30, limit=30)
    row = page.results[0]
    assert row.instance.instance_id == 'owning-container'
    assert row.source_page.id == 'source-201'
    assert row.scene.derived_from.entity_instance_id == 'anchor-201'
    assert row.scene.milestone_count == 3 and page.total == 301
    assert len(graph.calls) == 2
    query, args = graph.calls[1]
    assert query.index('$scene_query') < query.index('SKIP $skip LIMIT $limit') < query.index('OPTIONAL MATCH')
    assert args['scene_query'] == 'arrival' and args['include_private'] is False
    assert '$include_private OR coalesce(agent.visibility' in query


@pytest.mark.asyncio
async def test_empty_search_skips_hydration():
    graph = Graph([{'total': 0}])
    page = await AtlasDiscoveryService(graph).scenes(ontology_id=7, query='absent')
    assert not page.results and len(graph.calls) == 1


@pytest.mark.asyncio
async def test_scene_source_does_not_fall_back_to_owning_container():
    graph = Graph([source(5)], [])
    service = AtlasDiscoveryService(graph)
    assert (await service.scene_source(ontology_id=7, scene_id='scene')).id == 'source-5'
    with pytest.raises(ValueError, match='Scene source not found'):
        await service.scene_source(ontology_id=7, scene_id='missing')


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/ontologies/7/scene-sources', '/ontologies/7/scenes', '/ontologies/7/scenes/secret/source', '/ontologies/7/elders'])
async def test_world_denial_precedes_graph_reads(path):
    graph = Graph()
    service = SimpleNamespace(graph_session=graph, assert_ontology_graph_read_access=AsyncMock(side_effect=PermissionError('Denied')))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=4, role=UserRole.PLAYER)
    app.dependency_overrides[get_ontology_instance_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.get(path)
    assert result.status_code == 403 and graph.calls == []


@pytest.mark.asyncio
async def test_private_counts_are_admin_only_and_limits_are_validated():
    graph = Graph([{'total': 0}], [{'total': 0}])
    service = SimpleNamespace(graph_session=graph, assert_ontology_graph_read_access=AsyncMock())
    app = FastAPI()
    app.include_router(router)
    actor = SimpleNamespace(id=4, role=UserRole.PLAYER)
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_ontology_instance_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/ontologies/7/scenes')).status_code == 200
        actor.role = UserRole.ADMIN
        assert (await client.get('/ontologies/7/scenes')).status_code == 200
        assert (await client.get('/ontologies/7/scene-sources?limit=101')).status_code == 422
    assert [args['include_private'] for _, args in graph.calls] == [False, True]


@pytest.mark.asyncio
async def test_elder_cards_are_active_world_filtered_and_omit_configuration(session_maker):
    from app.models.agent import Agent
    from app.models.ontology import Ontology
    from app.services.agent_service import AgentService
    from datetime import datetime, timezone
    async with session_maker() as session:
        world = Ontology(name='Atlas world', description='')
        other = Ontology(name='Other world', description='')
        session.add_all([world, other])
        await session.flush()
        session.add_all([
            Agent(id='elder-z', name='Z Sage', job='elder', active=True, created_at=datetime(2026, 10, 17, tzinfo=timezone.utc), ontologies=[world], writing_style='private config'),
            Agent(id='elder-a', name='A Sage', job='elder', active=True, created_at=datetime(2026, 10, 16, tzinfo=timezone.utc), ontologies=[world]),
            Agent(id='inactive', name='Inactive', job='elder', active=False, ontologies=[world]),
            Agent(id='architect', name='Architect', job='architect', active=True, ontologies=[world]),
            Agent(id='other', name='Other Elder', job='elder', active=True, ontologies=[other]),
        ])
        await session.flush()
        cards = await AgentService(session).world_elder_cards(world.id, limit=1)
        assert [card.id for card in cards] == ['elder-z']
        assert set(cards[0].model_dump()) == {'id', 'name', 'description', 'avatar_url'}
        assert [card.id for card in await AgentService(session).world_elder_cards(world.id, skip=1)] == ['elder-a']
