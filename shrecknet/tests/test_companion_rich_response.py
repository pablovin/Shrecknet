"""Presentation grounding, bounded schemas, compatibility and durable projections."""
import copy
import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.integrations.companion.retrieval import evidence
from app.jobs.companion import rich_response as rich
from app.jobs.companion.query_contracts import Draft
from app.schemas.companion_rich_response import RichResponse
from app.schemas.character_companion import FoundryContext, normalized_turn_request
from app.integrations.companion.media import local_image
from test_companion_query_pipeline import harness  # noqa: F401


def atlas():
    return evidence('atlas','fiona','Fiona rules.',title='Fiona',reference={'node_id':'fiona','node_label':'EntityInstance','ontology_id':1},scope={'ontology_id':1})


def draft(source):
    return Draft(segments=[{'id':'s1','kind':'factual','text':'Fiona rules.','claims':['Fiona rules.'],
        'supports':[{'evidence_id':source['evidence_id'],'quote':source['excerpt'][:100]}]}])


def test_only_cited_source_produces_a_block_and_rejection_prunes_it():
    source=atlas();other=copy.deepcopy(source);other['evidence_id']='other'
    proposal,checks=rich.compose(draft(source),[source,other],{})
    assert len(checks)==1
    assert proposal['blocks'][0]['name']=='Fiona'
    segments=[{'id':'s1','kind':'factual','text':'Fiona rules.','evidence_ids':[source['evidence_id']]}]
    rejected=rich.finalize(proposal,segments,set())
    assert [b['type'] for b in rejected['blocks']]==['narrative']
    assert rejected['targets']==[]
    accepted=rich.finalize(proposal,segments,{checks[0].id})
    assert accepted['blocks'][1]['type']=='entity_card'
    assert rich.fallback(accepted,segments)=='Fiona rules.' # no duplicate title


def test_structured_table_preserves_null_zero_and_row_citations():
    source=evidence('foundry','Actor.hero','Visible sheet',title='Foundry snapshot · Hero',
        reference={'foundry_world_id':'table','uuid':'Actor.hero','source_revision':'1'},
        structured={'fields':[{'label':'HP','value':0},{'label':'Maximum','value':None}]})
    response=draft(source)
    response.segments[0].text='HP: 0. Maximum: unknown.'
    proposal,checks=rich.compose(response,[source],{})
    table=proposal['blocks'][0]
    assert table['type']=='table'
    assert [r['cells'][1]['value'] for r in table['rows']]==[0,None]
    assert all(c['evidence_ids']==[source['evidence_id']] for r in table['rows'] for c in r['cells'])
    assert checks[0].supports[0].quote in json.dumps(source['structured_evidence'],ensure_ascii=False)


def test_exact_book_excerpt_and_physical_page_are_preserved():
    source=evidence('shreckworld','book:chunk','An exact passage.',title='Rules',scope={'ontology_id':7},
        reference={'library_item_id':'book','document_id':'doc','source_sha256':'a'*64,'page_number':12,'page_label':'iv'})
    proposal,checks=rich.compose(draft(source),[source],{})
    block=proposal['blocks'][0]
    assert block['quote']==source['excerpt']
    assert block['document_ref']['page_number']==12 and block['document_ref']['page_label']=='iv'
    assert 'iv' in checks[0].text
    source['reference']['document_id']=None
    assert rich.compose(draft(source),[source],{})[0]['blocks']==[]


def test_schema_rejects_dangling_ids_non_rectangular_tables_and_duplicate_segments():
    source=atlas();proposal,checks=rich.compose(draft(source),[source],{})
    result=rich.finalize(proposal,[{'id':'s1','text':'Fiona','evidence_ids':[]}],{checks[0].id})
    result['blocks'][1]['asset_id']='invented'
    with pytest.raises(ValidationError):RichResponse.model_validate(result)
    result['blocks'][1]['asset_id']=None
    result['blocks'][0]['segment_ids']=['s1','s1']
    with pytest.raises(ValidationError):RichResponse.model_validate(result)


