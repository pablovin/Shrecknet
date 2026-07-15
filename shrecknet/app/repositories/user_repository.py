from __future__ import annotations

from typing import Any, Iterable, Sequence

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserApprovalStatus
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list(self) -> Sequence[User]:
        result = await self.session.execute(
            select(User).options(selectinload(User.entities))
        )
        return result.scalars().all()

    async def list_by_ids(self, user_ids: Iterable[int]) -> Sequence[User]:
        ids = list(user_ids)
        if not ids:
            return []
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.entities))
            .where(User.id.in_(ids))
        )
        return result.scalars().all()

    async def list_by_approval_status(
        self, approval_status: UserApprovalStatus
    ) -> Sequence[User]:
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.entities))
            .where(User.approval_status == approval_status)
            .order_by(User.id)
        )
        return result.scalars().all()

    async def get(self, user_id: int) -> User | None:
        result = await self.session.execute(
            select(User).options(selectinload(User.entities)).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.entities))
            .where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).options(selectinload(User.entities)).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def get_by_verification_token_hash(self, token_hash: str) -> User | None:
        result = await self.session.execute(
            select(User).options(selectinload(User.entities)).where(
                User.email_verification_token_hash == token_hash
            )
        )
        return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> User:
        user = User(**data)
        await self.save(user)
        await self.session.refresh(user)
        return user

    async def update(self, user: User, data: dict[str, Any]) -> User:
        for key, value in data.items():
            setattr(user, key, value)
        await self.save(user)
        await self.session.refresh(user)
        return user

    async def remove(self, user: User) -> None:
        await self.delete(user)

    async def has_any(self) -> bool:
        result = await self.session.execute(select(func.count()).select_from(User))
        return bool(result.scalar())
