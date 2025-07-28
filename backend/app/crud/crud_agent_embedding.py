from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from app.models.model_agent_embedding import AgentEmbedding
from app.models.model_world_embedding import WorldEmbedding

async def set_embeddings(session: AsyncSession, agent_id: int, embedding_ids: List[int]) -> None:
    await session.execute(
        select(AgentEmbedding).where(AgentEmbedding.agent_id == agent_id)
    )
    await session.execute(
        AgentEmbedding.__table__.delete().where(AgentEmbedding.agent_id == agent_id)
    )
    for eid in embedding_ids:
        session.add(AgentEmbedding(agent_id=agent_id, embedding_id=eid))
    await session.commit()

async def get_embeddings(session: AsyncSession, agent_id: int) -> List[WorldEmbedding]:
    result = await session.execute(
        select(WorldEmbedding).join(AgentEmbedding, WorldEmbedding.id == AgentEmbedding.embedding_id).where(AgentEmbedding.agent_id == agent_id)
    )
    return result.scalars().all()

async def get_embedding_ids(session: AsyncSession, agent_id: int) -> List[int]:
    result = await session.execute(
        select(AgentEmbedding.embedding_id).where(AgentEmbedding.agent_id == agent_id)
    )
    return [row[0] for row in result.all()]
