from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.database import get_session
from app.models.model_user import User
from app.dependencies import get_current_user
from app.schemas.schema_session import SessionCreate, SessionRead
from app.crud.crud_session import create_session, get_sessions_for_table

router = APIRouter(prefix="/tables", tags=["sessions"], dependencies=[Depends(get_current_user)])

@router.post("/{table_id}/sessions", response_model=SessionRead)
async def create_session_endpoint(
    table_id: int,
    session_in: SessionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    session_in.table_id = table_id
    sess = await create_session(session, session_in, user.id)
    return SessionRead(
        id=sess.id,
        table_id=sess.table_id,
        scheduled_time=sess.scheduled_time,
        summary=sess.summary,
        location=sess.location,
        created_by=sess.created_by,
        created_at=sess.created_at,
    )

@router.get("/{table_id}/sessions", response_model=List[SessionRead])
async def list_sessions_endpoint(
    table_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    sessions = await get_sessions_for_table(session, table_id)
    return [
        SessionRead(
            id=s.id,
            table_id=s.table_id,
            scheduled_time=s.scheduled_time,
            summary=s.summary,
            location=s.location,
            created_by=s.created_by,
            created_at=s.created_at,
        )
        for s in sessions
    ]
