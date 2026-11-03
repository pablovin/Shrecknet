from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.character_agent import IdentityDescription
from app.schemas.character_traits import TraitProfile
from app.services import character_identity_summary_service as module


IDENTITY = IdentityDescription(identity_summary="A loyal guardian", psychological_summary="Duty tempers fear", personality_traits=[])


def setup_service(monkeypatch, *, existing=False, changed=False, competing=False):
    stamp = datetime(2026, 11, 3, tzinfo=timezone.utc)
    saved = SimpleNamespace(id="agent-1", entity_instance_id="entity-1", ontology_id=7,
                            name="Manual Mara", background_story="My authored background", updated_at=stamp,
                            identity_description=IDENTITY if existing else None)
    current = {"updated_at": "2026-11-04T00:00:00Z" if changed else stamp.isoformat(),
               "identity_description": IDENTITY.model_dump_json() if competing else None}
    writes = []
    class Tx:
        async def run(self, query, **params):
            if "RETURN agent" in query:
                return SimpleNamespace(single=AsyncMock(return_value={"agent": current}))
            writes.append((query, params))
            saved.identity_description = IDENTITY
    class Graph:
        async def execute_write(self, operation):
            return await operation(Tx())
    service = module.CharacterIdentitySummaryService(None, Graph())
    service.characters.get_agent = AsyncMock(return_value=saved)
    inputs = {"canonical_identity": {"alias": "Entity name", "authored_text": "Original entity text"},
              "trait_profile": TraitProfile(), "trait_evidence": [],
              "current_aspects": [{"name": "My aspect", "status": "active"}],
              "current_goals": [{"title": "My goal", "status": "active"}]}
    service.inputs.load_embodiment_input = AsyncMock(return_value=inputs)
    service.inputs.load_current_assignments = AsyncMock(return_value=(inputs["current_aspects"], inputs["current_goals"]))
    generator = SimpleNamespace(generate_identity_description=AsyncMock(return_value=IDENTITY))
    client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(module, "ShreckLLMClient", lambda **kwargs: client)
    monkeypatch.setattr(module, "EmbodyAgent", lambda **kwargs: generator)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(
        shreckllm_base_url="http://model", shreckllm_request_timeout_s=30, shreckllm_max_retries=0,
        shreckllm_chat_job_timeout_s=120, model_character_agent_character_incorporation={},
        model_character_agent_scene_interpretation={}, model_character_agent_psychological_interpretation={},
        character_agent_embodiment_validation_retries=1))
    return service, saved, generator, client, writes


@pytest.mark.asyncio
async def test_summary_uses_saved_manual_state_and_changes_only_the_narrative(monkeypatch):
    service, saved, generator, client, writes = setup_service(monkeypatch)
    assert await service.generate_missing("agent-1") is saved
    service.inputs.load_embodiment_input.assert_awaited_once_with("entity-1", 7, include_scenes=False)
    payload = generator.generate_identity_description.call_args.kwargs
    assert payload["canonical_identity"]["alias"] == "Manual Mara"
    assert "My authored background" in payload["canonical_identity"]["authored_text"]
    assert "Original entity text" in payload["canonical_identity"]["authored_text"]
    assert payload["current_aspects"][0]["name"] == "My aspect"
    assert payload["current_goals"][0]["title"] == "My goal"
    assert len(writes) == 1
    assert "SET agent.identity_description=" in writes[0][0]
    assert "trait_profile" not in writes[0][0]
    assert "HAS_ASPECT" not in writes[0][0] and "PURSUES" not in writes[0][0]
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_returns_existing_summary_without_model_call(monkeypatch):
    service, saved, generator, client, writes = setup_service(monkeypatch, existing=True)
    assert await service.generate_missing("agent-1") is saved
    service.inputs.load_embodiment_input.assert_not_awaited()
    generator.generate_identity_description.assert_not_awaited()
    assert writes == []


