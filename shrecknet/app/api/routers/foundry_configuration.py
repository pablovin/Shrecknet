"""Configuration-manager endpoints for Foundry integration credentials."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.foundry_integration import (
    FoundryIntegrationCreate,
    FoundryIntegrationCreated,
    FoundryIntegrationRead,
)
from app.schemas.foundry_integration_config import FoundryIntegrationConfigUpdate
from app.services.foundry_integration_management_service import FoundryIntegrationManagementService
from app.services.foundry_integration_service import FoundryIntegrationService

router = APIRouter(prefix="/config/integrations/foundry", tags=["config", "foundry-integrations"])
AdminUser = Annotated[User, Depends(get_current_admin_user)]


async def get_management_service(
    session: AsyncSession = Depends(get_db_session),
) -> FoundryIntegrationManagementService:
    return FoundryIntegrationManagementService(session)


@router.get("", response_model=list[FoundryIntegrationRead])
async def list_integrations(
    _: AdminUser,
    session: AsyncSession = Depends(get_db_session),
) -> list[FoundryIntegrationRead]:
    service = FoundryIntegrationService(session)
    return [FoundryIntegrationRead.model_validate(item) for item in await service.repository.list()]


@router.post("", response_model=FoundryIntegrationCreated, status_code=status.HTTP_201_CREATED)
async def create_integration(
    payload: FoundryIntegrationCreate,
    current_user: AdminUser,
    session: AsyncSession = Depends(get_db_session),
) -> FoundryIntegrationCreated:
    integration, key = await FoundryIntegrationService(session).create(
        name=payload.name,
        allowed_world_ids=payload.allowed_world_ids,
        created_by_user_id=current_user.id,
    )
    return FoundryIntegrationCreated(
        **FoundryIntegrationRead.model_validate(integration).model_dump(), key=key
    )


@router.put("/{integration_id}", response_model=FoundryIntegrationRead)
async def update_integration(
    integration_id: str,
    payload: FoundryIntegrationConfigUpdate,
    _: AdminUser,
    service: FoundryIntegrationManagementService = Depends(get_management_service),
) -> FoundryIntegrationRead:
    integration = await service.get(integration_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foundry integration not found")
    updated = await service.update(
        integration,
        name=payload.name,
        allowed_world_ids=payload.allowed_world_ids,
    )
    return FoundryIntegrationRead.model_validate(updated)


@router.post("/{integration_id}/rotate", response_model=FoundryIntegrationCreated)
async def rotate_integration(
    integration_id: str,
    _: AdminUser,
    service: FoundryIntegrationManagementService = Depends(get_management_service),
) -> FoundryIntegrationCreated:
    integration = await service.get(integration_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foundry integration not found")
    updated, key = await service.rotate(integration)
    return FoundryIntegrationCreated(
        **FoundryIntegrationRead.model_validate(updated).model_dump(), key=key
    )


@router.post("/{integration_id}/revoke", response_model=FoundryIntegrationRead)
async def revoke_integration(
    integration_id: str,
    _: AdminUser,
    service: FoundryIntegrationManagementService = Depends(get_management_service),
) -> FoundryIntegrationRead:
    integration = await service.get(integration_id)
    if integration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Foundry integration not found")
    await service.revoke(integration)
    return FoundryIntegrationRead.model_validate(integration)
