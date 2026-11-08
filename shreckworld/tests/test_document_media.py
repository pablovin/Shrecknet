"""Protected revision-aware media backfill does not mutate retrieval projections."""
import hashlib
import pytest
from app.db.session import session_factory
from app.models import ShreckWorldDocument, ShreckWorldLibraryItem
from test_upload_metadata_api import api  # noqa: F401
from test_ontology_items import direct  # noqa: F401


def prepare(direct):
    import fitz
    client,headers,_=direct
    pdf=fitz.open();page=pdf.new_page(width=400,height=600);page.insert_text((30,40),'Book passage')
    raw=pdf.tobytes();pdf.close()
    item=client.post('/ontologies/7/items',headers=headers,params={'title':'Illustrated book'},files={'pdf':('book.pdf',raw,'application/pdf')}).json()
    digest=hashlib.sha256(raw).hexdigest()
    with session_factory()() as session:
        session.add(ShreckWorldDocument(id='doc',library_item_id=item['id'],is_active=True,source_sha256=digest,
            parser_name='test',parser_version='1',embedding_version='unchanged',embedding_model='test',embedding_fingerprint='unchanged',embedding_dimension=1,manifest_path='original'))
        session.commit()
    return client,headers,item,digest


def test_thumbnail_is_private_revision_bound_and_backfill_is_idempotent(direct):
    client,headers,item,digest=prepare(direct)
    path=f"/ontologies/7/items/{item['id']}/documents/doc/media/p1-thumbnail"
    response=client.get(path,headers=headers,params={'revision':digest})
    assert response.status_code==200,response.text
    assert response.content.startswith(b'\x89PNG')
    assert response.headers['cache-control']=='private, no-store'
    assert client.get(path,headers=headers,params={'revision':digest}).content==response.content
    assert client.get(path,headers=headers,params={'revision':'wrong'}).status_code==409
    assert client.get(path.replace('/7/','/8/'),headers=headers,params={'revision':digest}).status_code==404
    assert client.get(path.replace('p1-thumbnail','p2-thumbnail'),headers=headers,params={'revision':digest}).status_code==404
    with session_factory()() as session:
        doc=session.get(ShreckWorldDocument,'doc')
        assert doc.embedding_fingerprint=='unchanged' and doc.embedding_version=='unchanged' and doc.manifest_path=='original'
        doc.is_active=False;session.commit()
    assert client.get(path,headers=headers,params={'revision':digest}).status_code==404


def test_media_grant_checks_scope_and_book_selection(direct,monkeypatch):
    client,headers,item,digest=prepare(direct)
    checked=[]
    async def authorize(token,scope,payload):checked.append((scope.ontology_id,payload.library_item_ids))
    monkeypatch.setattr('app.services.companion_retrieval.authorize',authorize)
    ref={'library_item_id':item['id'],'document_id':'doc','source_sha256':digest,'page_number':1}
    response=client.post('/ontologies/7/companion-media',headers=headers,json={'references':[ref]})
    assert response.status_code==200,response.text
    assert checked==[(7,[item['id']])]
    assert response.json()['items'][0]['kind']=='page_thumbnail'
    assert client.post('/ontologies/8/companion-media',headers=headers,json={'references':[ref]}).status_code==404
    assert client.post('/ontologies/7/companion-media',headers=headers,json={'references':[]}).status_code==422
    assert client.post('/ontologies/7/companion-media',headers=headers,json={'references':[dict(ref,page_number=True)]}).status_code==422


def test_cannot_serve_media_after_source_delete(direct):
    client,headers,item,digest=prepare(direct)
    path=f"/ontologies/7/items/{item['id']}/documents/doc/media/p1-thumbnail"
    assert client.get(path,headers=headers,params={'revision':digest}).status_code==200
    assert client.delete(f"/ontologies/7/items/{item['id']}",headers=headers).status_code==204
    assert client.get(path,headers=headers,params={'revision':digest}).status_code==404
