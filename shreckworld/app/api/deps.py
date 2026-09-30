from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.request import urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.core.config import get_settings


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
_jwks: dict[str, dict] | None = None
_jwks_expires_at = 0.0


@dataclass(frozen=True)
class ShrecknetPrincipal:
    user_id: str
    role: str


def _signing_keys() -> dict[str, dict]:
    global _jwks, _jwks_expires_at
    if _jwks is not None and time.monotonic() < _jwks_expires_at:
        return _jwks
    try:
        with urlopen(get_settings().shrecknet_jwks_url, timeout=3) as response:  # nosec: configured internal URL
            keys = {str(key["kid"]): key for key in json.loads(response.read())["keys"]}
        if not keys:
            raise ValueError("no keys")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Shrecknet identity service is unavailable") from exc
    _jwks, _jwks_expires_at = keys, time.monotonic() + 300
    return keys


def get_current_user(token: str = Depends(oauth2_scheme)) -> ShrecknetPrincipal:
    global _jwks_expires_at
    try:
        kid = str(jwt.get_unverified_header(token).get("kid") or "")
        key = _signing_keys().get(kid)
        if key is None:
            _jwks_expires_at = 0  # signing-key rotation
            key = _signing_keys().get(kid)
        if key is None:
            raise JWTError("unknown signing key")
        settings = get_settings()
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.shrecknet_jwt_audience, issuer=settings.shrecknet_jwt_issuer)
        user_id, role = str(claims.get("sub") or ""), str(claims.get("role") or "")
        if not user_id or role not in {"admin", "world_builder", "writer", "player"}:
            raise JWTError("invalid claims")
        return ShrecknetPrincipal(user_id=user_id, role=role)
    except HTTPException:
        raise
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Shrecknet access token", headers={"WWW-Authenticate": "Bearer"}) from exc


def require_world_manager(principal: ShrecknetPrincipal = Depends(get_current_user)) -> ShrecknetPrincipal:
    if principal.role not in {"admin", "world_builder"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="World-builder privileges required")
    return principal
