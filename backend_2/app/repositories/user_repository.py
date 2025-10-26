from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list(self) -> Sequence[User]:
        result = await self.session.execute(
            select(User).options(selectinload(User.entities))
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
