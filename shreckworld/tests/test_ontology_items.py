"""Direct ontology ownership requires no ShreckWorld container."""
import pytest
from sqlalchemy import select, func
from app.api.deps import get_current_user, require_world_manager, ShrecknetPrincipal
from app.db.session import session_factory
from app.integrations.ontologies import authorized_ontology
from app.main import app
from app.models import ShreckWorld, ShreckWorldLibraryItem
from app.repositories.source_scope import OntologyScope
from test_upload_metadata_api import api, upload


@pytest.fixture
def direct(api):
    def scope(ontology_id: int):
        return OntologyScope(ontology_id, f'Ontology {ontology_id}')
    app.dependency_overrides[authorized_ontology] = scope
    yield api
    app.dependency_overrides.pop(authorized_ontology, None)


def add(client, headers, ontology_id=7):
    return client.post(f'/ontologies/{ontology_id}/items', headers=headers, params={'title':'Direct'}, files={'pdf':('notes.txt',b'Direct notes','text/plain')})


def test_first_item_and_empty_ontology_have_no_container(direct):
    client, headers, _ = direct
    with session_factory()() as db:
        before = db.scalar(select(func.count()).select_from(ShreckWorld))
    assert client.get('/ontologies/7/items', headers=headers).json() == []
    created = add(client, headers)
    assert created.status_code == 201, created.text
    item = created.json()
    assert item['ontology_id'] == 7 and item['shreckworld_id'] is None
    assert client.get('/ontologies/7/items', headers=headers).json()[0]['id'] == item['id']
    assert client.get('/ontologies/8/items', headers=headers).json() == []
    for suffix in ['', '/content', '/cover']:
        assert client.get(f"/ontologies/8/items/{item['id']}{suffix}", headers=headers).status_code == 404
    assert client.get(f"/ontologies/7/items/{item['id']}/content", headers=headers).content == b'Direct notes'
    assert client.patch(f"/ontologies/8/items/{item['id']}", headers=headers, json={'title':'Wrong'}).status_code == 404
    assert client.delete(f"/ontologies/8/items/{item['id']}", headers=headers).status_code == 404
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ShreckWorld)) == before
        assert db.get(ShreckWorldLibraryItem, item['id']).ontology_id == 7
    assert client.delete(f"/ontologies/7/items/{item['id']}", headers=headers).status_code == 204


def test_roles_and_query_scope(direct, monkeypatch):
    client, headers, _ = direct
    item = add(client, headers).json()
    monkeypatch.setattr('app.api.routers.shreckworlds.query_shreckworld', lambda *a, **k: ([], {'requested_sources':1,'sources_searched':0,'sources_without_ready_index':1,'sources_requiring_reindex':0}))
    assert client.post('/ontologies/7/query', headers=headers, json={'query':'Question','library_item_id':item['id']}).status_code == 200
    assert client.post('/ontologies/8/query', headers=headers, json={'query':'Question','library_item_id':item['id']}).status_code == 404
    app.dependency_overrides.pop(require_world_manager)
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id='reader', role='player')
    assert add(client, headers).status_code == 403
    assert client.delete(f"/ontologies/7/items/{item['id']}", headers=headers).status_code == 403
    assert client.post(f"/ontologies/7/items/{item['id']}/index", headers=headers).status_code == 403


def test_ontology_rejection_is_not_an_empty_library(api, monkeypatch):
    from fastapi import HTTPException
    client, headers, _ = api
    def unavailable(*args):
        raise HTTPException(404, 'Ontology not found')
    monkeypatch.setattr('app.integrations.ontologies.shrecknet_read', unavailable)
    assert client.get('/ontologies/7/items', headers=headers).status_code == 404
    assert add(client, headers).status_code == 404
    assert client.get('/ontologies/0/items', headers=headers).status_code == 422
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get('/ontologies/7/items').status_code == 401


def test_migration_preserves_ids_files_and_legacy_links(direct, monkeypatch):
    client, headers, world_id = direct
    item = upload(direct, params={'title':'Legacy'}).json()
    monkeypatch.setattr('app.services.ontology_migration.shrecknet_read', lambda *a: [])
    monkeypatch.setattr('app.services.ontology_migration.resolve_ontology', lambda id, token: OntologyScope(id,'World'))
    preview = client.post('/admin/ontology-migration', headers=headers, json={}).json()
    assert preview['collections'][0]['status'] == 'needs_mapping'
    assert preview['changed_items'] == 0
    payload={'mappings':{world_id:7},'apply':True}
    assert client.post('/admin/ontology-migration', headers=headers,json=payload).json()['changed_items'] == 1
    assert client.post('/admin/ontology-migration', headers=headers,json=payload).json()['changed_items'] == 0
    assert client.get('/ontologies/7/items', headers=headers).json()[0]['id'] == item['id']
    assert client.get(f"/ontologies/7/items/{item['id']}/content",headers=headers).content.startswith(b'%PDF')
    assert client.get(f"/shreckworlds/{world_id}/library-items/{item['id']}/content",headers=headers).status_code == 200
    assert client.delete(f'/admin/shreckworlds/{world_id}',headers=headers).status_code == 409
    assert client.post('/admin/ontology-migration',headers=headers,json={'mappings':{world_id:8},'apply':True}).status_code == 422


