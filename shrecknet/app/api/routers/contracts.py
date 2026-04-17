from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models import User
from app.services.users import user_service
from app.services.worlds import world_service

router = APIRouter(prefix="/contracts", tags=["contracts"])


class ContractUser(BaseModel):
    id: str
    role: str
    full_name: str
    email: str


class ContractWorld(BaseModel):
    id: str
    name: str
    ontology_ids: list[int]


@router.get("/users/me", response_model=ContractUser)
def contract_me(current_user: User = Depends(get_current_user)) -> ContractUser:
    return ContractUser(
        id=str(current_user.id),
        role=current_user.role,
        full_name=current_user.full_name,
        email=current_user.email,
    )


@router.get("/users/{user_id}", response_model=ContractUser)
async def contract_user(
    user_id: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ContractUser:
    user = await user_service.get_async(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return ContractUser(
        id=str(user.id),
        role=user.role,
        full_name=user.full_name,
        email=user.email,
    )


@router.get("/worlds/{world_id}", response_model=ContractWorld)
async def contract_world(
    world_id: str,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ContractWorld:
    row = await world_service.get_world_async(session, world_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="World not found")
    world, ontology_ids = row
    return ContractWorld(id=world.id, name=world.name, ontology_ids=ontology_ids)
