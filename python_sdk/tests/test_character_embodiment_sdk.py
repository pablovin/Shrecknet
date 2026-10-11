import pytest

from shrecknet_client.models import (
    CharacterAgentCreateRequest,
    CharacterAgentEmbeddedAspect,
    EmbodimentDraftCreate,
)
from shrecknet_client.resources import CharacterAgentsAPI


def profile_fixture():
    estimate = {"point": None, "status": "unknown", "observation_count": 0, "observation_ids": []}
    keys = ("integrity", "caution", "presence", "forbearance", "diligence", "curiosity", "sharing", "restlessness")
    return {"version": "dispositions-v3-points-perspectives", "dispositional_traits": {key: estimate for key in keys}, "steadiness": estimate}


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
                name="I lead the settlement", category="role", in_focus=True
            )],
        )
    )
    assert started.job_id == 1
    assert agent.id == "a1"
    assert client.calls[0][2]["json"] == {"ontology_id": 3, "entity_instance_id": "e1", "replace_existing": False}
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
    for value in (-2, 0, 10, True, 2.5, float("nan")):
        with pytest.raises(ValidationError): TraitEdit(point=value,reason='Invalid')
    with pytest.raises(ValidationError): TraitEdit(z=1.2, reason='Old field')
    with pytest.raises(ValidationError):
        TraitProfile.model_validate({**profile_fixture(), 'version': 'dispositions-v2-bipolar-evidence'})


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


def test_sdk_reads_weighted_and_legacy_profiles_with_baseline():
    import pytest
    from pydantic import ValidationError
    from shrecknet_client.character_traits import TraitProfile, TraitEvidence
    keys = ('integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness')
    profile = dict(version='dispositions-v4-weighted-scenes',
        dispositional_traits={key: dict(point=None, status='unknown', observation_count=0) for key in keys},
        steadiness=dict(point=5, status='baseline', observation_count=0),
        observation_groups={'p': 'reviewed-event'}, unresolved_observation_ids=[])
    parsed = TraitProfile.model_validate(profile)
    assert parsed.steadiness.status == 'baseline'
    assert parsed.observation_groups == {'p': 'reviewed-event'}
    legacy = {**profile, 'version': 'dispositions-v3-points-perspectives',
              'steadiness': dict(point=None, status='unknown', observation_count=0)}
    assert TraitProfile.model_validate(legacy).steadiness.point is None
    profile['dispositional_traits']['caution'] = dict(point=5, status='baseline', observation_count=0)
    with pytest.raises(ValidationError):
        TraitProfile.model_validate(profile)
    item = TraitEvidence(id='t', trait='caution', evidence_kind='behavior', situation_type='unspecified',
        polarity='high', justification='A preference.', perspective_id='p', evidence_ids=['scene:s'],
        source_group_id='source', chronological_position=0, policy_version='point-evidence-v2-weighted-scenes',
        source='perspective', observation_group_id='event', independence_status='independent')
    assert item.source == 'perspective' and item.observation_group_id == 'event'
    missing_source = item.model_dump(exclude={'source'})
    with pytest.raises(ValidationError, match='explicit source'):
        TraitEvidence.model_validate(missing_source)


def test_neutral_prior_profile_and_legacy_directional_baseline_rejection():
    from pydantic import ValidationError
    from shrecknet_client.character_traits import TraitProfile
    keys = ('integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness')
    baseline = dict(point=5, status='baseline', observation_count=0, observation_ids=[])
    value = dict(version='dispositions-v5-neutral-prior',
        dispositional_traits={key: dict(baseline) for key in keys}, steadiness=dict(baseline),
        inferred_traits={'integrity': dict(baseline)})
    parsed = TraitProfile.model_validate(value)
    assert TraitProfile.model_validate_json(parsed.model_dump_json()) == parsed
    for version in ('dispositions-v3-points-perspectives', 'dispositions-v4-weighted-scenes'):
        with pytest.raises(ValidationError, match='only valid for Steadiness'):
            TraitProfile.model_validate({**value, 'version': version})
    for change in ({'point': 4}, {'observation_count': 1, 'observation_ids': ['p']}):
        invalid = {**value, 'dispositional_traits': {**value['dispositional_traits'],
            'integrity': {**baseline, **change}}}
        with pytest.raises(ValidationError):
            TraitProfile.model_validate(invalid)
