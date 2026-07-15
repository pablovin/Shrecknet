from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.deps import get_user_service
from app.core.config_store import get_settings
from app.schemas.user import ServiceEmailSendRequest
from app.services.email_service import EmailService, get_email_service_status
from app.services.user_service import UserService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/email", tags=["internal-email"])


def require_smtp_service_token(authorization: str | None = Header(default=None)) -> None:
    expected = str(get_settings().smtp_service_token or "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid SMTP service token")


@router.post("/send", dependencies=[Depends(require_smtp_service_token)])
async def send_service_email(
    payload: ServiceEmailSendRequest,
    user_service: UserService = Depends(get_user_service),
) -> dict[str, bool]:
    user = await user_service.get_user(payload.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    settings = get_settings()
    health = await EmailService(settings).validate_and_record_status()
    if not health["configured"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "email_service_unavailable", "message": health["error"]},
        )
    try:
        await EmailService(settings).send_message(
            recipient=user.email,
            subject=payload.subject,
            message=payload.message,
        )
    except Exception:
        logger.exception("Trusted-service email delivery failed for user_id=%s", user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "email_delivery_failed", "message": "Unable to deliver email."},
        )
    logger.info("Trusted-service email sent to user_id=%s subject=%r", user.id, payload.subject)
    return {"sent": True}
