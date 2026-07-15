from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.core.config_store import UserCreationMode, get_settings
from app.models.ontology import OntologyEntity
from app.models.user import User, UserApprovalStatus, UserRole, user_entities
from app.repositories.user_repository import UserRepository
from app.services.email_service import EmailDeliveryError, EmailService, get_email_service_status

_registration_lock = asyncio.Lock()
_resend_requests: dict[str, datetime] = {}
_resend_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


class EmailServiceUnavailableError(RuntimeError):
    pass


class UserService:
    """Business logic for user registration, authentication, and profile management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = UserRepository(session)

    async def register_user(self, data: dict) -> User:
        # Prevent two in-process requests from both observing an empty user table.
        # The first account is a bootstrap escape hatch and must be the sole admin.
        async with _registration_lock:
            return await self._register_user(data)

    async def _register_user(self, data: dict) -> User:
        username = data["username"]
        email = data["email"]
        password = data.pop("password")
        entity_ids = data.pop("entity_ids", None)

        is_first_user = not await self.repository.has_any()
        if is_first_user:
            data["role"] = UserRole.ADMIN
            data["approval_status"] = UserApprovalStatus.APPROVED
        else:
            mode = get_settings().user_creation_mode
            if mode == UserCreationMode.STOPPED:
                raise UserCreationStoppedError("User creation is currently stopped")
            # Self-registration never grants an elevated role.
            data["role"] = UserRole.PLAYER
            data["approval_status"] = (
                UserApprovalStatus.PENDING
                if mode == UserCreationMode.MODERATED
                else UserApprovalStatus.APPROVED
            )

        await self._ensure_unique_constraints(username=username, email=email)

        if not is_first_user and getattr(get_settings(), "email_verification_enabled", False) and not get_email_service_status()["configured"]:
            raise EmailServiceUnavailableError("Email verification is temporarily unavailable")

        user = await self.repository.create(
            {
                **data,
                "hashed_password": get_password_hash(password),
            }
        )

        if entity_ids is not None:
            await self._apply_entity_assignments(user, entity_ids)

        await self.session.commit()
        await self.session.refresh(user, attribute_names=["entities"])
        user.verification_email_sent = None
        if not is_first_user and getattr(get_settings(), "email_verification_enabled", False):
            user.verification_email_sent = await self._issue_and_send_verification(user)
        return user

    async def authenticate_user(self, username: str, password: str) -> User | None:
        user, _ = await self.authenticate_user_with_reason(username, password)
        return user

    async def authenticate_user_with_reason(self, username: str, password: str) -> tuple[User | None, str | None]:
        """Authenticate and return a frontend-safe reason when credentials are valid but blocked."""
        # Try to find user by username first
        user = await self.repository.get_by_username(username)

        # If not found by username, try by email
        if not user:
            user = await self.repository.get_by_email(username)

        # If still not found, authentication fails
        if not user:
            return None, "invalid_credentials"

        # Verify password
        if not verify_password(password, user.hashed_password):
            return None, "invalid_credentials"

        # SQLAlchemy column defaults are populated on flush, so preserve legacy
        # in-memory model compatibility while persisted rows are always explicit.
        approval_status = user.approval_status or UserApprovalStatus.APPROVED
        if approval_status == UserApprovalStatus.PENDING:
            return None, "pending_approval"
        if approval_status != UserApprovalStatus.APPROVED:
            return None, "account_not_approved"
        if getattr(get_settings(), "email_verification_enabled", False) and user.email_verified_at is None:
            return None, "email_not_verified"

        return user, None

    async def verify_email(self, token: str) -> bool:
        if not token or len(token) > 512:
            return False
        user = await self.repository.get_by_verification_token_hash(_token_hash(token))
        now = datetime.now(timezone.utc)
        if not user or user.email_verified_at is not None or not user.email_verification_expires_at:
            return False
        expiry = user.email_verification_expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            return False
        await self.repository.update(user, {
            "email_verified_at": now,
            "email_verification_token_hash": None,
            "email_verification_expires_at": None,
        })
        await self.session.commit()
        return True

    async def resend_verification(self, email: str) -> None:
        normalized = email.strip().lower()
        async with _resend_lock:
            now = datetime.now(timezone.utc)
            previous = _resend_requests.get(normalized)
            if previous and (now - previous).total_seconds() < 60:
                return
            _resend_requests[normalized] = now
        if not getattr(get_settings(), "email_verification_enabled", False):
            return
        user = await self.repository.get_by_email(normalized)
        if user and user.email_verified_at is None:
            await self._issue_and_send_verification(user)

    async def _issue_and_send_verification(self, user: User) -> bool:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        await self.repository.update(user, {
            "email_verification_token_hash": _token_hash(token),
            "email_verification_expires_at": now.replace(microsecond=0) + timedelta(hours=24),
        })
        await self.session.commit()
        settings = get_settings()
        separator = "&" if "?" in settings.email_verification_frontend_url else "?"
        url = f"{settings.email_verification_frontend_url}{separator}token={token}"
        try:
            await EmailService(settings).send_verification(recipient=user.email, username=user.username, verification_url=url)
            return True
        except EmailDeliveryError:
            logger.exception("Verification email delivery failed for user id=%s", user.id)
            return False

    async def list_users(self) -> list[User]:
        users = await self.repository.list()
        return list(users)

    async def list_users_by_ids(self, user_ids: Iterable[int]) -> list[User]:
        users = await self.repository.list_by_ids(user_ids)
        return list(users)

    async def list_pending_users(self) -> list[User]:
        users = await self.repository.list_by_approval_status(UserApprovalStatus.PENDING)
        return list(users)

    async def has_any_users(self) -> bool:
        return await self.repository.has_any()

    async def get_user(self, user_id: int) -> User | None:
        return await self.repository.get(user_id)

    async def is_username_available(self, username: str) -> bool:
        existing = await self.repository.get_by_username(username)
        return existing is None

    async def is_email_available(self, email: str) -> bool:
        existing = await self.repository.get_by_email(email)
        return existing is None

    async def update_user(
        self,
        user: User,
        data: dict,
        *,
        actor: User,
    ) -> User:
        entity_ids = data.pop("entity_ids", None)
        new_password = data.pop("password", None)

        if "username" in data and data["username"] != user.username:
            await self._ensure_unique_constraints(username=data["username"])
        if "email" in data and data["email"] != user.email:
            await self._ensure_unique_constraints(email=data["email"])

        if "role" in data and actor.role != UserRole.ADMIN:
            raise PermissionError("Only admins can change user roles")

        update_payload = data.copy()
        if new_password:
            update_payload["hashed_password"] = get_password_hash(new_password)

        updated = await self.repository.update(user, update_payload)

        if entity_ids is not None:
            await self._apply_entity_assignments(updated, entity_ids)

        await self.session.commit()
        await self.session.refresh(updated, attribute_names=["entities"])
        return updated

    async def delete_user(self, user: User) -> None:
        await self.repository.remove(user)
        await self.session.commit()

    async def decide_registration(
        self,
        user: User,
        *,
        approved: bool,
        actor: User,
    ) -> User:
        if user.approval_status != UserApprovalStatus.PENDING:
            raise ValueError("Only pending users can be approved or rejected")

        updated = await self.repository.update(
            user,
            {
                "approval_status": (
                    UserApprovalStatus.APPROVED
                    if approved
                    else UserApprovalStatus.REJECTED
                ),
                "approval_decided_by_user_id": actor.id,
                "approval_decided_at": datetime.now(timezone.utc),
            },
        )
        await self.session.commit()
        await self.session.refresh(updated, attribute_names=["entities"])
        return updated

    async def _apply_entity_assignments(
        self, user: User, entity_ids: Iterable[int]
    ) -> None:
        unique_ids = {int(entity_id) for entity_id in entity_ids}
        if not unique_ids:
            if user.id is not None:
                await self.session.execute(
                    delete(user_entities).where(user_entities.c.user_id == user.id)
                )
            return

        result = await self.session.execute(
            select(OntologyEntity).where(OntologyEntity.id.in_(unique_ids))
        )
        entities = result.scalars().all()
        if len(entities) != len(unique_ids):
            missing = unique_ids - {entity.id for entity in entities}
            raise ValueError(f"Unknown entity ids: {sorted(missing)}")
        user.entities = list(entities)
        await self.session.flush()

    async def _ensure_unique_constraints(
        self,
        *,
        username: str | None = None,
        email: str | None = None,
    ) -> None:
        if username:
            existing_username = await self.repository.get_by_username(username)
            if existing_username:
                raise ValueError("Username already exists")
        if email:
            existing_email = await self.repository.get_by_email(email)
            if existing_email:
                raise ValueError("Email already exists")


class UserCreationStoppedError(PermissionError):
    """Raised when public registration is disabled by global configuration."""


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
