from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_current_user, get_page_visit_service, require_roles
from app.models.user import User, UserRole
from app.schemas.page_visits import (
    PageUserVisitSummaryRead,
    PageVisitCreate,
    PageVisitRead,
    PageVisitStatsRead,
    PageVisitUserRead,
)
from app.services.page_visit_service import PageVisitService

router = APIRouter(prefix="/page-visits", tags=["page-visits"])

MAX_RECENT_VISITS = 100


@router.post(
    "/",
    response_model=PageVisitRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_page_visit(
    payload: PageVisitCreate,
    service: PageVisitService = Depends(get_page_visit_service),
    current_user: User = Depends(get_current_user),
) -> PageVisitRead:
    visit = await service.record_visit(
        page_key=payload.page_key, user_id=current_user.id
    )
    return PageVisitRead.model_validate(visit)


@router.get(
    "/pages/search",
    response_model=list[PageVisitStatsRead],
    dependencies=[Depends(require_roles(UserRole.WORLD_BUILDER, UserRole.ADMIN))],
)
async def search_page_stats(
    page_key: str | None = Query(None, description="Search by page_key pattern"),
    page_alias: str | None = Query(None, description="Search by page_alias pattern"),
    ontology_instance_id: str | None = Query(
        None, description="Search by ontology instance ID"
    ),
    limit: int = Query(100, gt=0, le=MAX_RECENT_VISITS),
    service: PageVisitService = Depends(get_page_visit_service),
) -> list[PageVisitStatsRead]:
    """
    Search for page visit stats by page_key, page_alias, or ontology_instance_id.
    At least one search parameter must be provided.
    """
    if not any([page_key, page_alias, ontology_instance_id]):
        return []

    matching_page_keys = await service.search_page_keys(
        page_key=page_key,
        page_alias=page_alias,
        ontology_instance_id=ontology_instance_id,
    )

    results = []
    for pk in matching_page_keys:
        stats = await service.get_page_stats(pk)
        recent_visits = await service.list_recent_visits(page_key=pk, limit=limit)
        if stats is None:
            continue
        results.append(
            PageVisitStatsRead(
                page_key=stats.page_key,
                total_visits=stats.total_visits,
                unique_users=stats.unique_users,
                last_visited_at=stats.last_visited_at,
                recent_visits=[
                    PageVisitUserRead(
                        user_id=user_id, username=username, visited_at=visited_at
                    )
                    for user_id, username, visited_at in recent_visits
                ],
            )
        )
    return results


@router.get(
    "/pages/{page_key}/stats",
    response_model=PageVisitStatsRead,
    dependencies=[Depends(require_roles(UserRole.WORLD_BUILDER, UserRole.ADMIN))],
)
async def get_page_stats(
    page_key: str,
    limit: int = Query(100, gt=0, le=MAX_RECENT_VISITS),
    service: PageVisitService = Depends(get_page_visit_service),
) -> PageVisitStatsRead:
    stats = await service.get_page_stats(page_key)
    recent_visits = await service.list_recent_visits(page_key=page_key, limit=limit)
    if stats is None:
        return PageVisitStatsRead(
            page_key=page_key,
            total_visits=0,
            unique_users=0,
            last_visited_at=None,
            recent_visits=[
                PageVisitUserRead(
                    user_id=user_id, username=username, visited_at=visited_at
                )
                for user_id, username, visited_at in recent_visits
            ],
        )
    return PageVisitStatsRead(
        page_key=stats.page_key,
        total_visits=stats.total_visits,
        unique_users=stats.unique_users,
        last_visited_at=stats.last_visited_at,
        recent_visits=[
            PageVisitUserRead(
                user_id=user_id, username=username, visited_at=visited_at
            )
            for user_id, username, visited_at in recent_visits
        ],
    )


@router.get("/me/history", response_model=list[PageVisitRead])
async def list_my_page_visits(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=200),
    service: PageVisitService = Depends(get_page_visit_service),
    current_user: User = Depends(get_current_user),
) -> list[PageVisitRead]:
    visits = await service.list_user_visits(
        user_id=current_user.id, skip=skip, limit=limit
    )
    return [PageVisitRead.model_validate(visit) for visit in visits]


@router.get("/me/summary", response_model=list[PageUserVisitSummaryRead])
async def list_my_page_visit_summary(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=200),
    service: PageVisitService = Depends(get_page_visit_service),
    current_user: User = Depends(get_current_user),
) -> list[PageUserVisitSummaryRead]:
    summaries = await service.list_user_page_summaries(
        user_id=current_user.id, skip=skip, limit=limit
    )
    return [
        PageUserVisitSummaryRead(
            page_key=summary.page_key,
            visit_count=summary.visit_count,
            first_visited_at=summary.first_visited_at,
            last_visited_at=summary.last_visited_at,
        )
        for summary in summaries
    ]


@router.get(
    "/users/{user_id}/history",
    response_model=list[PageVisitRead],
    dependencies=[Depends(require_roles(UserRole.WORLD_BUILDER, UserRole.ADMIN))],
)
async def list_user_page_visits(
    user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=200),
    service: PageVisitService = Depends(get_page_visit_service),
) -> list[PageVisitRead]:
    visits = await service.list_user_visits(user_id=user_id, skip=skip, limit=limit)
    return [PageVisitRead.model_validate(visit) for visit in visits]


@router.get(
    "/users/{user_id}/summary",
    response_model=list[PageUserVisitSummaryRead],
    dependencies=[Depends(require_roles(UserRole.WORLD_BUILDER, UserRole.ADMIN))],
)
async def list_user_page_visit_summary(
    user_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, gt=0, le=200),
    service: PageVisitService = Depends(get_page_visit_service),
) -> list[PageUserVisitSummaryRead]:
    summaries = await service.list_user_page_summaries(
        user_id=user_id, skip=skip, limit=limit
    )
    return [
        PageUserVisitSummaryRead(
            page_key=summary.page_key,
            visit_count=summary.visit_count,
            first_visited_at=summary.first_visited_at,
            last_visited_at=summary.last_visited_at,
        )
        for summary in summaries
    ]
