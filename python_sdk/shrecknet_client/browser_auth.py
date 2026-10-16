"""Opt-in cookie session client; legacy SDK bearer login remains the default."""
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from .errors import raise_for_status
from .models import User


class BrowserSession(BaseModel):
    user: User
    access_token: str
    token_type: Literal["bearer"]
    access_expires_at: datetime
    session_expires_at: datetime
    idle_expires_at: datetime
    csrf_token: str


class BrowserRegistration(BaseModel):
    outcome: Literal["authenticated", "verification_required", "pending_approval"]
    session: BrowserSession | None = None
    verification_email_sent: bool | None = None


class BrowserAuthAPI:
    """Uses the underlying HTTPX cookie jar; never persists passwords to disk."""
    def __init__(self, client, origin: str):
        self.client = client
        self.origin = origin

    async def _post(self, path: str, body: dict, request_id: str | None = None):
        response = await self.client._client.get("/auth/csrf")
        raise_for_status(response.status_code, response.text)
        headers = {"Origin": self.origin, "X-CSRF-Token": response.json()["csrf_token"]}
        if request_id:
            headers["X-Request-ID"] = request_id
        response = await self.client._client.post(path, json=body, headers=headers)
        payload = response.json() if response.status_code != 204 else None
        raise_for_status(response.status_code, payload.get("detail") if isinstance(payload, dict) else None)
        return payload

    async def login(self, identifier: str, password: str, remember_me: bool = False) -> BrowserSession:
        result = BrowserSession.model_validate(await self._post("/auth/login", {"identifier": identifier, "password": password, "remember_me": remember_me}))
        self.client.set_token(result.access_token)
        return result

    async def register(self, *, username: str, full_name: str, email: str, password: str, timezone: str = "UTC", remember_me: bool = False) -> BrowserRegistration:
        result = BrowserRegistration.model_validate(await self._post("/auth/register", {"username": username, "full_name": full_name, "email": email, "password": password, "timezone": timezone, "remember_me": remember_me}))
        if result.session:
            self.client.set_token(result.session.access_token)
        return result

    async def refresh(self, request_id: str | None = None) -> BrowserSession:
        """Reuse request_id only to retry a lost response within 30 seconds."""
        result = BrowserSession.model_validate(await self._post("/auth/refresh", {}, request_id or str(uuid4())))
        self.client.set_token(result.access_token)
        return result

    async def logout(self) -> None:
        self.client.clear_token()
        await self._post("/auth/logout", {})

    async def verify_email(self, token: str) -> bool:
        return (await self._post("/auth/verify-email", {"token": token}))["verified"]

    async def resend_verification(self, email: str) -> None:
        await self._post("/auth/resend-verification", {"email": email})