@pytest.mark.asyncio
async def test_provider_failure_preserves_saved_agent_and_closes_client(monkeypatch):
    service, saved, generator, client, writes = setup_service(monkeypatch)
    generator.generate_identity_description.side_effect = RuntimeError("Provider unavailable")
    with pytest.raises(HTTPException) as error:
        await service.generate_missing("agent-1")
    assert error.value.status_code == 502
    assert saved.identity_description is None and writes == []
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_profile_edit_does_not_receive_stale_summary(monkeypatch):
    service, _, _, client, writes = setup_service(monkeypatch, changed=True)
    with pytest.raises(HTTPException) as error:
        await service.generate_missing("agent-1")
    assert error.value.status_code == 409 and writes == []


@pytest.mark.asyncio
async def test_competing_summary_is_not_overwritten(monkeypatch):
    service, _, _, _, writes = setup_service(monkeypatch, competing=True)
    await service.generate_missing("agent-1")
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize('role,enabled,configured,expected', [
    ('player', True, True, 403), ('admin', False, True, 503),
    ('admin', True, False, 503), ('admin', True, True, 200),
])
async def test_summary_endpoint_enforces_admin_and_model_configuration(monkeypatch, role, enabled, configured, expected):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.api import deps
    from app.api.routers import character_agents as routes
    from app.graph.neo4j import get_neo4j_session
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(id=41, role=role)
    app.dependency_overrides[deps.get_db_session] = lambda: None
    app.dependency_overrides[get_neo4j_session] = lambda: None
    app.dependency_overrides[deps.get_audit_service] = lambda: SimpleNamespace(log_action=AsyncMock())
    def gate():
        if not enabled:
            raise HTTPException(status_code=503, detail='AI disabled')
    monkeypatch.setattr(routes, 'require_ai_agents_enabled', gate)
    monkeypatch.setattr(routes, 'is_shreckllm_configured', lambda settings: configured)
    from app.schemas.character_agent import CharacterAgentRead
    result = CharacterAgentRead(
        id='agent-1', ontology_id=7, entity_instance_id='entity-1', embodied_entity_instance_id='entity-1',
        name='Mara', background_story='A guardian', created_by_user_id=41,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), identity_description=IDENTITY)
    generate = AsyncMock(return_value=result)
    monkeypatch.setattr(module, 'CharacterIdentitySummaryService', lambda *args: SimpleNamespace(generate_missing=generate))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/character-agents/agent-1/identity-summary')
    assert response.status_code == expected
    if expected == 200:
        assert response.json()['identity_description']['identity_summary'] == IDENTITY.identity_summary
        generate.assert_awaited_once_with('agent-1')
    else:
        generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_input_does_not_scan_or_generate_scene_memories():
    from app.services.character_embodiment_service import CharacterEmbodimentService
    calls = []
    class Graph:
        async def run(self, query, **params):
            calls.append(query)
            if 'RETURN entity' in query:
                return SimpleNamespace(single=AsyncMock(return_value={'entity': {
                    'entity_instance_id': 'entity-1', 'ontology_id': 7,
                    'entity_definition_id': 3, 'alias': 'Mara', 'text': 'A guardian'}}))
            if 'RETURN agent' in query:
                return SimpleNamespace(single=AsyncMock(return_value={'agent': None}))
            raise AssertionError('Summary-only loading must not scan scenes')
    sql = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(name='Person', description='A person')),
                          execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))))
    result = await CharacterEmbodimentService(sql, Graph()).load_embodiment_input('entity-1', 7, include_scenes=False)
    assert result['canonical_identity']['alias'] == 'Mara'
    assert result['current_aspects'] == result['current_goals'] == []
    assert 'scenes' not in result and len(calls) == 2


@pytest.mark.asyncio
async def test_assignment_change_does_not_receive_stale_summary(monkeypatch):
    service, _, _, _, writes = setup_service(monkeypatch)
    service.inputs.load_current_assignments.return_value = ([], [])
    with pytest.raises(HTTPException) as error:
        await service.generate_missing("agent-1")
    assert error.value.status_code == 409 and writes == []
