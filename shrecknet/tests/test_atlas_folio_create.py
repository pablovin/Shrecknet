from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.deps import get_current_user, get_ontology_instance_service
from app.api.routers.ontologies import router
from app.models.user import UserRole
from app.schemas.atlas import AtlasFolioCreate
from app.schemas.ontology_instance import OntologyInstanceRead, OntologyInstanceCreate
from app.services.atlas_folio_service import AtlasFolioService
from app.services.ontology_instance_service import OntologyInstanceService, FolioCreateConflict

REQUEST = '4b654adb-5666-4dbc-9122-123456789abc'
REVISION = '2026-10-01T00:00:00Z'


def page():
    return OntologyInstanceRead.model_validate({
        'instance_id': 'created', 'ontology_id': 7, 'name': 'New page', 'created_at': REVISION, 'updated_at': REVISION,
        'entities': [{'entity_instance_id': 'node', 'definition_id': 1, 'alias': 'new', 'text': 'Text',
            'created_date': REVISION, 'last_updated_date': REVISION, 'author_type': 'human', 'author_id': '4'}],
    })


@pytest.mark.parametrize('extra', [{'name': '  '}, {'definition_id': 0}, {'author_id': 'attacker'}, {'alias': 'custom'}, {'request_id': 'not-uuid'}, {'relationships': [{'definition_id': 8, 'target_alias': 'hidden'}]}])
def test_creation_rejects_invalid_and_client_owned_identity_fields(extra):
    with pytest.raises(ValidationError):
        AtlasFolioCreate(**{'request_id': REQUEST, 'definition_id': 1, 'name': 'New page', **extra})


@pytest.mark.asyncio
async def test_creation_assigns_actor_and_alias_upstream_and_scopes_retry_identity():
    instances = SimpleNamespace(graph_session=None, sql_session=None, create_instance=AsyncMock(return_value=page()))
    service = AtlasFolioService(instances)
    request = AtlasFolioCreate(request_id=REQUEST, definition_id=1, name='  New page  ', text='  Keep spacing  ')
    result = await service.create(7, 4, request)
    payload = instances.create_instance.call_args.args[0]
    assert payload.name == 'New page'
    assert payload.ontology_id == 7
    assert payload.entities[0].author_id == '4'
    assert payload.entities[0].author_type.value == 'human'
    assert payload.entities[0].alias == 'page_7_4_' + UUID(REQUEST).hex
    assert payload.entities[0].text == '  Keep spacing  '
    options = instances.create_instance.call_args.kwargs
    assert options['creation_key'] == f'4:7:{REQUEST}'
    assert options['bounded_result'] is True
    assert result.folio_projection.relationships == 'omitted'
    await service.create(7, 4, request)
    assert instances.create_instance.call_args.kwargs['creation_fingerprint'] == options['creation_fingerprint']
    await service.create(7, 5, request)
    assert instances.create_instance.call_args.args[0].entities[0].alias != payload.entities[0].alias
    assert instances.create_instance.call_args.kwargs['creation_key'] == f'5:7:{REQUEST}'
    await service.create(7, 4, request.model_copy(update={'name': 'Other'}))
    assert instances.create_instance.call_args.kwargs['creation_fingerprint'] != options['creation_fingerprint']


@pytest.mark.asyncio
@pytest.mark.parametrize('role', [UserRole.PLAYER, UserRole.WORLD_BUILDER])
async def test_new_page_routes_require_admin_before_domain_writes(role):
    app = FastAPI(); app.include_router(router)
    service = SimpleNamespace(assert_ontology_graph_read_access=AsyncMock(), create_instance=AsyncMock())
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=4, role=role)
    app.dependency_overrides[get_ontology_instance_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/ontologies/7/pages', json={'request_id': REQUEST, 'definition_id': 1, 'name': 'Name'})
        recovery = await client.get(f'/ontologies/7/pages/creation/{REQUEST}')
    assert response.status_code == recovery.status_code == 403
    service.create_instance.assert_not_awaited()


@pytest.mark.asyncio
async def test_creation_lookup_never_resolves_another_actor_or_world():
    graph = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(single=AsyncMock(return_value=None))))
    service = AtlasFolioService(SimpleNamespace(graph_session=graph, sql_session=None))
    with pytest.raises(ValueError):
        await service.find_creation(8, 5, UUID(REQUEST))
    assert graph.run.call_args.kwargs == {'key': f'5:8:{REQUEST}', 'ontology_id': 8}


