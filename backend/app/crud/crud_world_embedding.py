from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional

from app.models.model_world_embedding import WorldEmbedding

async def create_embedding(session: AsyncSession, embedding: WorldEmbedding) -> WorldEmbedding:
    session.add(embedding)
    await session.commit()
    await session.refresh(embedding)
    return embedding

async def get_embeddings(session: AsyncSession, world_id: int | None = None) -> List[WorldEmbedding]:
    stmt = select(WorldEmbedding)
    if world_id:
        stmt = stmt.where(WorldEmbedding.world_id == world_id)
    result = await session.execute(stmt)
    return result.scalars().all()

async def delete_embedding(session: AsyncSession, embedding_id: int) -> bool:
    emb = await session.get(WorldEmbedding, embedding_id)
    if not emb:
        return False
    await session.delete(emb)
    await session.commit()
    return True
