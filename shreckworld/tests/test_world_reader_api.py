"""Reader contracts are independent of the semantic model and indexing worker."""
import pytest

from test_upload_metadata_api import api, upload
from app.api.deps import get_current_user, require_world_manager, ShrecknetPrincipal
from app.main import app
from app.schemas.shreckworld import ShreckWorldCitation
from app.services.embedding import EmbeddingUnavailable


def test_shreckworld_model_target_is_admin_configurable_and_persisted(api):
    client, headers, _ = api
    endpoint = "/admin/settings/model-targets/shreckworld_synthesizer"
    initial = client.get(endpoint, headers=headers)
    assert initial.status_code == 200
    assert initial.json()["model_shreckworld_synthesizer"]["provider"] == "openai"
    assert initial.json()["model_shreckworld_synthesizer"]["name"] == "gpt-5-nano"

    target = {
        "provider": "anthropic",
        "name": "claude-sonnet-4",
        "reasoning": True,
        "reasoning_effort": "high",
        "web_search_enabled": False,
    }
    saved = client.put(
        endpoint, headers=headers,
        json={"model_shreckworld_synthesizer": target},
    )
    assert saved.status_code == 200
    assert saved.json()["model_shreckworld_synthesizer"] == target
    assert client.get(endpoint, headers=headers).json()["model_shreckworld_synthesizer"] == target
    assert client.put(
        endpoint, headers=headers,
        json={"model_shreckworld_synthesizer": {**target, "name": " "}},
    ).status_code == 422

    player()
    assert client.get(endpoint, headers=headers).status_code == 403
    assert client.put(
        endpoint, headers=headers,
        json={"model_shreckworld_synthesizer": target},
    ).status_code == 403


def player():
    app.dependency_overrides.pop(require_world_manager, None)
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="reader", role="player")


def test_player_can_discover_and_read_but_cannot_manage(api):
    client, headers, world_id = api
    item = upload(api, params={"title": "Core"}).json()
    player()
    assert client.get('/shreckworlds', headers=headers).status_code == 200
    assert client.get(f'/shreckworlds/{world_id}', headers=headers).json()['id'] == world_id
    prefix = f'/shreckworlds/{world_id}/library-items'
    assert client.get(prefix, headers=headers).json()[0]['id'] == item['id']
    detail = client.get(f"{prefix}/{item['id']}", headers=headers).json()
    assert detail['source_sha256'] == item['source_sha256']
    assert client.get(f"{prefix}/{item['id']}/content", headers=headers).content.startswith(b'%PDF')
    assert client.get('/admin/shreckworlds', headers=headers).status_code == 403
    assert client.delete(f"/admin/shreckworlds/{world_id}/library-items/{item['id']}", headers=headers).status_code == 403
    assert client.get(f"/shreckworlds/other/library-items/{item['id']}", headers=headers).status_code == 404
    app.dependency_overrides.pop(get_current_user)
    for path in ['/shreckworlds', f'/shreckworlds/{world_id}', prefix, f"{prefix}/{item['id']}", f"{prefix}/{item['id']}/content"]:
        assert client.get(path).status_code == 401


def test_selected_passage_preserves_structured_scope_and_checks_revision(api, monkeypatch):
    client, headers, world_id = api
    item = upload(api, params={"title": "Core"}).json()
    player()
    calls = []
    def query(session, **kwargs):
        calls.append(kwargs)
        return [], {
            "requested_sources": 1, "sources_searched": 0,
            "sources_without_ready_index": 1, "sources_requiring_reindex": 0,
        }
    monkeypatch.setattr('app.api.routers.shreckworlds.query_shreckworld', query)
    selection = dict(library_item_id=item['id'], source_sha256=item['source_sha256'], page_number=1, text='Selected evidence')
    payload = dict(query='Explain', library_item_id=item['id'], selection=selection)
    endpoint = f'/shreckworlds/{world_id}/query'
    assert client.post(endpoint, headers=headers, json=payload).status_code == 200
    assert calls[0]['library_item_id'] == item['id']
    assert calls[0]['query'] == 'Explain Selected evidence'
    assert client.post(endpoint, headers=headers, json={**payload, 'selection': {**selection, 'source_sha256': 'b'*64}}).status_code == 409
    assert client.post(endpoint, headers=headers, json={**payload, 'selection': {**selection, 'page_number': 0}}).status_code == 422
    other = upload_as_admin(api, 'Other')
    assert client.post(endpoint, headers=headers, json={**payload, 'selection': {**selection, 'library_item_id': other['id'], 'source_sha256': other['source_sha256']}}).status_code == 422
    assert len(calls) == 1