@pytest.mark.asyncio
@pytest.mark.parametrize('replay,conflict,invalid_target', [(False, False, False), (True, False, False), (True, True, False), (False, False, True)])
async def test_creation_replays_without_duplicate_facets_and_rolls_back_invalid_batches(monkeypatch, replay, conflict, invalid_target):
    import app.services.ontology_instance_service as module
    from app.tasks.neo4j_embedding import embed_nodes
    writes = []
    async def run(query, **values):
        writes.append((query, values))
        if 'MERGE (i:OntologyInstance' in query:
            return SimpleNamespace(single=AsyncMock(return_value={'instance_id': 'existing' if replay else values['instance_id'], 'fingerprint': 'other' if conflict else 'fingerprint'}))
        return SimpleNamespace()
    tx = SimpleNamespace(run=AsyncMock(side_effect=run), commit=AsyncMock(), rollback=AsyncMock(), close=AsyncMock())
    graph = SimpleNamespace(begin_transaction=AsyncMock(return_value=tx))
    service = OntologyInstanceService.__new__(OntologyInstanceService)
    service.graph_session = graph
    service.repository = SimpleNamespace(get=AsyncMock(return_value=object()))
    service._load_entity_definitions = AsyncMock(return_value={1: {'properties': {}, 'relationships': {8: SimpleNamespace(destiny_entity_id=1, bi_directional=True)}}})
    service._validate_existing_target_entity = AsyncMock(side_effect=ValueError('Foreign target') if invalid_target else None)
    service.get_instance = AsyncMock(return_value=page())
    monkeypatch.setattr(module, '_enqueue_link_instance', lambda _: None)
    monkeypatch.setattr(embed_nodes, 'delay', lambda *_: None)
    payload = OntologyInstanceCreate(ontology_id=7, name='New page', entities=[{'definition_id': 1, 'alias': 'new', 'author_type': 'human', 'author_id': '4', 'relationships': [{'definition_id': 8, 'target_entity_instance_id': 'target'}]}])
    async def create():
        return await service.create_instance(payload, creation_key=f'4:7:{REQUEST}', creation_fingerprint='fingerprint', bounded_result=True)
    if conflict or invalid_target:
        with pytest.raises(FolioCreateConflict if conflict else ValueError): await create()
        tx.rollback.assert_awaited_once(); tx.commit.assert_not_awaited()
    else:
        await create()
        tx.commit.assert_awaited_once(); tx.rollback.assert_not_awaited()
        if replay:
            assert len(writes) == 1
            service.get_instance.assert_awaited_once_with('existing', include_scenes=False, include_relationships=False)
        else:
            assert sum('CREATE (i)-[:HAS_ENTITY]' in query for query, _ in writes) == 1
            assert sum('CREATE (source)-[r:RELATES_TO' in query for query, _ in writes) == 1
            assert sum('CREATE (target)-[r:RELATES_TO' in query for query, _ in writes) == 1


@pytest.mark.asyncio
async def test_page_type_from_another_world_is_rejected_before_a_transaction():
    service = OntologyInstanceService.__new__(OntologyInstanceService)
    service.repository = SimpleNamespace(get=AsyncMock(return_value=object()))
    service._load_entity_definitions = AsyncMock(return_value={})
    service.graph_session = SimpleNamespace(begin_transaction=AsyncMock())
    request = OntologyInstanceCreate(ontology_id=7, name='Name', entities=[{'definition_id': 99, 'alias': 'new', 'author_type': 'human', 'author_id': '4'}])
    with pytest.raises(ValueError, match='does not belong to ontology'):
        await service.create_instance(request, creation_key=f'4:7:{REQUEST}', creation_fingerprint='fingerprint')
    service.graph_session.begin_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_startup_installs_the_unique_creation_key_contract():
    from app.graph.neo4j import ensure_temporal_graph_constraints
    graph = SimpleNamespace(run=AsyncMock())
    await ensure_temporal_graph_constraints(graph)
    assert any('atlas_creation_request_unique' in call.args[0] and 'atlas_creation_request IS UNIQUE' in call.args[0] for call in graph.run.call_args_list)
