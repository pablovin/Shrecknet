from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.user import User, UserApprovalStatus, UserRole
from app.services.user_service import UserService, _token_hash


@pytest.mark.asyncio
async def test_verification_token_activates_an_approved_user(session_maker, monkeypatch) -> None:
    settings = SimpleNamespace(email_verification_enabled=True)
    monkeypatch.setattr("app.services.user_service.get_settings", lambda: settings)
    async with session_maker() as session:
        user = User(
            username="unverified",
            email="unverified@example.com",
            hashed_password="2bb80d537b1da3e38bd30361aa855686bde0ba1b7a8e1a6a5f2a2c1f3e7c7c0d",
            password="",
            full_name="Unverified User",
            timezone="UTC",
            role=UserRole.PLAYER,
            approval_status=UserApprovalStatus.APPROVED,
            email_verification_token_hash=_token_hash("valid-token"),
            email_verification_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        session.add(user)
        await session.commit()
        service = UserService(session)
        assert await service.verify_email("valid-token") is True
        assert user.email_verified_at is not None
        assert await service.verify_email("valid-token") is False


@pytest.mark.asyncio
async def test_expired_verification_token_is_rejected(session_maker, monkeypatch) -> None:
    monkeypatch.setattr("app.services.user_service.get_settings", lambda: SimpleNamespace(email_verification_enabled=True))
    async with session_maker() as session:
        user = User(
            username="expired",
            email="expired@example.com",
            hashed_password="hash",
            password="",
            full_name="Expired User",
            timezone="UTC",
            role=UserRole.PLAYER,
            approval_status=UserApprovalStatus.APPROVED,
            email_verification_token_hash=_token_hash("expired-token"),
            email_verification_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        session.add(user)
        await session.commit()
        assert await UserService(session).verify_email("expired-token") is False
