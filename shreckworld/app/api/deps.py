from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def require_admin(x_shreckworld_admin_token: str | None = Header(default=None)) -> None:
    configured = get_settings().admin_token
    if not configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ShreckWorld admin token is not configured")
    if x_shreckworld_admin_token != configured:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
