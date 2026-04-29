from __future__ import annotations

from typing import Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.config import get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def _fetch_shrecknet_user(token: str) -> dict[str, Any]:
    settings = get_settings()
    base_url = settings.shrecknet_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{base_url}/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user payload")
    return payload


async def get_authenticated_user(token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
    return await _fetch_shrecknet_user(token)


async def get_admin_or_world_builder(user: dict[str, Any] = Depends(get_authenticated_user)) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    if role not in {"admin", "world_builder"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or world_builder role required",
        )
    return user