def upload_as_admin(api, title):
    app.dependency_overrides[require_world_manager] = lambda: ShrecknetPrincipal(user_id='admin', role='admin')
    item = upload(api, params={'title': title}).json()
    app.dependency_overrides.pop(require_world_manager)
    return item


def test_encoder_failure_does_not_prevent_reading(api, monkeypatch):
    client, headers, world_id = api
    item = upload(api, params={"title": "Core"}).json()
    def unavailable(*args, **kwargs):
        raise EmbeddingUnavailable('model not installed')
    monkeypatch.setattr('app.api.routers.shreckworlds.query_shreckworld', unavailable)
    monkeypatch.setattr('app.services.rule_resolution.query_shreckworld', unavailable)
    player()
    assert client.post(f'/shreckworlds/{world_id}/query', headers=headers, json={'query':'rules'}).status_code == 503
    assert client.post(f'/shreckworlds/{world_id}/resolve-action', headers=headers, json={'action':'attack'}).status_code == 503
    assert client.get(f"/shreckworlds/{world_id}/library-items/{item['id']}/content", headers=headers).status_code == 200


def test_action_resolution_reports_priority_and_uncertainty_without_mutation(api, monkeypatch):
    client, headers, world_id = api
    items = [upload(api, params={'title': name}).json() for name in ['Core', 'House']]
    citations = [ShreckWorldCitation(source_id=f'source-{i}', library_item_id=item['id'], title=item['title'],
        page_number=1, chunk_id=f'chunk-{i}', score=.8, excerpt='Attack rule', authority_tier='core_rules',
        authority_priority=priority) for i, (item, priority) in enumerate(zip(items, [100, 10]))]
    monkeypatch.setattr('app.services.rule_resolution.query_shreckworld', lambda *args, **kwargs: (citations, {}))
    player()
    endpoint = f'/shreckworlds/{world_id}/resolve-action'
    response = client.post(endpoint, headers=headers, json={'action': 'attack', 'facts':['Mounted']})
    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'ambiguous'
    assert result['preferred_source_ids'] == [items[1]['id']]
    assert result['missing_facts']
    assert len(client.get(f'/shreckworlds/{world_id}/library-items', headers=headers).json()) == 2
    monkeypatch.setattr('app.services.rule_resolution.query_shreckworld', lambda *args, **kwargs: ([], {}))
    assert client.post(endpoint, headers=headers, json={'action':'unknown'}).json()['status'] == 'unsupported'
    assert client.post(endpoint, headers=headers, json={'action':'attack', 'facts':['x'*501]}).status_code == 422
    assert client.post(endpoint, headers=headers, json={'action':'attack', 'library_item_id':'other-world-book'}).status_code == 404


def test_authenticated_byte_ranges_and_head(api):
    client, headers, world_id = api
    item = upload(api, params={'title':'Range book'}).json()
    player()
    url = f"/shreckworlds/{world_id}/library-items/{item['id']}/content"
    original = client.get(url, headers=headers).content
    head = client.head(url, headers=headers)
    assert head.status_code == 200
    assert head.content == b''
    assert int(head.headers['content-length']) == len(original)
    assert head.headers['cache-control'] == 'private, no-store'
    response = client.get(url, headers={**headers, 'Range':'bytes=0-9'})
    assert response.status_code == 206
    assert response.content == original[:10]
    assert response.headers['content-range'] == f'bytes 0-9/{len(original)}'
    assert client.get(url, headers={**headers,'Range':'bytes=-10'}).content == original[-10:]
    assert client.get(url, headers={**headers,'Range':'bytes=10-'}).content == original[10:]
    for value in ['bytes=999999-', 'bytes=9-2', 'bytes=-0', 'bytes=0-1,3-4', 'invalid']:
        assert client.get(url, headers={**headers,'Range':value}).status_code == 416
    assert client.get(url, headers={**headers,'Range':'bytes=0-9','If-Range':'"old-revision"'}).status_code == 200
    app.dependency_overrides.pop(get_current_user)
    assert client.get(url, headers={'Range':'bytes=0-9'}).status_code == 401


@pytest.mark.parametrize('role', ['player', 'writer', 'architect', 'world_builder', 'admin'])
def test_identity_bridge_accepts_every_supported_shrecknet_role(monkeypatch, role):
    from app.api import deps
    monkeypatch.setattr(deps, '_signing_keys', lambda: {'key': {}})
    monkeypatch.setattr(deps.jwt, 'get_unverified_header', lambda token: {'kid':'key'})
    monkeypatch.setattr(deps.jwt, 'decode', lambda *args, **kwargs: {'sub':'reader', 'role':role})
    assert deps.get_current_user('test-token').role == role
