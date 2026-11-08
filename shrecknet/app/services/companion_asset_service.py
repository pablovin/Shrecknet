"""Owner-only delivery of saved Companion Atlas artwork, revalidated on each read."""
from app.integrations.companion.media import atlas_image, local_image
from app.integrations.companion.references import stable_id
from app.models.character_companion import CompanionTurn
from app.repositories.character_companion_repository import fail


async def atlas_asset(service, actor, turn_id, asset_id):
    turn=await service.repo.owned(CompanionTurn,turn_id,actor.id)
    await service.chat(actor,turn.chat_id)
    rich=(turn.result or {}).get('rich_response') or {}
    asset=next((a for a in rich.get('assets',[]) if a['id']==asset_id and a['source_type']=='atlas'),None)
    if not asset: fail(404,'asset_unavailable')
    target=next((t for t in rich.get('targets',[]) if t['id']==asset['target_id']),None)
    if not target or target['kind']!='atlas': fail(404,'asset_unavailable')
    await service.character(actor,turn.character_agent_id,queryable=True)
    from app.services.companion_scope_service import CompanionScopeService
    from fastapi import HTTPException
    try:
        await CompanionScopeService(service.session).get(target['reference']['ontology_id'])
    except HTTPException as exc:
        if exc.status_code == 422:
            fail(404, 'asset_unavailable')
        raise
    from app.core.config_store import get_settings
    import asyncio
    settings=get_settings()
    current=await atlas_image(target['reference'],settings)
    local=await asyncio.to_thread(local_image,current['image'],settings) if current else None
    if not local: fail(404,'asset_unavailable')
    path,digest=local
    if stable_id('a',asset['evidence_ids'][0]+digest)!=asset_id or (asset['source_revision'] and str(current['revision'])!=asset['source_revision']):
        fail(409,'asset_changed')
    return path
