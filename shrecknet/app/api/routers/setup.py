from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_media_service, get_ontology_service, require_roles
from app.models.user import User, UserRole
from app.schemas.setup import DefaultWorldsRequest, DefaultWorldsResponse
from app.services.setup_service import SetupService
from app.services.media_service import MediaService
from app.services.ontology_service import OntologyService

router = APIRouter(prefix="/setup", tags=["setup"])


@router.post(
    "/default-worlds",
    response_model=DefaultWorldsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_default_worlds(
    payload: DefaultWorldsRequest,
    _current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
    ontology_service: OntologyService = Depends(get_ontology_service),
    media_service: MediaService = Depends(get_media_service),
) -> DefaultWorldsResponse:
    if not payload.worlds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="worlds list cannot be empty",
        )

    service = SetupService(
        ontology_service=ontology_service,
        media_service=media_service,
    )
    return await service.create_default_worlds(payload.worlds)
