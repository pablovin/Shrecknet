from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.database import get_session
from app.models.model_user import User, UserRole
from app.dependencies import get_current_user, require_role
from app.schemas.schema_table import TableCreate, TableRead
from app.crud.crud_table import create_table, get_tables_for_user

router = APIRouter(prefix="/tables", tags=["tables"], dependencies=[Depends(get_current_user)])

@router.post("/", response_model=TableRead)
async def create_table_endpoint(
    table: TableCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role(UserRole.world_builder)),
):
    return await create_table(session, table, user.id)

@router.get("/", response_model=List[TableRead])
async def list_tables_endpoint(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    tables = await get_tables_for_user(session, user.id)
    return [
        TableRead(
            id=t.id,
            world_id=t.world_id,
            name=t.name,
            crest_url=t.crest_url,
            created_by=t.created_by,
            created_at=t.created_at,
        )
        for t in tables
    ]
