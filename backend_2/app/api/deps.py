from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.ontology_service import OntologyService


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


async def get_ontology_service(
    session: AsyncSession = Depends(get_db_session),
) -> OntologyService:
    return OntologyService(session)
