"""Protected bootstrap, durable progress, and paid-work retry boundaries."""
import asyncio
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.browser_auth import BrowserAuthSettings
from app.core.runtime_secrets import provision_runtime_secrets, read_runtime_secret
from app.models.user import User, UserRole
from app.repositories.installation_repository import InstallationRepository
from app.services.installation_service import InstallationService, InstallationConflict


@pytest.fixture
def installation(monkeypatch, tmp_path):
    monkeypatch.setenv('SHRECKNET_INSTALLATION_ENABLED', 'true')
    monkeypatch.setenv('SHRECKNET_RUNTIME_SECRETS_DIR', str(tmp_path / 'keys'))
    monkeypatch.setenv('SHRECKNET_DATA_DIR', str(tmp_path))
    provision_runtime_secrets()
    settings = BrowserAuthSettings(enabled=True, secret=Fernet.generate_key().decode(), allowed_origins=['http://localhost'], secure_cookies=False)
    monkeypatch.setattr('app.api.routers.browser_auth.get_browser_auth_settings', lambda: settings)
    monkeypatch.setattr('app.core.browser_auth.get_browser_auth_settings', lambda: settings)
    return read_runtime_secret('installation_claim').strip()


@pytest.mark.asyncio
async def test_owner_claim_requires_host_secret_and_csrf_and_closes(client, session_maker, installation):
    status = await client.get('/setup/installation/status')
    assert status.json() == {'enabled': True, 'owner_required': True}
    assert installation not in status.text
    payload = {'username': 'keeper', 'full_name': 'World Keeper', 'email': 'keeper@example.com', 'password': 'a long memorable password', 'timezone': 'UTC', 'claim_token': installation}
    assert (await client.post('/auth/claim', json=payload)).status_code == 403
    csrf = (await client.get('/auth/csrf')).json()['csrf_token']
    headers = {'Origin': 'http://localhost', 'X-CSRF-Token': csrf}
    bad = await client.post('/auth/claim', json={**payload, 'claim_token': 'x' * 43}, headers=headers)
    assert bad.status_code == 403
    assert installation not in bad.text
    assert (await client.post('/users/', json={key: value for key, value in payload.items() if key != 'claim_token'})).status_code == 403
    response = await client.post('/auth/claim', json=payload, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()['outcome'] == 'authenticated'
    assert response.json()['session']['user']['role'] == 'admin'
    assert response.headers['cache-control'] == 'no-store'
    assert (await client.get('/setup/installation/status')).json()['owner_required'] is False
    csrf = (await client.get('/auth/csrf')).json()['csrf_token']
    again = await client.post('/auth/claim', json={**payload, 'username': 'second', 'email': 'second@example.com'}, headers={**headers, 'X-CSRF-Token': csrf})
    assert again.status_code == 403
    async with session_maker() as db:
        owners = (await db.execute(select(User).where(User.role == UserRole.ADMIN))).scalars().all()
        assert len(owners) == 1
        assert owners[0].email_verified_at is not None


@pytest.mark.asyncio
async def test_claim_disabled_preserves_existing_bootstrap(client, monkeypatch):
    monkeypatch.delenv('SHRECKNET_INSTALLATION_ENABLED', raising=False)
    assert (await client.get('/setup/installation/status')).json() == {'enabled': False, 'owner_required': False}
    assert (await client.get('/setup/installation/state')).status_code in {401, 404}


def configured_service(tmp_path, monkeypatch):
    service = InstallationService(InstallationRepository(tmp_path))
    monkeypatch.setattr(service, 'fingerprint', lambda keys: 'current')
    with service.repository.transaction() as (_, state):
        state.world_id = 7
        state.tour_completed = True
        state.agents_configured = True
        state.model_fingerprint = 'current'
        state.verified_targets = ['model_elder_planner']
    return service


def test_verification_reservation_is_durable_and_not_duplicated(tmp_path, monkeypatch):
    service = configured_service(tmp_path, monkeypatch)
    first, enqueue = service.begin_verification()
    second, enqueue_again = service.begin_verification()
    assert enqueue is True and enqueue_again is False
    assert first.verification_id == second.verification_id
    restored = InstallationRepository(tmp_path).read()
    assert restored.verification_status == 'queued'
    with pytest.raises(InstallationConflict, match='verification_required'):
        service.finish()


def test_failed_verification_can_be_retried_but_running_cannot(tmp_path, monkeypatch):
    service = configured_service(tmp_path, monkeypatch)
    old, _ = service.begin_verification()
    with service.repository.transaction() as (_, state):
        state.verification_status = 'failed'
    new, dispatch = service.begin_verification()
    assert dispatch and new.verification_id != old.verification_id


def test_timeout_and_configuration_changes_invalidate_pass(tmp_path, monkeypatch):
    service = configured_service(tmp_path, monkeypatch)
    service.begin_verification()
    with service.repository.transaction() as (_, state):
        state.verification_started_at = 1
    assert service.status().verification_code == 'worker_timeout'
    with service.repository.transaction() as (_, state):
        state.verification_status = 'passed'
    assert service.finish().completed
    monkeypatch.setattr(service, 'fingerprint', lambda keys: 'changed')
    assert service.status().completed is False
    with pytest.raises(InstallationConflict):
        service.finish()


@pytest.mark.asyncio
async def test_retry_world_creation_reuses_saved_world(tmp_path):
    calls = []
    async def create(template):
        calls.append(template)
        return SimpleNamespace(ontology_id=7, name='fantasy')
    service = InstallationService(InstallationRepository(tmp_path))
    async def find_existing(name): return None
    setup = SimpleNamespace(resume_default_world=create, ontology_service=SimpleNamespace(repository=SimpleNamespace(get_by_name=find_existing)))
    assert (await service.create_world('fantasy', setup)).world_id == 7
    assert (await service.create_world('horror', setup)).world_id == 7
    assert calls == ['fantasy']


def test_claim_secret_is_stable_private_and_not_overwritten(installation, monkeypatch, tmp_path):
    provision_runtime_secrets()
    assert read_runtime_secret('installation_claim').strip() == installation
    assert ((tmp_path / 'keys/installation_claim').stat().st_mode & 0o777) == 0o600

@pytest.mark.asyncio
async def test_example_retry_reuses_page_and_scene():
    from app.services.installation_example import ensure_example, TITLE
    class Instances:
        def __init__(self): self.pages = []; self.scenes = []; self.creations = 0
        async def list_instances(self, **kwargs): return self.pages
        async def create_instance(self, payload, **kwargs):
            assert kwargs == {'trigger_background_jobs': False}
            self.creations += 1
            page = SimpleNamespace(instance_id='page-1', name=TITLE, entities=[SimpleNamespace(alias=e.alias, entity_instance_id=str(i)) for i, e in enumerate(payload.entities)])
            self.pages.append(page)
            return page
        async def list_scenes(self, page_id): return self.scenes
        async def create_scene(self, page_id, payload, **kwargs):
            assert payload.derived_from.entity_instance_id == '0'
            assert len(payload.milestones) == 1
            self.scenes.append(payload)
    instances = Instances()
    definitions = [SimpleNamespace(name=name, id=index) for index, name in enumerate(['Story', 'NPCs', 'Places'], 1)]
    first = await ensure_example(7, instances, definitions)
    second = await ensure_example(7, instances, definitions)
    assert first == second and instances.creations == 1 and len(instances.scenes) == 1

@pytest.mark.asyncio
async def test_model_worker_does_not_duplicate_or_expose_provider_failures(tmp_path, monkeypatch):
    from app.core.config_store import LLMModelTarget
    service = configured_service(tmp_path, monkeypatch)
    target = LLMModelTarget(provider='test', name='test-model')
    monkeypatch.setattr('app.services.installation_service.get_settings', lambda: SimpleNamespace(shreckllm_base_url='http://test', model_elder_planner=target))
    calls = []
    class LLM:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def chat(self, **kwargs):
            calls.append(kwargs)
            raise ValueError('private-provider-secret')
    monkeypatch.setattr('app.integrations.llm.shreckllm_client.ShreckLLMClient', LLM)
    state, _ = service.begin_verification()
    await service.verify_models(state.verification_id)
    await service.verify_models(state.verification_id)
    saved = service.status()
    assert len(calls) == 1 and saved.verification_status == 'failed'
    assert saved.verification_code == 'model_check_failed'
    assert 'private-provider-secret' not in saved.model_dump_json()

def test_later_assignments_keep_previous_installation_roles_in_verification(tmp_path, monkeypatch):
    from app.core.config_store import LLMModelTarget
    service = configured_service(tmp_path, monkeypatch)
    writes = []
    monkeypatch.setattr('app.services.installation_service.update_settings', lambda values: writes.append(values))
    state = service._save_models({'model_architect_scene_chunking'}, LLMModelTarget(provider='test', name='new-model'))
    assert state.verified_targets == ['model_architect_scene_chunking', 'model_elder_planner']
    assert 'model_elder_planner' not in writes[0]

@pytest.mark.asyncio
async def test_setup_does_not_adopt_an_existing_named_world(tmp_path):
    async def existing(name): return SimpleNamespace(id=42)
    setup = SimpleNamespace(ontology_service=SimpleNamespace(repository=SimpleNamespace(get_by_name=existing)))
    service = InstallationService(InstallationRepository(tmp_path))
    with pytest.raises(InstallationConflict, match='world_name_exists'):
        await service.create_world('fantasy', setup)
    assert service.repository.read().world_id is None
