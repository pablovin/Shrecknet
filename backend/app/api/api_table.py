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
from app.crud.crud_table import create_table, get_tables_for_user, update_table

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
            select(Session).where(Session.table_id == t.id)
        )
        sessions = session_rows.scalars().all()
        latest = None
        next_up = None
        for s in sessions:
            if s.scheduled_time <= now:
                if not latest or s.scheduled_time > latest.scheduled_time:
                    latest = s
            elif not next_up or s.scheduled_time < next_up.scheduled_time:
                next_up = s

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
                latest_session=latest.scheduled_time if latest else None,
                next_session=next_up.scheduled_time if next_up else None,
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
