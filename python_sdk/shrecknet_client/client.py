from __future__ import annotations

from typing import Any

import httpx

from .errors import raise_for_status
from .models import Token, User, UserBootstrapStatus


class AsyncShrecknetClient:
    """Low-level async HTTP client for Shrecknet APIs."""

    def __init__(self, base_url: str = "http://localhost:8100", token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def __aenter__(self) -> "AsyncShrecknetClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def set_token(self, token: str) -> None:
        """Set bearer token used for authenticated requests."""
        self.token = token

    def clear_token(self) -> None:
        """Clear bearer token for subsequent requests."""
        self.token = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def raw_request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> Any:
        """Execute a raw HTTP request and map Shrecknet errors to SDK exceptions."""
        response = await self._client.request(method=method, url=path, params=params, json=json, headers=self._headers())
        detail = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail")
        except Exception:
            detail = response.text
        raise_for_status(response.status_code, detail)
        if response.status_code == 204:
            return None
        return response.json()

    async def bootstrap_status(self) -> UserBootstrapStatus:
        """Return whether at least one user already exists in Shrecknet."""
        data = await self.raw_request("GET", "/users/bootstrap")
        return UserBootstrapStatus.model_validate(data)

    async def register_user(
        self,
        *,
        username: str,
        password: str,
        email: str,
        full_name: str = "World Keeper",
        timezone: str = "UTC",
        role: str = "admin",
        entity_ids: list[int] | None = None,
    ) -> User:
        """Register a user through `/users/` for bootstrap or standard onboarding."""
        payload = {
            "username": username,
            "password": password,
            "email": email,
            "full_name": full_name,
            "timezone": timezone,
            "role": role,
            "entity_ids": entity_ids or [],
        }
        data = await self.raw_request("POST", "/users/", json=payload)
        return User.model_validate(data)

    async def login(self, username_or_email: str, password: str) -> Token:
        """Authenticate with username/email and store returned bearer token."""
        payload = {"password": password}
        if "@" in username_or_email:
            payload["email"] = username_or_email
        else:
            payload["username"] = username_or_email
        data = await self.raw_request("POST", "/auth/token", json=payload)
        token = Token.model_validate(data)
        self.token = token.access_token
        return token

    async def me(self) -> User:
        """Fetch profile of the current authenticated user."""
        data = await self.raw_request("GET", "/users/me")
        return User.model_validate(data)
