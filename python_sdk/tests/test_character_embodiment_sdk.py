import pytest

from shrecknet_client.models import (
    CharacterAgentCreateRequest,
    CharacterAgentEmbeddedAspect,
    EmbodimentDraftCreate,
)
from shrecknet_client.resources import CharacterAgentsAPI


class Client:
    def __init__(self):
        self.calls = []

    async def raw_request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/character-agents":
            return {
                **kwargs["json"], "id": "a1",
                "embodied_entity_instance_id": kwargs["json"]["entity_instance_id"],
                "created_by_user_id": 1,
                "created_at": "2026-07-25T00:00:00Z",
                "updated_at": "2026-07-25T00:00:00Z",
            }
        return {"draft_id": "d1", "job_id": 1, "status": "queued", "draft_url": "/d1", "job_url": "/j1"}


@pytest.mark.asyncio
async def test_embodiment_sdk_start_and_create_contracts():
    client = Client()
    api = CharacterAgentsAPI(client)
    started = await api.start_embodiment(EmbodimentDraftCreate(ontology_id=3, entity_instance_id="e1"))
    agent = await api.create(
        CharacterAgentCreateRequest(
            ontology_id=3, entity_instance_id="e1", embodiment_draft_id="d1",
            name="Mara", background_story="Story",
            aspects=[CharacterAgentEmbeddedAspect(
                name="Leader", category="role", importance=5
            )],
        )
    )
    assert started.job_id == 1
    assert agent.id == "a1"
    assert client.calls[0][2]["json"] == {"ontology_id": 3, "entity_instance_id": "e1"}
    assert client.calls[1][2]["json"]["embodiment_draft_id"] == "d1"
