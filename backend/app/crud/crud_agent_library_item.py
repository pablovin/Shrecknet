from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List

from app.models.model_agent_library_item import AgentLibraryItem
from app.models.model_library_item import LibraryItem

async def add_item(session: AsyncSession, agent_id: int, item_id: int) -> AgentLibraryItem:
    link = AgentLibraryItem(agent_id=agent_id, item_id=item_id)
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return link

async def get_items(session: AsyncSession, agent_id: int) -> List[LibraryItem]:
    result = await session.execute(
        select(LibraryItem).join(AgentLibraryItem, LibraryItem.id == AgentLibraryItem.item_id).where(AgentLibraryItem.agent_id == agent_id)
    )
    return result.scalars().all()

async def delete_item(session: AsyncSession, agent_id: int, item_id: int) -> bool:
    link = await session.get(AgentLibraryItem, (agent_id, item_id))
    if not link:
        return False
    await session.delete(link)
    await session.commit()
    return True

async def get_item_ids(session: AsyncSession, agent_id: int) -> List[int]:
    result = await session.execute(
        select(AgentLibraryItem.item_id).where(AgentLibraryItem.agent_id == agent_id)
    )
    return [row[0] for row in result.all()]
