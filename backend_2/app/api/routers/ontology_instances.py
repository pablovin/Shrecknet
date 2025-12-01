from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import (
    get_current_user,
    get_ontology_instance_service,
    require_roles,
)
from app.models.user import User, UserRole
from app.schemas.ontology_instance import (
    OntologyInstanceCreate,
    OntologyInstanceRead,
    OntologyInstanceSearchResponse,
    OntologyInstanceUpdate,
    TimelineEventCreate,
    TimelineEventRead,
    TimelineEventUpdate,
)
from app.services.ontology_instance_service import OntologyInstanceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ontology-instances", tags=["ontology-instances"])


@router.get("/search", response_model=OntologyInstanceSearchResponse)
async def search_ontology_instances(
    query: str = Query(..., min_length=1, description="Search text to match"),
    ontology_id: int = Query(
        ..., ge=1, description="Ontology identifier to limit the search scope"
    ),
    limit: int = Query(
        20, ge=1, le=20, description="Maximum results to return per category",
    ),
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> OntologyInstanceSearchResponse:
    try:
        response = await service.search_instances(
            query=query, ontology_id=ontology_id, per_section_limit=limit
        )
        try:
            logger.info(
                "ontology_search response=%s",
                json.dumps(response.model_dump(), default=str),
            )
        except Exception:
            logger.exception("Failed to serialize ontology search response")
        return response
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.post(
    "/", response_model=OntologyInstanceRead, status_code=status.HTTP_201_CREATED
)
async def create_ontology_instance(
    payload: OntologyInstanceCreate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyInstanceRead:
    try:
        return await service.create_instance(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/", response_model=list[OntologyInstanceRead])
async def list_ontology_instances(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=100),
    search: str | None = None,
    ontology_id: int | None = None,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> list[OntologyInstanceRead]:
    instances = await service.list_instances(
        skip=skip, limit=limit, search=search, ontology_id=ontology_id
    )
    return list(instances)


@router.get("/by-alias/{slug_alias}", response_model=OntologyInstanceRead)
async def get_ontology_instance_by_slug_alias(
    slug_alias: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> OntologyInstanceRead:
    try:
        return await service.get_instance_by_slug_alias(slug_alias)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/{instance_id}", response_model=OntologyInstanceRead)
async def get_ontology_instance(
    instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> OntologyInstanceRead:
    try:
        return await service.get_instance(instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.put("/{instance_id}", response_model=OntologyInstanceRead)
async def update_ontology_instance(
    instance_id: str,
    payload: OntologyInstanceUpdate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyInstanceRead:
    try:
        return await service.update_instance(instance_id, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.delete(
    "/{instance_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response,
)
async def delete_ontology_instance(
    instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    await service.delete_instance(instance_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _resolve_status(
    exc: ValueError, *, default: int = status.HTTP_400_BAD_REQUEST
) -> int:
    message = str(exc).lower()
    if "not found" in message:
        return status.HTTP_404_NOT_FOUND
    return default


@router.get("/{instance_id}/timeline-events", response_model=list[TimelineEventRead])
async def list_timeline_events(
    instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> list[TimelineEventRead]:
    try:
        return await service.list_timeline_events(instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.post(
    "/{instance_id}/timeline-events",
    response_model=TimelineEventRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_timeline_event(
    instance_id: str,
    payload: TimelineEventCreate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> TimelineEventRead:
    try:
        return await service.create_timeline_event(instance_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=_resolve_status(exc), detail=str(exc),) from exc


@router.get(
    "/{instance_id}/timeline-events/{timeline_event_id}",
    response_model=TimelineEventRead,
)
async def get_timeline_event(
    instance_id: str,
    timeline_event_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> TimelineEventRead:
    try:
        return await service.get_timeline_event(instance_id, timeline_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.put(
    "/{instance_id}/timeline-events/{timeline_event_id}",
    response_model=TimelineEventRead,
)
async def update_timeline_event(
    instance_id: str,
    timeline_event_id: str,
    payload: TimelineEventUpdate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> TimelineEventRead:
    try:
        return await service.update_timeline_event(
            instance_id, timeline_event_id, payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=_resolve_status(exc), detail=str(exc),) from exc


@router.delete(
    "/{instance_id}/timeline-events/{timeline_event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_timeline_event(
    instance_id: str,
    timeline_event_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    try:
        await service.delete_timeline_event(instance_id, timeline_event_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
