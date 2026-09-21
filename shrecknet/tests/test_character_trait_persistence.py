"""Manual audit, atomic append conflict, and backend/SDK wire compatibility."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from app.schemas.character_agent import CharacterAgentRead, CharacterAgentUpdate, CharacterTimelineProjection
from app.schemas.character_traits import TraitEdit, TraitProfile, trait_metadata
from app.services.character_agent_service import CharacterAgentService


class Result:
    def __init__(self,row=None): self.row=row
    async def single(self): return self.row


class ManualGraph:
    def __init__(self):
        self.calls=[]
        self.agent=dict(id='a',ontology_id=1,embodied_entity_instance_id='e',name='Mara',background_story='Story',
            status='active',visibility='private',created_by_user_id=1,
            created_at='2026-09-21T00:00:00Z',updated_at='2026-09-21T00:00:00Z',trait_profile=TraitProfile().storage_json())
        self.latest={'revision_number':4,'active_aspect_ids':'["aspect-1"]','active_goal_ids':'["goal-1"]'}
    async def execute_write(self,fn): return await fn(self)
    async def run(self,query,**params):
        self.calls.append((query,params))
        if 'RETURN agent, latest' in query: return Result({'agent':dict(self.agent),'latest':self.latest})
        if 'SET agent += $changes' in query:
            self.agent.update(params['changes'])
            return Result({'node':dict(self.agent)})
        return Result()


@pytest.mark.asyncio
async def test_manual_edit_creates_typed_snapshot_and_actor_audit():
    graph=ManualGraph()
    result=await CharacterAgentService(None,graph).update_agent('a',CharacterAgentUpdate(
        trait_edits={'integrity':TraitEdit(point=8,reason='Authored by administrator.')}),user_id=12)
    assert result.trait_profile.dispositional_traits['integrity'].point==8
    snapshots=[p['props'] for q,p in graph.calls if 'CREATE (revision:CharacterIdentityRevision)' in q]
    changes=[p['props'] for q,p in graph.calls if 'CREATE (change:CharacterIdentityChange)' in q]
    assert snapshots[0]['revision_number']==5
    assert json.loads(snapshots[0]['active_aspect_ids'])==['aspect-1']
    assert json.loads(snapshots[0]['active_goal_ids'])==['goal-1']
    assert changes[0]['actor_user_id']==12 and changes[0]['field_name']=='integrity'
    assert changes[0]['provenance_type']=='manual' and changes[0]['justification']=='Authored by administrator.'
    assert json.loads(changes[0]['new_value'])['point']==8
    assert changes[0]['evidence_ids']=='[]'
    assert 'point' not in json.loads(graph.agent['trait_profile'])['dispositional_traits']['integrity']


@pytest.mark.asyncio
async def test_append_rejects_stale_identity_before_any_timeline_writes():
    class StaleTx:
        def __init__(self): self.calls=[]
        async def run(self,query,**params):
            self.calls.append(query)
            return Result({'r':{'id':'newer','revision_number':7}})
    tx=StaleTx()
    timeline=CharacterTimelineProjection.model_validate({'revisions':[{'revision_number':6,'name':'Mara'}]})
    with pytest.raises(HTTPException) as exc:
        await CharacterAgentService(None,None)._persist_timeline_tx(tx,{'id':'a'},timeline,'now',
            append=True,provider=None,model=None,prompt_version=None)
    assert exc.value.status_code==409
    assert len(tx.calls)==1 and 'CREATE ' not in tx.calls[0]


def test_sdk_reads_backend_profile_without_contract_drift(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'python_sdk'))
    from shrecknet_client.models import CharacterAgentRead as SDKRead
    from shrecknet_client.models import CharacterAgentUpdate as SDKUpdate
    graph=ManualGraph()
    backend=CharacterAgentRead.model_validate({**graph.agent,'entity_instance_id':'e',
        'trait_profile':TraitProfile.model_validate_json(graph.agent['trait_profile'])})
    sdk=SDKRead.model_validate(backend.model_dump(mode='json'))
    assert sdk.trait_profile.dispositional_traits['integrity'].point is None
    edit=SDKUpdate(trait_edits={'integrity':{'point':None,'reason':'Resume inference.'}})
    assert CharacterAgentUpdate.model_validate(edit.model_dump(exclude_unset=True)).trait_edits['integrity'].point is None


@pytest.mark.asyncio
async def test_trait_metadata_and_raw_evidence_have_correct_auth_dependencies():
    from app.api.routers.character_agents import router
    from app.api.deps import get_current_admin_user, get_current_user
    routes={route.path:route for route in router.routes}
    metadata=next(route for path,route in routes.items() if path.endswith('/trait-definitions'))
    evidence=next(route for path,route in routes.items() if path.endswith('/trait-evidence'))
    assert get_current_user in [dep.call for dep in metadata.dependant.dependencies]
    assert get_current_admin_user in [dep.call for dep in evidence.dependant.dependencies]
    value=await metadata.endpoint(SimpleNamespace())
    assert value==trait_metadata() and len(value['traits'])==9


@pytest.mark.parametrize("revisions", [[], [
    {"revision_number": 0, "name": "Mara"},
    {"revision_number": 2, "name": "Mara"},
]])
def test_timeline_rejects_missing_baseline_or_unmatched_revisions(revisions):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CharacterTimelineProjection.model_validate({"revisions": revisions})


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['removed', 'detached', 'edited', 'backdated'])
async def test_architect_append_rejects_invalid_history_before_llm(case):
    from app.services.character_embodiment_service import CharacterEmbodimentService
    from app.services.character_trait_service import scene_digest
    original = dict(scene_id='s1', name='Choice', description='Original', created_at='002')
    current = dict(original)
    if case == 'edited':
        current['description'] = 'Rewritten'
    new = dict(scene_id='s2', name='Later', description='New', created_at='001')
    scenes = ([current] if case not in ('removed', 'detached') else []) + [new]
    class Graph:
        async def run(self, query, **params):
            async def rows():
                if case != 'removed':
                    yield {'id': 's1', 'time': '002', 'digest': scene_digest(original)}
            return rows()
    service = CharacterEmbodimentService(None, Graph())
    async def inputs(*args):
        return dict(agent_id='a', processed_scene_ids={'s1'}, scenes=scenes,
            source_groups=[dict(source_id='source', source_alias='Source', scenes=scenes)])
    service.load_embodiment_input = inputs
    with pytest.raises(ValueError, match='regeneration'):
        await service.append_created_scenes(agent_id='a', entity_id='e', ontology_id=1,
            created_scene_ids={'s2'}, llm_client=None, settings=None)


@pytest.mark.asyncio
async def test_timeline_scope_change_aborts_before_persistence():
    from test_character_embodiment import BatchLLM, _agent, _canonical, scenes
    from app.jobs.character_agent.profile import _build_timeline
    result = await _agent(BatchLLM()).run(source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1))
    timeline = CharacterTimelineProjection.model_validate_json(
        _build_timeline('e', 'Mara', _canonical(), TraitProfile(), [], [], None, [result]))
    class WrongScope:
        def __init__(self): self.calls = []
        async def run(self, query, **params):
            self.calls.append(query)
            return Result({'scoped_scene_ids': []})
    tx = WrongScope()
    with pytest.raises(HTTPException) as exc:
        await CharacterAgentService(None, None)._persist_timeline_tx(tx, {'id': 'a'}, timeline,
            'now', provider=None, model=None, prompt_version=None)
    assert exc.value.status_code == 409
    assert len(tx.calls) == 1 and 'CREATE ' not in tx.calls[0]
