from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.database import get_session
from app.models.model_user import User, UserRole
from app.models.model_table import TableMember
from app.models.model_gameworld import GameWorld
from app.models.model_session import Session
from app.dependencies import get_current_user, require_role
from app.schemas.schema_table import (
    TableCreate,
    TableListRead,
    TableMemberInfo,
    TableRead,
    TableUpdate,
)
from app.crud.crud_table import (
    create_table,
    get_tables_for_user,
    update_table,
    delete_table,
)

router = APIRouter(
    prefix="/tables", tags=["tables"], dependencies=[Depends(get_current_user)]
)


@router.post("/", response_model=TableRead)
async def create_table_endpoint(
    table: TableCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.world_builder)),
):
    return await create_table(session, table, user.id)


@router.get("/", response_model=List[TableListRead])
async def list_tables_endpoint(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    tables = await get_tables_for_user(session, user.id)
    now = datetime.now(timezone.utc)
    results: List[TableListRead] = []
    for t in tables:
        # World information
        world = await session.get(GameWorld, t.world_id)
        world_name = world.name if world else ""

        # Table members
        member_rows = await session.execute(
            select(User.id, User.nickname, User.image_url)
            .join(TableMember, TableMember.user_id == User.id)
            .where(TableMember.table_id == t.id)
        )
        members = [
            TableMemberInfo(id=mid, nickname=nick, image_url=img)
            for mid, nick, img in member_rows.all()
        ]

        # Sessions
        session_rows = await session.execute(
            select(Session.scheduled_time).where(Session.table_id == t.id)
        )
        session_times = session_rows.scalars().all()
        latest_time = None
        next_up_time = None
        for s_time in session_times:
            # Ensure we compare timezone-aware datetimes. Some stored
            # session times may be naive (no timezone info), so default
            # them to UTC for comparison.
            s_time_aware = (
                s_time.replace(tzinfo=timezone.utc) if s_time.tzinfo is None else s_time
            )
            if s_time_aware <= now:
                if not latest_time or s_time_aware > latest_time:
                    latest_time = s_time_aware
            elif not next_up_time or s_time_aware < next_up_time:
                next_up_time = s_time_aware

        results.append(
            TableListRead(
                id=t.id,
                world_id=t.world_id,
                name=t.name,
                crest_url=t.crest_url,
                created_by=t.created_by,
                created_at=t.created_at,
                world_name=world_name,
                members=members,
                latest_session=latest_time,
                next_session=next_up_time,
            )
        )
    return results


@router.patch("/{table_id}", response_model=TableRead)
async def update_table_endpoint(
    table_id: int,
    table: TableUpdate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.world_builder)),
):
    return await update_table(session, table_id, table)


@router.delete("/{table_id}")
async def delete_table_endpoint(
    table_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.world_builder)),
):
    await delete_table(session, table_id)
    return {"ok": True}