def test_sqlite_upgrade_relaxes_parent_without_losing_children(tmp_path):
    from sqlalchemy import create_engine, text, inspect
    from app.db.ontology_items import migrate_ontology_items
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as db:
        db.exec_driver_sql('PRAGMA foreign_keys=ON')
        db.exec_driver_sql('CREATE TABLE shreckworlds (id VARCHAR(36) PRIMARY KEY)')
        db.exec_driver_sql('CREATE TABLE shreckworld_library_items (id VARCHAR(36) PRIMARY KEY, shreckworld_id VARCHAR(36) NOT NULL REFERENCES shreckworlds(id) ON DELETE CASCADE, title TEXT)')
        db.exec_driver_sql('CREATE TABLE children (id TEXT PRIMARY KEY, item_id VARCHAR(36) REFERENCES shreckworld_library_items(id) ON DELETE CASCADE)')
        db.exec_driver_sql('CREATE TABLE source_audit (item_id TEXT)')
        db.exec_driver_sql('CREATE TRIGGER source_insert_audit AFTER INSERT ON shreckworld_library_items BEGIN INSERT INTO source_audit VALUES (new.id); END')
        db.exec_driver_sql("INSERT INTO shreckworlds VALUES ('legacy')")
        db.exec_driver_sql("INSERT INTO shreckworld_library_items VALUES ('book','legacy','Title')")
        db.exec_driver_sql("INSERT INTO children VALUES ('chunk','book')")
    migrate_ontology_items(engine)
    migrate_ontology_items(engine)
    assert next(c for c in inspect(engine).get_columns('shreckworld_library_items') if c['name']=='shreckworld_id')['nullable']
    with engine.begin() as db:
        assert db.exec_driver_sql('SELECT * FROM children').all() == [('chunk','book')]
        assert db.exec_driver_sql('SELECT id, shreckworld_id FROM shreckworld_library_items').all() == [('book','legacy')]
        db.exec_driver_sql("INSERT INTO shreckworld_library_items (id,ontology_id,title) VALUES ('direct',7,'New')")
        assert not db.exec_driver_sql('PRAGMA foreign_key_check').all()
        assert db.exec_driver_sql('SELECT item_id FROM source_audit ORDER BY item_id').all() == [('book',), ('direct',)]


def test_ontology_agent_job_has_no_collection_and_is_owner_scoped(direct, monkeypatch):
    from app.tasks.agent import run_shreckworld_agent_job
    from app.models import ShreckWorldAgentJob
    client, headers, _ = direct
    monkeypatch.setattr(run_shreckworld_agent_job, 'delay', lambda *args: None)
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id='owner', role='player')
    created = client.post('/ontologies/7/agent-jobs', headers=headers, json={'query':'Research'})
    assert created.status_code == 202, created.text
    job = created.json()
    assert job['ontology_id'] == 7 and job['shreckworld_id'] is None
    assert client.get(f"/ontologies/7/agent-jobs/{job['id']}", headers=headers).status_code == 200
    assert client.get(f"/ontologies/8/agent-jobs/{job['id']}", headers=headers).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id='other', role='player')
    assert client.get(f"/ontologies/7/agent-jobs/{job['id']}", headers=headers).status_code == 404
    with session_factory()() as db:
        assert db.get(ShreckWorldAgentJob, job['id']).shreckworld_id is None
    from app.schemas.shreckworld import ShreckWorldQueryResponse, QuerySourceCoverage
    def research(session, *, shreckworld_id, request, on_stage):
        assert isinstance(shreckworld_id, OntologyScope) and shreckworld_id.ontology_id == 7
        return ShreckWorldQueryResponse(shreckworld_id=None, ontology_id=7, query=request.query,
            answer='Evidence', citations=[], source_coverage=QuerySourceCoverage(requested_source_count=0,
            searched_source_count=0, sources_without_ready_index=0, sources_requiring_reindex=0))
    monkeypatch.setattr('app.tasks.agent.run_shreckworld_agent', research)
    run_shreckworld_agent_job.run(job['id'])
    with session_factory()() as db:
        assert db.get(ShreckWorldAgentJob, job['id']).status == 'completed'


def test_delegated_grant_requires_exact_ontology_and_book_subset(monkeypatch):
    import asyncio
    import httpx
    from fastapi import HTTPException
    from app.schemas.shreckworld import ShreckWorldQueryRequest
    from app.services.companion_retrieval import authorize
    class Identity:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, path, **kwargs):
            return httpx.Response(200, request=httpx.Request('GET','http://identity.test'), json={'scope': {'ontology_id':7,'library_item_ids':['one']}})
    monkeypatch.setattr('app.services.companion_retrieval.httpx.AsyncClient', Identity)
    request = ShreckWorldQueryRequest(query='Question', library_item_ids=['one'])
    asyncio.run(authorize('grant', OntologyScope(7,'World'), request))
    for scope, payload in [(OntologyScope(8,'Other'), request), (OntologyScope(7,'World'), ShreckWorldQueryRequest(query='Question')), (OntologyScope(7,'World'), request.model_copy(update={'mode':'answer'}))]:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(authorize('grant', scope, payload))
        assert exc.value.status_code == 403
