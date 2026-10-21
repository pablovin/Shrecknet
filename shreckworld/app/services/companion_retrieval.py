"""Retrieval-only turn grants never authorize ShreckWorld mutations."""
import httpx
from fastapi import HTTPException
from app.core.config import get_settings


async def authorize(token, world_id, payload):
    base = get_settings().shrecknet_jwks_url.rsplit('/auth/', 1)[0]
    async with httpx.AsyncClient(base_url=base, timeout=5, trust_env=False) as client:
        try:
            response = await client.get('/internal/companion/authorize', params={'tool': 'shreckworld'}, headers={'Authorization': f'Bearer {token}'})
            if response.status_code in {401, 403}:
                raise HTTPException(response.status_code, 'Invalid retrieval grant')
            response.raise_for_status()
            principal = response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503, 'Retrieval authorization unavailable') from exc
    scope = principal['scope']
    if scope.get('shreckworld_id') != world_id or payload.mode != 'evidence' or payload.retrieval_strategy != 'single':
        raise HTTPException(403, 'Retrieval scope mismatch')
    books = scope.get('library_item_ids') or []
    requested = payload.library_item_ids or ([payload.library_item_id] if payload.library_item_id else [])
    if books and (not requested or not set(requested).issubset(books)):
        raise HTTPException(403, 'Retrieval book scope mismatch')
    return principal
