from typing import List, Set
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.model_table import Table, TableMember
from app.models.model_session import (
    Session,
    SessionAttendance,
    SessionPage,
    SessionPoll,
    SessionPollOption,
    SessionPollVote,
)
from app.schemas.schema_table import TableCreate, TableUpdate
from app.crud.crud_news import create_news
from app.schemas.schema_news import NewsCreate


async def create_table(
    session: AsyncSession, table_in: TableCreate, creator_id: int
) -> Table:
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
        session.add(
            TableMember(table_id=table.id, user_id=uid, is_gm=(uid == creator_id))
        )
    await session.commit()

    target_ids = [uid for uid in member_ids if uid != creator_id]
    if target_ids:
        news = NewsCreate(
            title="Added to Table",
            type="gaming_session",
            description=f"You were added to table '{table.name}'. View: /tables/{table.id}",
            user_ids=target_ids,
        )
        await create_news(session, news)
    return table


async def get_tables_for_user(session: AsyncSession, user_id: int) -> List[Table]:
    result = await session.execute(
        select(Table).join(TableMember).where(TableMember.user_id == user_id)
    )
    return result.scalars().all()


async def update_table(
    session: AsyncSession, table_id: int, table_in: TableUpdate
) -> Table:
    table = await session.get(Table, table_id)
    if not table:
        raise ValueError("Table not found")

    update_data = table_in.model_dump(exclude_unset=True)
    member_ids = update_data.pop("member_ids", None)
    for field, value in update_data.items():
        setattr(table, field, value)

    session.add(table)
    added_ids: Set[int] = set()
    if member_ids is not None:
        existing_members = (
            (
                await session.execute(
                    select(TableMember).where(TableMember.table_id == table_id)
                )
            )
            .scalars()
            .all()
        )
        existing_ids = {m.user_id for m in existing_members}
        new_ids = set(member_ids)

        added_ids = new_ids - existing_ids
        for uid in added_ids:
            session.add(TableMember(table_id=table_id, user_id=uid, is_gm=False))

        for member in existing_members:
            if member.user_id not in new_ids:
                await session.delete(member)

    await session.commit()
    await session.refresh(table)

    if added_ids:
        news = NewsCreate(
            title="Added to Table",
            type="gaming_session",
            description=f"You were added to table '{table.name}'. View: /tables/{table.id}",
            user_ids=list(added_ids),
        )
        await create_news(session, news)
    return table


async def delete_table(session: AsyncSession, table_id: int) -> None:
    """Remove a table and all of its sessions."""
    session_ids = (
        (await session.execute(select(Session.id).where(Session.table_id == table_id)))
        .scalars()
        .all()
    )

    if session_ids:
        poll_ids = (
            (
                await session.execute(
                    select(SessionPoll.id).where(
                        SessionPoll.session_id.in_(session_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

        if poll_ids:
            await session.execute(
                delete(SessionPollVote).where(SessionPollVote.poll_id.in_(poll_ids))
            )
            await session.execute(
                delete(SessionPollOption).where(SessionPollOption.poll_id.in_(poll_ids))
            )
            await session.execute(
                delete(SessionPoll).where(SessionPoll.id.in_(poll_ids))
            )

        await session.execute(
            delete(SessionPage).where(SessionPage.session_id.in_(session_ids))
        )
        await session.execute(
            delete(SessionAttendance).where(
                SessionAttendance.session_id.in_(session_ids)
            )
        )
        await session.execute(delete(Session).where(Session.id.in_(session_ids)))

    await session.execute(delete(TableMember).where(TableMember.table_id == table_id))
    await session.execute(delete(Table).where(Table.id == table_id))
    await session.commit()
