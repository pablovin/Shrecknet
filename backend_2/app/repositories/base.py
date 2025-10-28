from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Common helpers for repositories."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def save(self, instance) -> None:
        self.session.add(instance)
        await self.session.flush()

    async def delete(self, instance) -> None:
        self.session.delete(instance)
        await self.session.flush()
