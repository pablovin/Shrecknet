from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_admin_user,
    get_current_user,
    get_favorite_ontology_instance_service,
    get_ontology_instance_service,
    get_user_service,
    require_roles,
)
from app.db.jobs_session import get_jobs_session
from app.models.background_job import BackgroundJob, JobStatus, JobType
from app.models.user import User, UserRole
from app.schemas.favorite_ontology_instance import (
    FavoriteOntologyInstanceCreate,
    FavoriteOntologyInstanceRead,
    FavoriteStatusRead,
)
from app.schemas.ontology_instance import (
    MilestoneCreate,
    MilestoneRead,
    MilestoneUpdate,
    OntologyInstanceEntityTypeClearJobResponse,
    OntologyInstanceEntityTypeClearRequest,
    OntologyInstanceTimelineClearJobResponse,
    OntologyInstanceTimelineClearRequest,
    OntologyInstanceCountResponse,
    OntologyInstanceCreate,
    OntologyInstanceRead,
    OntologyInstanceSearchResponse,
    OntologyInstanceSummaryPage,
    OntologyInstanceUpdate,
    SceneCreate,
    SceneRead,
    SceneUpdate,
)
from app.schemas.user import UserRead
from app.services.favorite_ontology_instance_service import (
    FavoriteOntologyInstanceService,
)
from app.services.ontology_instance_service import OntologyInstanceService
from app.services.user_service import UserService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ontology-instances", tags=["ontology-instances"])


def _parse_json_details(details: str | None) -> dict[str, Any] | None:
    if not details:
        return None
    try:
        parsed = json.loads(details)
        return parsed if isinstance(parsed, dict) else {"raw": details}
    except json.JSONDecodeError:
        return {"raw": details}


def _to_frontend_job(job: BackgroundJob) -> dict[str, Any]:
    return {
        "kind": job.job_type,
        "job_id": str(job.id),
        "status": job.status,
        "progress": job.progress,
        "description": job.description,
        "author_type": job.author_type,
        "author_id": job.author_id,
        "ontology_id": job.ontology_id,
        "celery_task_id": job.celery_task_id,
        "details": _parse_json_details(job.details),
        "error_message": job.error_message,
        "start_time": job.started_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "duration_seconds": job.duration_seconds,
        "updated_at": job.updated_at.isoformat(),
    }


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


@router.get("/count", response_model=OntologyInstanceCountResponse)
async def count_ontology_instances(
    ontology_id: int | None = Query(None, ge=1),
    entity_definition_id: int | None = Query(
        None, ge=1, description="Filter instances that include this entity definition",
    ),
    search: str | None = Query(
        None,
        description="Optional text to match against instance names or entity aliases",
    ),
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> OntologyInstanceCountResponse:
    total = await service.count_instances(
        ontology_id=ontology_id,
        entity_definition_id=entity_definition_id,
        search=search,
    )
    return OntologyInstanceCountResponse(total=total)


@router.get("/basic", response_model=OntologyInstanceSummaryPage)
async def list_ontology_instance_summaries(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=200),
    ontology_id: int | None = Query(None, ge=1),
    entity_definition_id: int | None = Query(
        None, ge=1, description="Filter instances that include this entity definition",
    ),
    search: str | None = Query(
        None,
        description="Optional text to match against instance names or entity aliases",
    ),
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> OntologyInstanceSummaryPage:
    return await service.list_instance_summaries(
        skip=skip,
        limit=limit,
        ontology_id=ontology_id,
        entity_definition_id=entity_definition_id,
        search=search,
    )


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


@router.get("/favorites", response_model=list[FavoriteOntologyInstanceRead])
async def list_favorites(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=100),
    favorite_service: FavoriteOntologyInstanceService = Depends(
        get_favorite_ontology_instance_service
    ),
    current_user: User = Depends(get_current_user),
) -> list[FavoriteOntologyInstanceRead]:
    favorites = await favorite_service.list_favorites(
        current_user.id, skip=skip, limit=limit
    )
    return [FavoriteOntologyInstanceRead(**fav) for fav in favorites]


