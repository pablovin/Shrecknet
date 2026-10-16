"""Browser session policy, credential rotation, CSRF contexts, and eligibility."""
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionCompat
from app.core.config_store import get_settings, UserCreationMode
from app.core.browser_auth import BrowserAuthSettings
from app.core.security import create_access_token
from app.models.auth_session import AuthRotation, AuthSession
from app.models.user import User
from app.repositories.auth_sessions import AuthSessionRepository
from app.schemas.browser_auth import BrowserSessionResponse, SessionUser, BrowserRegistration, BrowserRegistrationResponse
from app.services.user_service import account_login_failure, UserService, UserCreationStoppedError, EmailServiceUnavailableError


class SessionFailure(ValueError):
    def __init__(self, code: str, status: int = 401, retry_after: int = 60):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__(code)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class BrowserSessionService:
    def __init__(self, session: AsyncSession | AsyncSessionCompat, settings: BrowserAuthSettings) -> None:
        self.db = session
        self.settings = settings
        self.repository = AuthSessionRepository(session)
        self.cipher = Fernet(settings.secret.encode())

    def csrf(self, session_id: str, previous: str | None = None) -> str:
        if previous:
            try:
                payload = json.loads(self.cipher.decrypt(previous.encode(), ttl=600))
                if payload["sid"] == session_id:
                    return previous
            except (InvalidToken, ValueError, KeyError):
                pass
        return self.cipher.encrypt(json.dumps({"sid": session_id, "nonce": secrets.token_urlsafe(24)}).encode()).decode()

    def validate_csrf(self, cookie: str, header: str, session_id: str) -> None:
        if not cookie or not header or len(header) > 1024 or not hmac.compare_digest(cookie, header):
            raise SessionFailure("csrf_invalid", 403)
        try:
            payload = json.loads(self.cipher.decrypt(cookie.encode(), ttl=600))
            if payload["sid"] != session_id:
                raise ValueError("session mismatch")
        except (InvalidToken, ValueError, KeyError) as exc:
            raise SessionFailure("csrf_invalid", 403) from exc

    async def limit(self, action: str, identity: str, maximum: int, window: int = 60) -> None:
        # Keyed digest prevents identifiers/IPs being stored directly in counters.
        key = hmac.new(self.settings.secret.encode(), f"{action}:{identity}".encode(), hashlib.sha256).hexdigest()
        if not await self.repository.rate_limit(key, int(time.time()), window, maximum):
            raise SessionFailure("rate_limited", 429, window)

    async def active(self, session_id: str, user_id: int | None = None) -> tuple[AuthSession, User]:
        row = await self.repository.get(session_id)
        now = int(time.time())
        if not row or row.revoked_at is not None or min(row.absolute_expires_at, row.idle_expires_at) <= now or (user_id is not None and row.user_id != user_id):
            raise SessionFailure("invalid_session")
        user = await self.db.get(User, row.user_id)
        if not user or account_login_failure(user):
            await self.repository.revoke(row.id, now)
            await self.db.commit()
            raise SessionFailure("invalid_session")
        return row, user

    def response(self, row: AuthSession, user: User) -> BrowserSessionResponse:
        now = int(time.time())
        expiry = min(now + self.settings.access_minutes * 60, row.absolute_expires_at, row.idle_expires_at)
        role = user.role.value if hasattr(user.role, "value") else str(user.role)
        return BrowserSessionResponse(
            user=SessionUser.model_validate(user),
            access_token=create_access_token(str(user.id), role, expires_minutes=self.settings.access_minutes, session_id=row.id, expires_at=expiry),
            access_expires_at=datetime.fromtimestamp(expiry, timezone.utc),
            session_expires_at=datetime.fromtimestamp(row.absolute_expires_at, timezone.utc),
            idle_expires_at=datetime.fromtimestamp(row.idle_expires_at, timezone.utc),
            csrf_token=self.csrf(row.id),
        )

    async def create(self, user: User, persistent: bool, previous_credential: str = "") -> tuple[BrowserSessionResponse, str, AuthSession]:
        now = int(time.time())
        session_id = secrets.token_urlsafe(24)
        credential = f"{session_id}.{secrets.token_urlsafe(32)}"
        absolute = now + (self.settings.persistent_days * 86400 if persistent else self.settings.session_hours * 3600)
        idle = min(absolute, now + self.idle_seconds(persistent))
        row = AuthSession(id=session_id, user_id=user.id, refresh_digest=digest(credential), created_at=now, absolute_expires_at=absolute, idle_expires_at=idle, persistent=persistent)
        self.db.add(row)
        await self.revoke_credential(previous_credential)
        await self.db.commit()
        return self.response(row, user), credential, row

    def idle_seconds(self, persistent: bool) -> int:
        return self.settings.persistent_idle_days * 86400 if persistent else self.settings.session_idle_hours * 3600

    async def refresh(self, credential: str, request_id: str) -> tuple[BrowserSessionResponse, str, AuthSession]:
        session_id = credential.partition(".")[0]
        row, user = await self.active(session_id)
        now = int(time.time())
        old = digest(credential)
        if not hmac.compare_digest(old, row.refresh_digest):
            return await self._duplicate(row, user, old, request_id, now)
        replacement = f"{session_id}.{secrets.token_urlsafe(32)}"
        idle = min(row.absolute_expires_at, now + self.idle_seconds(row.persistent))
        rotated = await self.repository.rotate(session_id, old, digest(replacement), now, idle)
        if not rotated:
            await self.db.rollback()
            row, user = await self.active(session_id)
            return await self._duplicate(row, user, old, request_id, now)
        self.db.add(AuthRotation(digest=old, session_id=session_id, request_id=request_id, rotated_at=now, replacement=self.cipher.encrypt(replacement.encode()).decode()))
        await self.db.commit()
        row = await self.repository.get(session_id)
        return self.response(row, user), replacement, row

    async def _duplicate(self, row: AuthSession, user: User, old: str, request_id: str, now: int) -> tuple[BrowserSessionResponse, str, AuthSession]:
        previous = await self.repository.rotation(old)
        if previous and previous.session_id == row.id:
            if now - previous.rotated_at < 30:
                if previous.request_id == request_id and previous.replacement:
                    try:
                        replacement = self.cipher.decrypt(previous.replacement.encode(), ttl=30).decode()
                    except InvalidToken as exc:
                        raise SessionFailure("invalid_session") from exc
                    if hmac.compare_digest(digest(replacement), row.refresh_digest):
                        return self.response(row, user), replacement, row
                # Parallel tab: no cookie mutation and no revocation; caller obtains
                # current cookie then starts a fresh, serialized renewal.
                raise SessionFailure("refresh_conflict", 409)
            await self.repository.revoke(row.id, now)
            await self.db.commit()
        raise SessionFailure("invalid_session")


    async def register(self, payload: BrowserRegistration, previous_credential: str = "") -> tuple[BrowserRegistrationResponse, tuple[BrowserSessionResponse, str, AuthSession] | None]:
        users = UserService(self.db)
        if get_settings().user_creation_mode == UserCreationMode.STOPPED:
            raise SessionFailure("registration_closed", 403)
        if not await users.has_any_users():
            raise SessionFailure("unavailable", 503)
        try:
            user = await users.register_user(payload.model_dump(exclude={"remember_me"}))
        except UserCreationStoppedError as exc:
            raise SessionFailure("registration_closed", 403) from exc
        except EmailServiceUnavailableError as exc:
            raise SessionFailure("unavailable", 503) from exc
        except (ValueError, IntegrityError) as exc:
            await self.db.rollback()
            raise SessionFailure("account_conflict", 409) from exc
        if get_settings().email_verification_enabled and user.email_verified_at is None:
            return BrowserRegistrationResponse(outcome="verification_required", verification_email_sent=user.verification_email_sent), None
        if account_login_failure(user):
            return BrowserRegistrationResponse(outcome="pending_approval"), None
        issued = await self.create(user, payload.remember_me, previous_credential)
        return BrowserRegistrationResponse(outcome="authenticated", session=issued[0]), issued

    async def revoke_credential(self, credential: str) -> None:
        """Know the actual current/prior secret, not merely a public session ID."""
        if not credential:
            return
        row = await self.repository.get(credential.partition(".")[0])
        if row:
            token_digest = digest(credential)
            previous = await self.repository.rotation(token_digest)
            if hmac.compare_digest(row.refresh_digest, token_digest) or (previous and previous.session_id == row.id):
                await self.repository.revoke(row.id, int(time.time()))
