import pytest

from shrecknet_client.models import (
    CharacterAgentCreateRequest,
    CharacterAgentEmbeddedAspect,
    EmbodimentDraftCreate,
)
from shrecknet_client.resources import CharacterAgentsAPI


def profile_fixture():
    estimate = {"z": None, "point": None, "status": "unknown"}
    keys = ("integrity", "caution", "presence", "forbearance", "diligence", "curiosity", "sharing", "restlessness")
    return {"version": "dispositions-v1", "dispositional_traits": {key: estimate for key in keys}, "steadiness": estimate}


class Client:
    def __init__(self):
        self.calls = []

    async def raw_request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/character-agents":
            return {
                **{key: value for key, value in kwargs["json"].items() if key not in {"aspects", "goals", "trait_edits"}}, "id": "a1",
                "status": "active", "visibility": "private", "trait_profile": profile_fixture(),
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


@pytest.mark.asyncio
async def test_sdk_preserves_explicit_null_when_clearing_override():
    from shrecknet_client.models import CharacterAgentUpdate
    from shrecknet_client.character_traits import TraitEdit
    class PatchClient(Client):
        async def raw_request(self,method,path,**kwargs):
            self.calls.append((method,path,kwargs))
            return {'id':'a1','ontology_id':3,'entity_instance_id':'e1','embodied_entity_instance_id':'e1',
                'name':'Mara','background_story':'Story','status':'active','visibility':'private',
                'created_by_user_id':1,'created_at':'2026-09-21T00:00:00Z','updated_at':'2026-09-21T00:00:00Z',
                'trait_profile':profile_fixture()}
    client=PatchClient()
    await CharacterAgentsAPI(client).update('a1',CharacterAgentUpdate(trait_edits={'integrity':TraitEdit(point=None,reason='Resume evidence.')}))
    assert client.calls[0][2]['json']=={'trait_edits':{'integrity':{'point':None,'reason':'Resume evidence.'}}}


def test_sdk_types_unknown_profile_and_rejects_invalid_edits():
    from pydantic import ValidationError
    from shrecknet_client.character_traits import TraitEdit, TraitProfile
    profile=TraitProfile.model_validate(profile_fixture())
    assert profile.dispositional_traits['integrity'].point is None
    for value in (0,10,True,5.5):
        with pytest.raises(ValidationError): TraitEdit(point=value,reason='Invalid')


@pytest.mark.asyncio
async def test_sdk_metadata_and_evidence_paths():
    class ReadClient:
        def __init__(self): self.calls=[]
        async def raw_request(self,method,path,**kwargs):
            self.calls.append((method,path,kwargs))
            return {'traits':[]} if path.endswith('trait-definitions') else []
    client=ReadClient();api=CharacterAgentsAPI(client)
    await api.trait_definitions()
    await api.list_trait_evidence('a1',trait='integrity',revision=3)
    assert client.calls[0][1]=='/character-agents/trait-definitions'
    assert client.calls[1][1]=='/character-agents/a1/trait-evidence'
    assert client.calls[1][2]['params']['revision']==3