@router.get("/{instance_id}/favorites/users", response_model=list[UserRead])
async def list_users_who_favorited(
    instance_id: str,
    favorite_service: FavoriteOntologyInstanceService = Depends(
        get_favorite_ontology_instance_service
    ),
    user_service: UserService = Depends(get_user_service),
    _: User = Depends(get_current_user),
) -> list[UserRead]:
    user_ids = await favorite_service.get_users_who_favorited(instance_id)
    if not user_ids:
        return []
    users = await user_service.list_users_by_ids(user_ids)
    users_by_id = {user.id: user for user in users}
    ordered_users = [
        users_by_id[user_id] for user_id in user_ids if user_id in users_by_id
    ]
    return [UserRead.model_validate(user) for user in ordered_users]


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


@router.get("/{instance_id}/full", response_model=OntologyInstanceRead)
async def get_ontology_instance_full(
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


@router.post(
    "/admin/clear-entity-types-content/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OntologyInstanceEntityTypeClearJobResponse,
)
async def trigger_clear_ontology_instance_content_by_entity_types(
    payload: OntologyInstanceEntityTypeClearRequest,
    _: User = Depends(get_current_admin_user),
) -> OntologyInstanceEntityTypeClearJobResponse:
    from app.tasks.ontology_instance_clear import clear_instance_content_by_entity_types
    from app.utils.async_helpers import run_async
    from app.utils.job_tracking import create_background_job
    from app.models.background_job import AuthorType

    definition_ids = [int(value) for value in payload.entity_definition_ids or []]
    type_names = [
        value.strip()
        for value in (payload.entity_type_names or [])
        if value and value.strip()
    ]
    if not definition_ids and not type_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one entity_definition_id or entity_type_name",
        )

    job_id = run_async(
        create_background_job(
            author_type=AuthorType.USER,
            author_id="admin",
            job_type=JobType.ONTOLOGY_INSTANCE_ENTITY_TYPE_CLEAR,
            description=(
                f"Clearing ontology {payload.ontology_id} content for selected entity types"
            ),
            details={
                "ontology_id": payload.ontology_id,
                "entity_definition_ids": definition_ids,
                "entity_type_names": type_names,
                "status": "queued",
            },
            ontology_id=payload.ontology_id,
        )
    )

    clear_instance_content_by_entity_types.delay(
        ontology_id=payload.ontology_id,
        entity_definition_ids=definition_ids,
        entity_type_names=type_names,
        author_type="user",
        author_id="admin",
        job_id=job_id,
    )

    return OntologyInstanceEntityTypeClearJobResponse(
        message="Background clear job queued",
        kind=JobType.ONTOLOGY_INSTANCE_ENTITY_TYPE_CLEAR.value,
        job_id=job_id,
        status=JobStatus.QUEUED.value,
        monitor_url=f"/ontology-instances/admin/clear-entity-types-content/jobs/{job_id}",
    )


@router.get(
    "/admin/clear-entity-types-content/jobs/{job_id}",
    status_code=status.HTTP_200_OK,
)
async def get_entity_type_clear_job_status(
    job_id: int,
    jobs_session: AsyncSession = Depends(get_jobs_session),
    _: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    result = await jobs_session.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.job_type == JobType.ONTOLOGY_INSTANCE_ENTITY_TYPE_CLEAR,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clear job {job_id} not found",
        )
    return _to_frontend_job(job)


