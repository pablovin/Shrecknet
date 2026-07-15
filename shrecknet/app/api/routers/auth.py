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

    user, failure_code = await service.authenticate_user_with_reason(str(identifier), str(password))
    if user is None:
        messages = {
            "invalid_credentials": "Incorrect username/email or password.",
            "pending_approval": "Your account is waiting for moderation approval.",
            "account_not_approved": "Your account is not approved for sign-in.",
            "email_not_verified": "Confirm your email address before signing in.",
        }
        raise HTTPException(
            status_code=(status.HTTP_401_UNAUTHORIZED if failure_code == "invalid_credentials" else status.HTTP_403_FORBIDDEN),
            detail={"code": failure_code or "invalid_credentials", "message": messages.get(failure_code or "", "Unable to sign in.")},
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return Token(access_token=create_access_token(subject=str(user.id), role=role))


@router.get("/jwks")
def get_jwks() -> dict[str, list[dict[str, str]]]:
    return jwks()
