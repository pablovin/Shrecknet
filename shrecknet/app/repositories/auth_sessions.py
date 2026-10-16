"""Database-only operations; compare-and-swap rotation is safe across workers."""
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionCompat
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from app.models.auth_session import AuthRateLimit, AuthRotation, AuthSession


class AuthSessionRepository:
    def __init__(self, session: AsyncSession | AsyncSessionCompat) -> None:
        self.session = session

    async def get(self, session_id: str) -> AuthSession | None:
        result = await self.session.execute(select(AuthSession).where(AuthSession.id == session_id).execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def rotation(self, digest: str) -> AuthRotation | None:
        return await self.session.get(AuthRotation, digest)

    async def rotate(self, session_id: str, old: str, new: str, now: int, idle: int) -> bool:
        result = await self.session.execute(update(AuthSession).where(
            AuthSession.id == session_id, AuthSession.refresh_digest == old,
            AuthSession.revoked_at.is_(None), AuthSession.absolute_expires_at > now,
            AuthSession.idle_expires_at > now,
        ).values(refresh_digest=new, idle_expires_at=idle).execution_options(synchronize_session=False))
        return result.rowcount == 1

    async def revoke(self, session_id: str, now: int) -> None:
        await self.session.execute(update(AuthSession).where(AuthSession.id == session_id).values(revoked_at=now))

    async def revoke_user(self, user_id: int, now: int) -> None:
        await self.session.execute(update(AuthSession).where(AuthSession.user_id == user_id).values(revoked_at=now))

    async def rate_limit(self, key: str, now: int, window: int, maximum: int) -> bool:
        # Expired counters can be replaced; unique-key upsert serializes increments.
        await self.session.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= now))
        bind = self.session.get_bind()
        insert = postgres_insert if bind.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(AuthRateLimit).values(key=key, count=1, expires_at=now + window)
        await self.session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"count": AuthRateLimit.count + 1}))
        count = await self.session.scalar(select(AuthRateLimit.count).where(AuthRateLimit.key == key))
        await self.session.commit()
        return count <= maximum

    async def cleanup(self, now: int) -> None:
        await self.session.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= now))
        await self.session.execute(update(AuthRotation).where(AuthRotation.rotated_at <= now - 30, AuthRotation.replacement.is_not(None)).values(replacement=None))
        # Rotation digests stay until their family's absolute lifetime ends so
        # older refresh credentials can still trigger reuse detection.
        expired = select(AuthSession.id).where(AuthSession.absolute_expires_at <= now)
        await self.session.execute(delete(AuthRotation).where(AuthRotation.session_id.in_(expired)))
        await self.session.execute(delete(AuthSession).where(AuthSession.absolute_expires_at <= now))