@router.get(
    "/admin/clear-entity-types-content/jobs",
    status_code=status.HTTP_200_OK,
)
async def list_entity_type_clear_jobs(
    jobs_session: AsyncSession = Depends(get_jobs_session),
    _: User = Depends(get_current_admin_user),
    ontology_id: int | None = Query(None, ge=1),
    status_filter: JobStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    query = select(BackgroundJob).where(
        BackgroundJob.job_type == JobType.ONTOLOGY_INSTANCE_ENTITY_TYPE_CLEAR
    )
    if ontology_id is not None:
        query = query.where(BackgroundJob.ontology_id == ontology_id)
    if status_filter is not None:
        query = query.where(BackgroundJob.status == status_filter)
    query = query.order_by(BackgroundJob.started_at.desc()).limit(limit).offset(offset)
    result = await jobs_session.execute(query)
    jobs = result.scalars().all()
    return [_to_frontend_job(job) for job in jobs]


@router.post(
    "/admin/clear-timeline-events/trigger",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=OntologyInstanceTimelineClearJobResponse,
)
async def trigger_clear_timeline_events(
    payload: OntologyInstanceTimelineClearRequest,
    _: User = Depends(get_current_admin_user),
) -> OntologyInstanceTimelineClearJobResponse:
    from app.models.background_job import AuthorType
    from app.tasks.ontology_instance_clear import clear_timeline_events_and_orphans
    from app.utils.async_helpers import run_async
    from app.utils.job_tracking import create_background_job

    job_id = run_async(
        create_background_job(
            author_type=AuthorType.USER,
            author_id="admin",
            job_type=JobType.ONTOLOGY_INSTANCE_TIMELINE_CLEAR,
            description=(
                f"Clearing timeline events/orphans for ontology {payload.ontology_id}"
            ),
            details={
                "ontology_id": payload.ontology_id,
                "status": "queued",
            },
            ontology_id=payload.ontology_id,
        )
    )

    clear_timeline_events_and_orphans.delay(
        ontology_id=payload.ontology_id,
        author_type="user",
        author_id="admin",
        job_id=job_id,
    )

    return OntologyInstanceTimelineClearJobResponse(
        message="Background timeline clear job queued",
        kind=JobType.ONTOLOGY_INSTANCE_TIMELINE_CLEAR.value,
        job_id=job_id,
        status=JobStatus.QUEUED.value,
        monitor_url=f"/ontology-instances/admin/clear-timeline-events/jobs/{job_id}",
    )


@router.get(
    "/admin/clear-timeline-events/jobs/{job_id}",
    status_code=status.HTTP_200_OK,
)
async def get_timeline_clear_job_status(
    job_id: int,
    jobs_session: AsyncSession = Depends(get_jobs_session),
    _: User = Depends(get_current_admin_user),
) -> dict[str, Any]:
    result = await jobs_session.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job_id,
            BackgroundJob.job_type == JobType.ONTOLOGY_INSTANCE_TIMELINE_CLEAR,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Timeline clear job {job_id} not found",
        )
    return _to_frontend_job(job)