def test_local_media_never_maps_external_urls_traversal_or_svg(tmp_path):
    from PIL import Image
    Image.new('RGB',(10,10)).save(tmp_path/'portrait.png')
    settings=SimpleNamespace(media_root=str(tmp_path),media_base_url='/media',media_public_url=None)
    assert local_image('/media/portrait.png',settings)
    for url in ['https://evil.test/portrait.png','/media/../portrait.png','/media/%2e%2e/outside.png','/media/portrait.png?token=secret','/media/portrait.svg']:
        assert local_image(url,settings) is None


@pytest.mark.asyncio
async def test_verified_rich_reply_matches_event_history_and_snapshot(harness,monkeypatch):
    from app.jobs.companion import query_pipeline as pipeline
    from app.models.character_companion import CompanionTurn,CompanionMessage,CompanionEvent
    from app.repositories.character_companion_repository import CompanionRepository
    from sqlalchemy import select
    h=harness;h.settings.companion_rich_response_enabled=True
    async def retrieval(self,tool,query):return {'tool':tool,'status':'ok','evidence':[atlas()]}
    monkeypatch.setattr(pipeline.CompanionRetrieval,'retrieve',retrieval)
    async def no_media(*args):return {}
    monkeypatch.setattr(pipeline.companion_media,'resolve',no_media)
    h.replies['companion.claim_verification']=lambda tag,data:{'segments':[{'segment_id':s['id'],'supported':True,'reason':'Source supported'} for s in data['draft']['segments']]}
    turn=await h.run()
    assert turn['status']=='completed',turn['error']
    result=turn['result'];assert result['rich_response']['blocks'][1]['name']=='Fiona'
    async with h.sessions() as session:
        message=(await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id==turn['id'],CompanionMessage.kind=='final'))).scalar_one()
        details=(await CompanionRepository(session).message_records([message],h.uid))[0]['answer_details']
        event=(await session.execute(select(CompanionEvent).where(CompanionEvent.turn_id==turn['id'],CompanionEvent.type=='response.final'))).scalar_one()
        assert details['rich_response']==result['rich_response']==event.payload['rich_response']
        assert message.content==result['text']
        assert (await CompanionRepository(session).message_records([message],h.uid+1))[0]['answer_details'] is None


@pytest.mark.asyncio
async def test_rejected_visual_keeps_verified_narrative_without_another_repair(harness,monkeypatch):
    from app.jobs.companion import query_pipeline as pipeline
    h=harness;h.settings.companion_rich_response_enabled=True
    async def retrieval(self,tool,query):return {'tool':tool,'status':'ok','evidence':[atlas()]}
    async def no_media(*args):return {}
    monkeypatch.setattr(pipeline.CompanionRetrieval,'retrieve',retrieval)
    monkeypatch.setattr(pipeline.companion_media,'resolve',no_media)
    h.replies['companion.claim_verification']=lambda tag,data:{'segments':[{'segment_id':s['id'],'supported':s['id']=='s1','reason':'Unverified image attribution'} for s in data['draft']['segments']]}
    turn=await h.run()
    assert turn['result']['text']=='Fiona rules.'
    assert len(turn['result']['rich_response']['blocks'])==1
    assert len([tag for tag,_ in h.calls if tag=='companion.grounded_synthesis'])==1


def test_foundry_v2_image_negotiation_preserves_v1_idempotency():
    from test_companion_foundry_context import snapshot
    old=snapshot()
    normalized=normalized_turn_request({'foundry_context':FoundryContext.model_validate(old).model_dump(mode='json')})
    assert 'image_available' not in normalized['foundry_context']['documents'][0]
    old['documents'][0]['image_available']=True
    with pytest.raises(ValidationError):FoundryContext.model_validate(old)
    old['version']=2
    assert FoundryContext.model_validate(old).documents[0].image_available
    assert normalized_turn_request({'foundry_context':old})['foundry_context']['documents'][0]['image_available']


@pytest.mark.asyncio
async def test_media_timeout_keeps_a_grounded_reply(harness,monkeypatch):
    from app.jobs.companion import query_pipeline as pipeline
    h=harness;h.settings.companion_rich_response_enabled=True
    async def unavailable(*args):raise TimeoutError('optional')
    monkeypatch.setattr(pipeline.companion_media,'resolve',unavailable)
    turn=await h.run()
    assert turn['status']=='completed' and turn['result']['text']=='Fiona rules.'
    assert turn['result']['diagnostics']['media_outcome']=='unavailable'


