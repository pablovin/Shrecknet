from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routers import configurations
from app.services.email_service import EmailDeliveryError


@pytest.mark.asyncio
async def test_enabling_verification_requires_a_ready_smtp_connection(monkeypatch) -> None:
    settings = configurations.Settings(
        smtp_host="smtp.example.com",
        smtp_sender_email="no-reply@example.com",
        email_verification_frontend_url="https://example.com/verify-email",
    )
    monkeypatch.setattr(configurations, "get_settings", lambda: settings)

    async def failed_connection(self) -> None:
        raise EmailDeliveryError("unavailable")

    monkeypatch.setattr(configurations.EmailService, "verify_connection", failed_connection)
    with pytest.raises(HTTPException, match="SMTP configuration could not be verified"):
        await configurations._put_config_payload({"email_verification_enabled": True})
