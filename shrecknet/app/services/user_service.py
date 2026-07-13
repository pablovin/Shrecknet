from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.core.config_store import UserCreationMode, get_settings
from app.models.ontology import OntologyEntity
from app.models.user import User, UserApprovalStatus, UserRole, user_entities
from app.repositories.user_repository import UserRepository

_registration_lock = asyncio.Lock()


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
        return user

    async def authenticate_user(self, username: str, password: str) -> User | None:
        """Authenticate user by username or email with password."""
        # Try to find user by username first
        user = await self.repository.get_by_username(username)

        # If not found by username, try by email
        if not user:
            user = await self.repository.get_by_email(username)

        # If still not found, authentication fails
        if not user:
            return None

        # Verify password
        if not verify_password(password, user.hashed_password):
            return None

        # SQLAlchemy column defaults are populated on flush, so preserve legacy
        # in-memory model compatibility while persisted rows are always explicit.
        if (user.approval_status or UserApprovalStatus.APPROVED) != UserApprovalStatus.APPROVED:
            return None

        return user

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
