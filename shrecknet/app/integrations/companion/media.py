"""Optional source-bound media discovery; never fetch arbitrary image URLs."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, unquote, quote
import httpx
from PIL import Image
from app.core.config_store import get_settings
from app.graph.neo4j import get_driver
from app.integrations.companion.references import stable_id, target_for
from app.services.companion_authorization import grant


async def atlas_image(reference, settings):
    """Read the admitted graph node's own artwork, never category fallback artwork."""
    async with get_driver().session(database=settings.neo4j_database) as graph:
        result=await graph.run('MATCH (n) WHERE (n:EntityInstance OR n:Scene OR n:Milestone) AND n.ontology_id=$ontology AND coalesce(n.entity_instance_id,n.scene_id,n.milestone_id,n.id)=$id RETURN n.node_avatar_url AS image, coalesce(n.last_updated_date,n.updated_at) AS revision',
                               ontology=reference['ontology_id'],id=reference['node_id'])
        row=await result.single()
        return dict(row) if row else None


def local_image(url, settings):
    """Map only the configured storage URL namespace to a confined raster file."""
    if not isinstance(url,str): return None
    prefixes=[value.rstrip('/')+'/' for value in (getattr(settings,'media_public_url',None), settings.media_base_url) if value]
    base=next((prefix for prefix in sorted(prefixes,key=len,reverse=True) if url.startswith(prefix)),None)
    if base is None: return None
    relative=unquote(url[len(base):])
    if urlsplit(url).query or urlsplit(url).fragment or '\\' in relative: return None
    root=Path(settings.media_root).resolve()
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>10*1024*1024: return None
    try:
        with Image.open(path) as image:
            if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height>20_000_000: return None
            image.verify()
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        return path,digest
    except (OSError,ValueError,Image.DecompressionBombError):
        return None


async def resolve(turn, actor, evidence, settings):
    """One bounded catalog per admitted source; partial success survives siblings."""
    async def one(source):
        target=target_for(source)
        if not target: return source['evidence_id'],[]
        ref=target['reference']; kind=source['source_type']; rows=[]
        if kind=='atlas':
            current=await atlas_image(ref,settings)
            local=await asyncio.to_thread(local_image,current['image'],settings) if current else None
            if local:
                _,digest=local
                identifier=stable_id('a',source['evidence_id']+digest)
                rows=[{'id':identifier,'source_type':'atlas','source_revision':str(current['revision']) if current['revision'] else None,
                       'provenance_kind':'entity_image' if ref['node_label']=='EntityInstance' else 'scene_image',
                       'attribution':source['title'][:1000],
                       'delivery_ref':{'kind':'service','service':'shrecknet','path':f'/users/me/character-companion/turns/{turn.id}/assets/{identifier}'}}]
        elif kind=='shreckworld' and settings.shreckworld_base_url:
            prefix=f"/ontologies/{ref['ontology_id']}" if ref.get('ontology_id') else f"/shreckworlds/{quote(ref['shreckworld_id'],safe='')}"
            async with httpx.AsyncClient(base_url=settings.shreckworld_base_url,timeout=settings.companion_rich_response_timeout_s,follow_redirects=False) as client:
                response=await client.post(prefix+'/companion-media',json={'references':[source['reference']]},headers={'Authorization':f"Bearer {grant(turn,'shreckworld')}"})
                response.raise_for_status()
                media=response.json()['items']
            for entry in media[:4]:
                if entry['document_id']!=ref['document_id'] or entry['source_sha256']!=ref['source_sha256'] or entry['page_number']!=ref['page_number']: continue
                path=f"{prefix}/{'items' if ref.get('ontology_id') else 'library-items'}/{quote(ref['library_item_id'],safe='')}/documents/{quote(ref['document_id'],safe='')}/media/{entry['id']}?revision={quote(ref['source_sha256'],safe='')}"
                rows.append({'id':stable_id('a',source['evidence_id']+entry['id']),'source_type':'shreckworld','source_revision':ref['source_sha256'],
                    'provenance_kind':entry['kind'],'mime_type':'image/png','width':entry['width'],'height':entry['height'],
                    'attribution':source['title'][:1000],'delivery_ref':{'kind':'service','service':'shreckworld','path':path}})
        elif kind=='foundry' and (source.get('structured_evidence') or {}).get('image_available'):
            rows=[{'id':stable_id('a',source['evidence_id']),'source_type':'foundry','source_revision':ref.get('source_revision'),
                'provenance_kind':'document_image','attribution':source['title'][:1000],
                'delivery_ref':{'kind':'foundry_local','reference':ref}}]
        elif kind=='web':
            # Adapter admits only provider-declared, source-associated image metadata.
            for image in source['reference'].get('images',[])[:4]:
                rows.append({'id':stable_id('a',source['evidence_id']+image['url']),'source_type':'web','source_revision':None,
                    'provenance_kind':'provider_image','attribution':image['attribution'][:1000],
                    'delivery_ref':{'kind':'external','url':image['url']}})
        return source['evidence_id'],rows
    tasks=[asyncio.create_task(one(source)) for source in evidence[:20]]
    try:
        done,pending=await asyncio.wait(tasks,timeout=settings.companion_rich_response_timeout_s) if tasks else (set(),set())
        results = {task.result()[0]:task.result()[1] for task in done if not task.cancelled() and task.exception() is None}
        return {source['evidence_id']:results[source['evidence_id']] for source in evidence if source['evidence_id'] in results}
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


async def revalidate(turn, rich, settings):
    """Drop optional Atlas assets replaced since discovery; factual sources check separately."""
    from app.integrations.companion.references import stable_id
    targets={t['id']:t for t in rich['targets']}
    async def valid(asset):
        if asset['source_type']!='atlas': return asset['id']
        current=await atlas_image(targets[asset['target_id']]['reference'],settings)
        local=await asyncio.to_thread(local_image,current['image'],settings) if current else None
        if local and stable_id('a',asset['evidence_ids'][0]+local[1])==asset['id'] and (not asset['source_revision'] or str(current['revision'])==asset['source_revision']):
            return asset['id']
        return None
    tasks=[asyncio.create_task(valid(asset)) for asset in rich['assets']]
    try:
        done,_=await asyncio.wait(tasks,timeout=settings.companion_rich_response_timeout_s) if tasks else (set(),set())
        admitted={task.result() for task in done if not task.cancelled() and task.exception() is None}
        return admitted-{None}
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
