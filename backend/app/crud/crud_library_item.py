from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional
import os

from app.models.model_library_item import LibraryItem

async def create_item(session: AsyncSession, item: LibraryItem) -> LibraryItem:
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item

async def get_items(session: AsyncSession, system: Optional[str] = None) -> List[LibraryItem]:
    stmt = select(LibraryItem)
    if system:
        stmt = stmt.where(LibraryItem.system == system)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_item(session: AsyncSession, item_id: int) -> Optional[LibraryItem]:
    return await session.get(LibraryItem, item_id)

async def update_item(session: AsyncSession, item_id: int, updates: dict) -> Optional[LibraryItem]:
    item = await session.get(LibraryItem, item_id)
    if not item:
        return None
    for k, v in updates.items():
        setattr(item, k, v)
    await session.commit()
    await session.refresh(item)
    return item

async def delete_item(session: AsyncSession, item_id: int) -> bool:
    item = await session.get(LibraryItem, item_id)
    if not item:
        return False
    if item.path and os.path.isfile(item.path):
        try:
            os.remove(item.path)
        except Exception:
            pass
    if item.cover_url:
        folder = os.path.dirname(item.cover_url)
        if os.path.isdir(folder):
            try:
                import shutil
                shutil.rmtree(folder)
            except Exception:
                pass
    try:
        from app.crud import crud_library_vectordb
        crud_library_vectordb.delete_item_vectors(item_id)
    except Exception:
        pass
    await session.delete(item)
    await session.commit()
    return True
