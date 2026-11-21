from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db_session
from app.models import User, World
from app.models.user import UserRole
from app.schemas.world import WorldMetadata, WorldRead, WorldUpdate
from app.services.worlds import world_service

router = APIRouter(prefix="/worlds", tags=["worlds"])
manage_world = require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)


def world_read(world: World, ontology_ids: list[int]) -> WorldRead:
    return WorldRead(
        id=world.id,
        name=world.name,
        description=world.description,
        image_url=world.image_url,
        rpg_system=world.rpg_system,
        rpg_edition=world.rpg_edition,
        rpg_subversion=world.rpg_subversion,
        ontology_ids=ontology_ids,
        active_ontology_id=ontology_ids[0] if len(ontology_ids) == 1 else None,
    )


@router.get("", response_model=list[WorldRead])
async def list_worlds(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[WorldRead]:
    return [world_read(*row) for row in await world_service.list_worlds_async(session)]


@router.post("", response_model=WorldRead, status_code=status.HTTP_201_CREATED)
async def create_world(
    payload: WorldMetadata,
    _: User = Depends(manage_world),
    session: AsyncSession = Depends(get_db_session),
) -> WorldRead:
    return world_read(*await world_service.create_world(session, payload.model_dump()))


@router.get("/{world_id}", response_model=WorldRead)
async def get_world(
    world_id: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> WorldRead:
    row = await world_service.get_world_async(session, world_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="World not found")
    return world_read(*row)


@router.patch("/{world_id}", response_model=WorldRead)
async def update_world(
    world_id: str,
    payload: WorldUpdate,
    _: User = Depends(manage_world),
    session: AsyncSession = Depends(get_db_session),
) -> WorldRead:
    row = await world_service.update_world(session, world_id, payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="World not found")
    return world_read(*row)
