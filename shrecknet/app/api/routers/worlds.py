from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models import User
from app.services.worlds import world_service

router = APIRouter(prefix="/worlds", tags=["worlds"])


class WorldRead(BaseModel):
    id: str
    name: str
    ontology_ids: list[int]


@router.get("", response_model=list[WorldRead])
async def list_worlds(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[WorldRead]:
    rows = await world_service.list_worlds_async(session)
    return [WorldRead(id=world.id, name=world.name, ontology_ids=ontology_ids) for world, ontology_ids in rows]


@router.get("/{world_id}", response_model=WorldRead)
async def get_world(
    world_id: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> WorldRead:
    row = await world_service.get_world_async(session, world_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="World not found")
    world, ontology_ids = row
    return WorldRead(id=world.id, name=world.name, ontology_ids=ontology_ids)
