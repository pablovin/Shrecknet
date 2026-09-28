from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.foundry_integration import (
    FoundryIntegrationValidation,
    FoundryWorldRead,
)
from app.services.foundry_integration_service import FoundryIntegrationService

router = APIRouter(prefix="/integrations/foundry", tags=["foundry-integrations"])


async def get_foundry_integration_service(
    session: AsyncSession = Depends(get_db_session),
) -> FoundryIntegrationService:
    return FoundryIntegrationService(session)


async def require_foundry_integration(
    key: str | None = Header(default=None, alias="X-Shrecknet-Integration-Key"),
    service: FoundryIntegrationService = Depends(get_foundry_integration_service),
):
    integration = await service.validate_key(key or "")
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked Foundry integration key",
        )
    return integration


@router.post("/validate", response_model=FoundryIntegrationValidation)
async def validate_integration(
    _=Depends(require_foundry_integration),
) -> FoundryIntegrationValidation:
    return FoundryIntegrationValidation(version="0.5.7")


@router.get("/worlds", response_model=list[FoundryWorldRead])
async def list_worlds_for_integration(
    integration=Depends(require_foundry_integration),
    service: FoundryIntegrationService = Depends(get_foundry_integration_service),
) -> list[FoundryWorldRead]:
    worlds = await service.list_allowed_worlds(integration)
    return [FoundryWorldRead(id=world.id, name=world.name) for world in worlds]
