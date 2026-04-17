from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import AsyncSessionCompat
from app.models import User


class UserService:
    def authenticate(self, session: Session, email: str, password: str) -> User | None:
        result = session.execute(select(User).where(User.email == email, User.password == password))
        return result.scalar_one_or_none()

    async def authenticate_async(self, session: AsyncSessionCompat, email: str, password: str) -> User | None:
        result = await session.execute(select(User).where(User.email == email, User.password == password))
        return result.scalar_one_or_none()

    def get(self, session: Session, user_id: str) -> User | None:
        if not user_id.isdigit():
            return None
        result = session.execute(select(User).where(User.id == int(user_id)))
        return result.scalar_one_or_none()

    async def get_async(self, session: AsyncSessionCompat, user_id: str) -> User | None:
        if not user_id.isdigit():
            return None
        result = await session.execute(select(User).where(User.id == int(user_id)))
        return result.scalar_one_or_none()


user_service = UserService()
