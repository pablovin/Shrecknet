"""Revision-bound PDF thumbnails/illustrations; no embedding or active-document writes.

Media files and atomic page manifests live beside their owning source. A missing
manifest can be backfilled one bounded page at a time, independently of indexing.
Deletion uses the existing source lifecycle directory cleanup.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from fastapi import HTTPException
from app.repositories.source_scope import active_document_for_item

MEDIA_ID = re.compile(r'^p([1-9][0-9]{0,5})-(thumbnail|image-[0-2])$')


def document(session, item, document_id, revision):
    record=active_document_for_item(session,item.id,document_id)
    if record is None:
        raise HTTPException(404,'Document unavailable')
    if not revision or record.source_sha256!=revision or item.source_sha256!=revision:
        raise HTTPException(409,'Source changed')
    return record


def root_for(item, doc):
    return Path(item.storage_path or item.pdf_path).parent/'media'/doc.source_sha256/doc.id


def atomic_write(path, content):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.media-')
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(content)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def catalog(item, doc, page_number):
    if type(page_number) is not int or not 1<=page_number<=100000:
        raise HTTPException(422,'Invalid page')
    root=root_for(item,doc)
    manifest=root/f'p{page_number}.json'
    if manifest.is_file(): return json.loads(manifest.read_text())
    path=Path(item.storage_path or item.pdf_path)
    if path.suffix.lower()!='.pdf': return []
    if not path.is_file() or path.stat().st_size>200*1024*1024:
        raise HTTPException(404,'Source media unavailable')
    # Fingerprint before extraction: never attach a new file to an old revision.
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
    if digest.hexdigest()!=doc.source_sha256:
        raise HTTPException(409,'Source changed')
    import fitz
    rows=[]
    with fitz.open(path) as pdf:
        if page_number>len(pdf): raise HTTPException(404,'Page unavailable')
        page=pdf[page_number-1]
        if not 0<page.rect.width<=20000 or not 0<page.rect.height<=20000:
            raise HTTPException(422,'Invalid page dimensions')
        areas=[('thumbnail','page_thumbnail',page.rect)]
        # Reuse PDF's own image layout. A picture's page association does not
        # assert that it depicts the subject of the answer.
        for info in page.get_image_info()[:100]:
            rect=fitz.Rect(info['bbox']) & page.rect
            if rect.width>=32 and rect.height>=32:
                areas.append((f'image-{len(areas)-1}','book_illustration',rect))
            if len(areas)==4: break
        for suffix,kind,rect in areas:
            scale=min(1,960/max(rect.width,rect.height))
            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
            identifier=f'p{page_number}-{suffix}'
            atomic_write(root/(identifier+'.png'),pix.tobytes('png'))
            rows.append({'id':identifier,'kind':kind,'document_id':doc.id,'source_sha256':doc.source_sha256,
                'page_number':page_number,'width':pix.width,'height':pix.height,
                'bounds':list(rect)})
    atomic_write(manifest,json.dumps(rows).encode())
    return rows


def asset(session, item, document_id, identifier, revision):
    if not MEDIA_ID.fullmatch(identifier): raise HTTPException(404,'Media unavailable')
    doc=document(session,item,document_id,revision)
    rows=catalog(item,doc,int(MEDIA_ID.fullmatch(identifier).group(1)))
    if not any(row['id']==identifier for row in rows): raise HTTPException(404,'Media unavailable')
    return root_for(item,doc)/(identifier+'.png')


def asset_for_scope(session, scope, item_id, document_id, identifier, revision):
    from app.repositories.source_scope import scoped_item
    item = scoped_item(session, scope, item_id)
    if item is None:
        raise HTTPException(404, 'ShreckWorld library item not found')
    return asset(session, item, document_id, identifier, revision)


async def companion_catalog(session, scope, request, token):
    """Grant validation precedes all source reads; raster work stays off the event loop."""
    from starlette.concurrency import run_in_threadpool
    from app.services.companion_retrieval import authorize
    from app.schemas.shreckworld import ShreckWorldQueryRequest
    from app.repositories.source_scope import scoped_item
    ids = list(dict.fromkeys(ref.library_item_id for ref in request.references))
    await authorize(token, scope, ShreckWorldQueryRequest(query='media', library_item_ids=ids))
    items = []
    for ref in request.references:
        item = scoped_item(session, scope, ref.library_item_id)
        if item is None:
            raise HTTPException(404, 'ShreckWorld library item not found')
        doc = document(session, item, ref.document_id, ref.source_sha256)
        items.extend(await run_in_threadpool(catalog, item, doc, ref.page_number))
    return {'items': items}