@router.get(
    "/admin/clear-timeline-events/jobs",
    status_code=status.HTTP_200_OK,
)
async def list_timeline_clear_jobs(
    jobs_session: AsyncSession = Depends(get_jobs_session),
    _: User = Depends(get_current_admin_user),
    ontology_id: int | None = Query(None, ge=1),
    status_filter: JobStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    query = select(BackgroundJob).where(
        BackgroundJob.job_type == JobType.ONTOLOGY_INSTANCE_TIMELINE_CLEAR
    )
    if ontology_id is not None:
        query = query.where(BackgroundJob.ontology_id == ontology_id)
    if status_filter is not None:
        query = query.where(BackgroundJob.status == status_filter)
    query = query.order_by(BackgroundJob.started_at.desc()).limit(limit)

    result = await jobs_session.execute(query)
    jobs = result.scalars().all()
    return [_to_frontend_job(job) for job in jobs]


def _resolve_status(
    exc: ValueError, *, default: int = status.HTTP_400_BAD_REQUEST
) -> int:
    message = str(exc).lower()
    if "not found" in message:
        return status.HTTP_404_NOT_FOUND
    return default


@router.get("/{instance_id}/scenes", response_model=list[SceneRead])
async def list_scenes(
    instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> list[SceneRead]:
    try:
        return await service.list_scenes(instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.get(
    "/{instance_id}/scenes/derived-from/{entity_instance_id}",
    response_model=list[SceneRead],
)
async def list_scenes_by_derived_from(
    instance_id: str,
    entity_instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> list[SceneRead]:
    try:
        return await service.list_scenes_by_derived_from(instance_id, entity_instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.get(
    "/{instance_id}/scenes/related-to/{entity_instance_id}",
    response_model=list[SceneRead],
)
async def list_scenes_by_related_to(
    instance_id: str,
    entity_instance_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> list[SceneRead]:
    try:
        return await service.list_scenes_by_related_to(instance_id, entity_instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.post(
    "/{instance_id}/scenes",
    response_model=SceneRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_scene(
    instance_id: str,
    payload: SceneCreate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> SceneRead:
    try:
        return await service.create_scene(instance_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=_resolve_status(exc), detail=str(exc)) from exc


@router.get("/{instance_id}/scenes/{scene_id}", response_model=SceneRead)
async def get_scene(
    instance_id: str,
    scene_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> SceneRead:
    try:
        return await service.get_scene(instance_id, scene_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.put("/{instance_id}/scenes/{scene_id}", response_model=SceneRead)
async def update_scene(
    instance_id: str,
    scene_id: str,
    payload: SceneUpdate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> SceneRead:
    try:
        return await service.update_scene(instance_id, scene_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=_resolve_status(exc), detail=str(exc)) from exc


@router.delete(
    "/{instance_id}/scenes/{scene_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_scene(
    instance_id: str,
    scene_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    try:
        await service.delete_scene(instance_id, scene_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{instance_id}/scenes/{scene_id}/milestones", response_model=list[MilestoneRead]
)
async def list_milestones(
    instance_id: str,
    scene_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> list[MilestoneRead]:
    try:
        return await service.list_milestones(instance_id, scene_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.post(
    "/{instance_id}/scenes/{scene_id}/milestones",
    response_model=MilestoneRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_milestone(
    instance_id: str,
    scene_id: str,
    payload: MilestoneCreate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> MilestoneRead:
    try:
        return await service.create_milestone(instance_id, scene_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=_resolve_status(exc), detail=str(exc)) from exc


@router.get(
    "/{instance_id}/scenes/{scene_id}/milestones/{milestone_id}",
    response_model=MilestoneRead,
)
async def get_milestone(
    instance_id: str,
    scene_id: str,
    milestone_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> MilestoneRead:
    try:
        return await service.get_milestone(instance_id, scene_id, milestone_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc


@router.put(
    "/{instance_id}/scenes/{scene_id}/milestones/{milestone_id}",
    response_model=MilestoneRead,
)
async def update_milestone(
    instance_id: str,
    scene_id: str,
    milestone_id: str,
    payload: MilestoneUpdate,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> MilestoneRead:
    try:
        return await service.update_milestone(
            instance_id, scene_id, milestone_id, payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=_resolve_status(exc), detail=str(exc)) from exc


@router.delete(
    "/{instance_id}/scenes/{scene_id}/milestones/{milestone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_milestone(
    instance_id: str,
    scene_id: str,
    milestone_id: str,
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    try:
        await service.delete_milestone(instance_id, scene_id, milestone_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=_resolve_status(exc, default=status.HTTP_404_NOT_FOUND),
            detail=str(exc),
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{instance_id}/favorite",
    response_model=FavoriteOntologyInstanceRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_favorite(
    instance_id: str,
    payload: FavoriteOntologyInstanceCreate,
    favorite_service: FavoriteOntologyInstanceService = Depends(
        get_favorite_ontology_instance_service
    ),
    instance_service: OntologyInstanceService = Depends(get_ontology_instance_service),
    current_user: User = Depends(get_current_user),
) -> FavoriteOntologyInstanceRead:
    try:
        await instance_service.get_instance(instance_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    favorite = await favorite_service.add_favorite(
        current_user.id, instance_id, payload.ontology_id
    )
    return FavoriteOntologyInstanceRead(**favorite)


@router.delete(
    "/{instance_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def remove_favorite(
    instance_id: str,
    favorite_service: FavoriteOntologyInstanceService = Depends(
        get_favorite_ontology_instance_service
    ),
    current_user: User = Depends(get_current_user),
) -> Response:
    removed = await favorite_service.remove_favorite(current_user.id, instance_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Favorite not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{instance_id}/is-favorite", response_model=FavoriteStatusRead)
async def check_favorite_status(
    instance_id: str,
    favorite_service: FavoriteOntologyInstanceService = Depends(
        get_favorite_ontology_instance_service
    ),
    current_user: User = Depends(get_current_user),
) -> FavoriteStatusRead:
    is_favorite = await favorite_service.is_favorite(current_user.id, instance_id)
    return FavoriteStatusRead(is_favorite=is_favorite)
