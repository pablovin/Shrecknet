from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import (
    get_ontology_instance_service,
    require_roles,
)
from app.models.user import UserRole
from app.schemas.ontology_instance import (
    OntologyInstanceCreate,
    OntologyInstanceRead,
    OntologyInstanceUpdate,
)
from app.services.ontology_instance_service import OntologyInstanceService

router = APIRouter(
    prefix="/ontology-instances",
    tags=["ontology-instances"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)


@router.post("/", response_model=OntologyInstanceRead, status_code=status.HTTP_201_CREATED)
async def create_ontology_instance(
    payload: OntologyInstanceCreate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
) -> OntologyInstanceRead:
    try:
        return await service.create_instance(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/", response_model=list[OntologyInstanceRead])
async def list_ontology_instances(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=100),
    search: str | None = None,
    ontology_id: int | None = None,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
) -> list[OntologyInstanceRead]:
    instances = await service.list_instances(
        skip=skip, limit=limit, search=search, ontology_id=ontology_id
    )
    return list(instances)


@router.get("/{instance_id}", response_model=OntologyInstanceRead)
async def get_ontology_instance(
    instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
) -> OntologyInstanceRead:
    try:
        return await service.get_instance(instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{instance_id}", response_model=OntologyInstanceRead)
async def update_ontology_instance(
    instance_id: str,
    payload: OntologyInstanceUpdate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
) -> OntologyInstanceRead:
    try:
        return await service.update_instance(instance_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete(
    "/{instance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_ontology_instance(
    instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
) -> Response:
    await service.delete_instance(instance_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