def test_shared_fixture_and_generated_sdk_schema_stay_in_sync():
    from pathlib import Path
    from app.schemas.character_companion import MessageRead
    root=Path(__file__).parents[2]
    fixture=root/'shrecknet/tests/fixtures/companion-rich-response.v1.json'
    message=MessageRead.model_validate({**json.loads(fixture.read_text()),'turn_id':'turn','chat_id':'chat','committed':True,'created_at':'2026-11-08T12:00:00Z'})
    assert len(message.answer_details.rich_response.blocks)==6
    for other in ['integrations/foundry/shrecknet-foundry/tests/fixtures/companion-rich-response.v1.json','python_sdk/tests/fixtures/companion-rich-response.v1.json']:
        assert fixture.read_bytes()==(root/other).read_bytes()
    backend=(root/'shrecknet/app/schemas/companion_rich_response.py').read_text()
    sdk=(root/'python_sdk/shrecknet_client/companion_rich_response.py').read_text()
    assert backend.split('from __future__',1)[1]==sdk.split('from __future__',1)[1]


@pytest.mark.asyncio
async def test_saved_atlas_asset_rechecks_owner_scope_revision_and_file(harness,monkeypatch,tmp_path):
    from app.jobs.companion import query_pipeline as pipeline
    from app.services import companion_asset_service as assets
    from app.repositories.character_companion_repository import CompanionRepository
    from app.models.character_companion import CompanionChat
    from app.core import config_store
    from PIL import Image
    from fastapi import HTTPException
    h=harness;h.settings.companion_rich_response_enabled=True;h.settings.media_root=str(tmp_path)
    h.settings.media_base_url='/media';h.settings.media_public_url=None
    image_path=tmp_path/'portrait.png';Image.new('RGB',(10,10)).save(image_path)
    import hashlib
    identifier=rich.stable_id('a',atlas()['evidence_id']+hashlib.sha256(image_path.read_bytes()).hexdigest())
    async def retrieval(self,tool,query):return {'tool':tool,'status':'ok','evidence':[atlas()]}
    async def catalog(*args):return {atlas()['evidence_id']:[{'id':identifier,'source_type':'atlas','source_revision':'1','provenance_kind':'entity_image','attribution':'Fiona','delivery_ref':{'kind':'service','service':'shrecknet','path':f'/users/me/character-companion/turns/11111111-1111-4111-8111-111111111111/assets/{identifier}'}}]}
    current={'image':'/media/portrait.png','revision':'1'}
    async def image(*args):return current
    monkeypatch.setattr(pipeline.CompanionRetrieval,'retrieve',retrieval)
    monkeypatch.setattr(pipeline.companion_media,'resolve',catalog)
    monkeypatch.setattr(pipeline.companion_media,'atlas_image',image)
    monkeypatch.setattr(assets,'atlas_image',image)
    monkeypatch.setattr(config_store,'get_settings',lambda:h.settings)
    h.replies['companion.claim_verification']=lambda tag,data:{'segments':[{'segment_id':s['id'],'supported':True,'reason':'Source supported'} for s in data['draft']['segments']]}
    turn=await h.run()
    assert len(turn['result']['rich_response']['assets'])==1
    async with h.sessions() as session:
        repo=CompanionRepository(session)
        async def chat(actor,chat_id):return await repo.owned(CompanionChat,chat_id,actor.id)
        async def character(*args,**kwargs):return None
        service=SimpleNamespace(repo=repo,session=session,chat=chat,character=character)
        assert await assets.atlas_asset(service,SimpleNamespace(id=h.uid),turn['id'],identifier)==image_path
        with pytest.raises(HTTPException) as denied:await assets.atlas_asset(service,SimpleNamespace(id=h.uid+1),turn['id'],identifier)
        assert denied.value.status_code==404
        current['revision']='2'
        with pytest.raises(HTTPException) as changed:await assets.atlas_asset(service,SimpleNamespace(id=h.uid),turn['id'],identifier)
        assert changed.value.status_code==409


def test_optional_asset_loss_preserves_card_and_removes_image_and_gallery():
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/companion-rich-response.v1.json').read_text())
    result=rich.prune_assets(fixture['answer_details']['rich_response'],set())
    assert result['assets']==[]
    assert [b['type'] for b in result['blocks']]==['narrative','entity_card','table','book_excerpt']
    assert result['blocks'][1]['asset_id'] is None


