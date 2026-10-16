from __future__ import annotations

import hashlib
import time
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.browser_auth import BrowserAuthSettings
from app.core.security import decode_access_token, get_password_hash, verify_password
from app.models.auth_session import AuthRotation, AuthSession
from app.models.user import User, UserApprovalStatus, UserRole
from app.services.auth_sessions import BrowserSessionService, SessionFailure
from app.services.user_service import UserService


@pytest.fixture
def browser_settings(monkeypatch):
    settings = BrowserAuthSettings(enabled=True, secret=Fernet.generate_key().decode(), allowed_origins=["http://localhost"], secure_cookies=False)
    # ASGI requests carry an explicitly trusted loopback browser origin.
    monkeypatch.setattr("app.api.routers.browser_auth.get_browser_auth_settings", lambda: settings)
    monkeypatch.setattr("app.core.browser_auth.get_browser_auth_settings", lambda: settings)
    return settings


async def seed_user(session, username="storyteller", approval=UserApprovalStatus.APPROVED):
    user = User(username=username, email=f"{username}@example.com", full_name="Story Teller", timezone="UTC", role=UserRole.PLAYER, approval_status=approval, hashed_password=get_password_hash("a long memorable password"))
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_rotation_replay_conflict_revocation_and_no_plaintext(session_maker, browser_settings):
    async with session_maker() as db:
        user = await seed_user(db)
        service = BrowserSessionService(db, browser_settings)
        initial, old, row = await service.create(user, True)
        assert initial.user.id == user.id
        assert decode_access_token(initial.access_token)["sid"] == row.id
        request_id = str(uuid4())
        _, new, row = await service.refresh(old, request_id)
        assert new != old
        _, replay, _ = await service.refresh(old, request_id)
        assert replay == new
        with pytest.raises(SessionFailure, match="refresh_conflict"):
            await service.refresh(old, str(uuid4()))
        rotation = await db.get(AuthRotation, hashlib.sha256(old.encode()).hexdigest())
        assert new not in rotation.replacement
        rotation.rotated_at = int(time.time()) - 31
        await db.commit()
        with pytest.raises(SessionFailure, match="invalid_session"):
            await service.refresh(old, request_id)
        with pytest.raises(SessionFailure):
            await service.active(row.id)


@pytest.mark.asyncio
async def test_expiry_and_approval_rechecked(session_maker, browser_settings):
    async with session_maker() as db:
        user = await seed_user(db)
        service = BrowserSessionService(db, browser_settings)
        _, credential, row = await service.create(user, False)
        user.approval_status = UserApprovalStatus.REJECTED
        await db.commit()
        with pytest.raises(SessionFailure):
            await service.refresh(credential, str(uuid4()))
        user.approval_status = UserApprovalStatus.APPROVED
        await db.commit()
        _, credential, row = await service.create(user, False)
        row.idle_expires_at = int(time.time()) - 1
        await db.commit()
        with pytest.raises(SessionFailure):
            await service.refresh(credential, str(uuid4()))


@pytest.mark.asyncio
async def test_cookie_csrf_login_logout_contract(client, session_maker, browser_settings):
    async with session_maker() as db:
        await seed_user(db)
    login_body = {"identifier": "storyteller", "password": "a long memorable password", "remember_me": True}
    denied = await client.post("/auth/login", json=login_body)
    assert denied.status_code == 403
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    headers = {"Origin": "http://localhost", "X-CSRF-Token": csrf}
    success = await client.post("/auth/login", json=login_body, headers=headers)
    assert success.status_code == 200, success.text
    assert "HttpOnly" in success.headers["set-cookie"]
    assert "SameSite=lax" in success.headers["set-cookie"]
    assert "Max-Age=" in success.headers["set-cookie"]
    assert success.headers["cache-control"] == "no-store"
    access = success.json()["access_token"]
    assert (await client.get("/auth/session", headers={"Authorization": f"Bearer {access}"})).status_code == 200
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    headers["X-CSRF-Token"] = csrf
    invalid_origin = await client.post("/auth/logout", json={}, headers={**headers, "Origin": "https://attacker.example"})
    assert invalid_origin.status_code == 403
    assert (await client.post("/auth/logout", json={}, headers=headers)).status_code == 204
    assert (await client.get("/auth/session", headers={"Authorization": f"Bearer {access}"})).status_code == 401


@pytest.mark.asyncio
async def test_registration_does_not_allow_role_or_bootstrap(client, session_maker, browser_settings):
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    headers = {"Origin": "http://localhost", "X-CSRF-Token": csrf}
    payload = {"full_name": "New Storyteller", "username": "newstory", "email": "new@example.com", "password": "a long memorable password", "timezone": "Europe/Brussels"}
    assert (await client.post("/auth/register", json={**payload, "role": "admin"}, headers=headers)).status_code == 422
    assert (await client.post("/auth/register", json=payload, headers=headers)).status_code in {403, 503}


@pytest.mark.asyncio
async def test_legacy_password_is_upgraded_and_password_change_revokes(session_maker, browser_settings):
    async with session_maker() as db:
        user = await seed_user(db)
        user.hashed_password = hashlib.sha256(b"a long memorable password").hexdigest()
        await db.commit()
        assert verify_password("a long memorable password", user.hashed_password)
        authenticated, _ = await UserService(db).authenticate_user_with_reason(user.username, "a long memorable password")
        assert authenticated.hashed_password.startswith("$argon2id$")
        service = BrowserSessionService(db, browser_settings)
        _, _, row = await service.create(user, True)
        await UserService(db).update_user(user, {"password": "a different long password"}, actor=user)
        with pytest.raises(SessionFailure):
            await service.active(row.id)


@pytest.mark.asyncio
async def test_rate_limit_is_persisted(session_maker, browser_settings):
    async with session_maker() as db:
        first = BrowserSessionService(db, browser_settings)
        await first.limit("login", "same-account", 1)
    async with session_maker() as db:
        with pytest.raises(SessionFailure, match="rate_limited"):
            await BrowserSessionService(db, browser_settings).limit("login", "same-account", 1)


def test_secure_cookie_configuration_and_password_hashes():
    with pytest.raises(ValueError):
        BrowserAuthSettings(enabled=True, secret=Fernet.generate_key().decode(), allowed_origins=["http://example.com"])
    hashed = get_password_hash("correct password")
    assert verify_password("correct password", hashed)
    assert not verify_password("wrong password", hashed)
