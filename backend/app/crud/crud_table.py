from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.model_table import Table, TableMember
from app.schemas.schema_table import TableCreate

async def create_table(session: AsyncSession, table_in: TableCreate, creator_id: int) -> Table:
    table = Table(
        world_id=table_in.world_id,
        name=table_in.name,
        crest_url=table_in.crest_url,
        created_by=creator_id,
    )
    session.add(table)
    await session.commit()
    await session.refresh(table)

    member_ids = set(table_in.member_ids or [])
    member_ids.add(creator_id)
    for uid in member_ids:
        session.add(TableMember(table_id=table.id, user_id=uid, is_gm=(uid == creator_id)))
    await session.commit()
    return table

async def get_tables_for_user(session: AsyncSession, user_id: int) -> List[Table]:
    result = await session.execute(
        select(Table).join(TableMember).where(TableMember.user_id == user_id)
    )
    return result.scalars().all()