@pytest.mark.asyncio
async def test_atlas_projection_uses_scoped_property_definitions_and_scalar_values(monkeypatch):
    from app.integrations.companion.atlas_properties import fields_for
    from app.repositories.ontology_repository import OntologyRepository
    calls=[]
    async def definitions(self,ontology_id,definition_id):
        calls.append((ontology_id,definition_id))
        return [SimpleNamespace(id=1,name='HP'),SimpleNamespace(id=2,name='Maximum'),SimpleNamespace(id=3,name='Hidden object')]
    monkeypatch.setattr(OntologyRepository,'list_properties',definitions)
    row=SimpleNamespace(properties={'entity_definition_id':4,'properties':json.dumps({'1':0,'2':None,'3':{'raw':'not a scalar'}})})
    assert await fields_for(row,7,None)==[{'label':'HP','value':0},{'label':'Maximum','value':None}]
    assert calls==[(7,4)]


@pytest.mark.asyncio
async def test_configured_web_images_require_permission_and_page_association(monkeypatch):
    import httpx
    from app.integrations.companion import retrieval
    from app.core.config_store import Settings
    settings=Settings(companion_query_pipeline_version='evidence_v2',companion_rich_response_enabled=True,
        companion_web_images_enabled=True,companion_web_enabled=True,companion_web_backend='json',companion_web_search_url='https://provider.test/search')
    monkeypatch.setattr(retrieval,'get_settings',lambda:settings)
    async def public(url):return isinstance(url,str) and url.startswith('https://')
    monkeypatch.setattr(retrieval,'safe_web_url',public)
    requests=[]
    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'results':[{'url':'https://source.test/page','title':'Source','snippet':'Actual provider excerpt',
            'images':[{'url':'https://images.test/picture.png','source_page_url':'https://source.test/page','relation':'page_image','attribution':'Publisher'},
                      {'url':'https://images.test/wrong.png','source_page_url':'https://other.test/page','relation':'page_image','attribution':'Wrong'}]}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(retrieval.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(provider),**kwargs))
    adapter=retrieval.CompanionRetrieval(SimpleNamespace(request={'context':{},'tools':{'web':'allow'}},execution_context={}),SimpleNamespace())
    rows=await adapter.web('public query')
    assert requests[0]['include_images']
    assert len(rows[0]['reference']['images'])==1
    assert rows[0]['excerpt']=='Actual provider excerpt'
    settings.companion_web_images_enabled=False
    rows=await adapter.web('public query')
    assert 'include_images' not in requests[1] and 'images' not in rows[0]['reference']


def test_table_budget_keeps_every_displayed_value_in_text_fallback():
    fields=[{'label':f'Field{i}','value':str(i)+'x'*499} for i in range(20)]
    source=evidence('foundry','Actor.hero','Sheet',title='Hero',reference={'foundry_world_id':'table','uuid':'Actor.hero','source_revision':'1'},structured={'fields':fields})
    response=draft(source);response.segments[0].text=' '.join(field['label'] for field in fields)
    proposal,_=rich.compose(response,[source],{})
    table=proposal['blocks'][0]
    assert table['type']=='table' and len(table['rows'])<20
    assert len(table['fallback_text'])<=4000
    for row in table['rows']:
        assert row['cells'][1]['value'] in table['fallback_text']


def test_block_cannot_survive_a_rejected_dependent_narrative_segment():
    source=atlas();proposal,checks=rich.compose(draft(source),[source],{})
    response=rich.finalize(proposal,[{'id':'safe-reaction','text':'I would be careful.','evidence_ids':[]}],{checks[0].id})
    assert [block['type'] for block in response['blocks']]==['narrative']


def test_presentation_enablement_cannot_change_legacy_pipeline_behavior():
    from app.core.config_store import Settings
    assert not Settings(companion_query_pipeline_version='legacy',companion_rich_response_enabled=True).companion_rich_response_active
    assert Settings(companion_query_pipeline_version='evidence_v2',companion_rich_response_enabled=True).companion_rich_response_active
    assert not Settings(companion_query_pipeline_version='evidence_v2',companion_rich_response_enabled=False).companion_rich_response_active
