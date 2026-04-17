from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.security import create_access_token, jwks
from app.api.deps import get_user_service
from app.schemas.user import Token
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token", response_model=Token)
async def issue_token(
    request: Request,
    service: UserService = Depends(get_user_service),
) -> Token:
    content_type = (request.headers.get("content-type") or "").lower()
    identifier: str | None = None
    password: str | None = None

    if "application/json" in content_type:
        payload = await request.json()
        if isinstance(payload, dict):
            identifier = payload.get("username") or payload.get("email")
            password = payload.get("password")
    elif (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    ):
        form = await request.form()
        identifier = form.get("username") or form.get("email")
        password = form.get("password")

    if not identifier or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username/email and password are required",
        )

    user = await service.authenticate_user(str(identifier), str(password))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return Token(access_token=create_access_token(subject=str(user.id), role=role))


@router.get("/jwks")
def get_jwks() -> dict[str, list[dict[str, str]]]:
    return jwks()
