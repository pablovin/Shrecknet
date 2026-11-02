"""World and source boundaries for the built-in creative workshops."""
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.ontology import Ontology


async def require_workshop_world(session: AsyncSession, ontology_id: int) -> None:
    # Worlds share the authenticated read audience. Workshop roles are checked by transport.
    if ontology_id < 1 or await session.get(Ontology, ontology_id) is None:
        raise HTTPException(status_code=404, detail="World not found")


def workshop_content_author_type(job_author_type: str) -> str:
    """Translate job provenance into the existing graph's human/agent vocabulary."""
    from app.models.background_job import AuthorType as JobAuthorType
    from app.models.ontology import AuthorType as ContentAuthorType

    if job_author_type == ContentAuthorType.HUMAN.value:
        return ContentAuthorType.HUMAN.value
    return (
        ContentAuthorType.HUMAN.value
        if JobAuthorType(job_author_type) == JobAuthorType.USER
        else ContentAuthorType.AGENT.value
    )
