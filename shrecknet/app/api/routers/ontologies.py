from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_audit_service,
    get_current_user,
    get_current_admin_user,
    get_ontology_instance_service,
    get_ontology_service,
    require_roles,
)
from app.db.session import AsyncSessionCompat, get_db_session
from app.db.jobs_session import get_jobs_session
from app.graph.neo4j import get_neo4j_session, get_optional_neo4j_session
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
from app.models.background_job import JobType
from app.models.user import User, UserRole
from app.schemas.ontology import (
    OntologyCreate,
    OntologyEntityCreate,
    OntologyEntityRead,
    OntologyEntityUpdate,
    OntologyPropertyCreate,
    OntologyPropertyRead,
    OntologyPropertyUpdate,
    OntologyRead,
    OntologyRelationshipCreate,
    OntologyRelationshipRead,
    OntologyRelationshipUpdate,
    OntologyWorldStatsResponse,
    OntologyUpdate,
    OntologyCopyRequest,
    OntologyCopyResponse,
)
from app.schemas.ontology_instance import (
    ArchitectReviewEntityCatalogPage,
    ArchitectReviewEntityCatalogResolveRequest,
    ArchitectReviewEntityCatalogResolveResponse,
    OntologyInstanceRead,
    SceneGraphRead,
    SceneGraphSourcePageReference,
)
from app.services.audit_service import AuditService
from app.services.background_job_service import BackgroundJobService
from app.schemas.atlas import AtlasWorldWeb, AtlasFolioDetailRead, AtlasFolioPatch, AtlasSourcePage, AtlasScenePage, AtlasAuthor, AtlasRelationshipPage
from app.services.atlas_folio_service import AtlasFolioService
from app.schemas.agent import AgentSummary
from app.services.agent_service import AgentService
from app.services.atlas_discovery_service import AtlasDiscoveryService
from app.services.ontology_service import OntologyService
from app.services.ontology_instance_service import OntologyInstanceService, FolioEditConflict
from app.tasks.neo4j_embedding import embed_ontology

router = APIRouter(prefix="/ontologies", tags=["ontologies"])
_WORLD_STATS_CACHE_TTL_SECONDS = 60
_world_stats_cache: dict[
    tuple[tuple[int, ...] | None, bool], tuple[datetime, OntologyWorldStatsResponse]
] = {}
_world_stats_cache_lock = asyncio.Lock()


async def _atlas_world(
    ontology_id: int = Path(..., ge=1),
    actor: User = Depends(get_current_user),
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
) -> OntologyInstanceService:
    try:
        await service.assert_ontology_graph_read_access(ontology_id=ontology_id, actor=actor)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service


async def _atlas_reader(service: OntologyInstanceService = Depends(_atlas_world)) -> AtlasDiscoveryService:
    return AtlasDiscoveryService(service.graph_session)


@router.patch("/{ontology_id}/pages/{instance_id}/folio", response_model=AtlasFolioDetailRead)
async def patch_folio_detail(
    ontology_id: int, instance_id: str, payload: AtlasFolioPatch,
    _: User = Depends(get_current_admin_user),
    service: OntologyInstanceService = Depends(_atlas_world),
) -> AtlasFolioDetailRead:
    try:
        return await AtlasFolioService(service).patch(ontology_id, instance_id, payload)
    except FolioEditConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{ontology_id}/pages/{instance_id}/folio", response_model=AtlasFolioDetailRead)
async def get_folio_detail(
    ontology_id: int, instance_id: str,
    service: OntologyInstanceService = Depends(_atlas_world),
) -> AtlasFolioDetailRead:
    try:
        return await AtlasFolioService(service).detail(ontology_id, instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Page unavailable") from exc


@router.get("/{ontology_id}/pages/{instance_id}/authors", response_model=list[AtlasAuthor])
async def get_folio_authors(
    ontology_id: int, instance_id: str,
    service: OntologyInstanceService = Depends(_atlas_world),
) -> list[AtlasAuthor]:
    try:
        return await AtlasFolioService(service).authors(ontology_id, instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Page unavailable") from exc


@router.get("/{ontology_id}/pages/{instance_id}/relationships", response_model=AtlasRelationshipPage)
async def get_folio_relationships(
    ontology_id: int, instance_id: str,
    entity_id: str | None = Query(None, max_length=250), query: str | None = Query(None, max_length=500),
    group: str | None = Query(None, max_length=100),
    skip: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
    service: OntologyInstanceService = Depends(_atlas_world),
) -> AtlasRelationshipPage:
    try:
        return await AtlasFolioService(service).relationships(
            ontology_id=ontology_id, page_id=instance_id, entity_id=entity_id,
            query=query, group=group, skip=skip, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Page unavailable") from exc


@router.get("/{ontology_id}/elders", response_model=list[AgentSummary])
async def get_world_elder_cards(
    ontology_id: int = Path(..., ge=1), skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
    service: OntologyInstanceService = Depends(_atlas_world),
) -> list[AgentSummary]:
    """Authorized, world-filtered portraits; ordered newest first with a stable ID tie-breaker."""
    return await AgentService(service.sql_session).world_elder_cards(ontology_id, skip, limit)


@router.get("/{ontology_id}/world-web", response_model=AtlasWorldWeb)
async def get_world_web(
    ontology_id: int = Path(..., ge=1),
    reader: AtlasDiscoveryService = Depends(_atlas_reader),
) -> AtlasWorldWeb:
    """Compact authorized world topology, with explicit payload limits."""
    return await reader.world_web(ontology_id=ontology_id)


@router.get("/{ontology_id}/scene-sources", response_model=AtlasSourcePage)
async def get_scene_sources(
    ontology_id: int = Path(..., ge=1),
    skip: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=100),
    reader: AtlasDiscoveryService = Depends(_atlas_reader),
) -> AtlasSourcePage:
    """Distinct provenance-source pages, including artwork, in one bounded projection."""
    return await reader.sources(ontology_id=ontology_id, skip=skip, limit=limit)


@router.get("/{ontology_id}/scenes", response_model=AtlasScenePage)
async def get_scene_index(
    ontology_id: int = Path(..., ge=1),
    query: str | None = Query(None, max_length=500),
    related_entity_id: str | None = Query(None, max_length=250),
    source_entity_id: str | None = Query(None, max_length=250),
    has_perspectives: bool = False,
    related_only: bool = False,
    skip: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
    actor: User = Depends(get_current_user),
    reader: AtlasDiscoveryService = Depends(_atlas_reader),
) -> AtlasScenePage:
    """Filter scenes before pagination and hydrate only authorized card metadata."""
    if related_only and not related_entity_id:
        raise HTTPException(status_code=422, detail="related_entity_id is required when related_only is true")
    role = actor.role.value if hasattr(actor.role, "value") else str(actor.role)
    return await reader.scenes(
        ontology_id=ontology_id, query=query, related_entity_id=related_entity_id,
        source_entity_id=source_entity_id, has_perspectives=has_perspectives, related_only=related_only,
        include_private=role.lower() == UserRole.ADMIN.value, skip=skip, limit=limit,
    )


@router.get("/{ontology_id}/scenes/{scene_id}/source", response_model=SceneGraphSourcePageReference)
async def get_scene_source(
    ontology_id: int = Path(..., ge=1), scene_id: str = Path(..., min_length=1, max_length=250),
    reader: AtlasDiscoveryService = Depends(_atlas_reader),
) -> SceneGraphSourcePageReference:
    """Resolve scene evidence to the exact provenance page, not its owning container."""
    try:
        return await reader.scene_source(ontology_id=ontology_id, scene_id=scene_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/{ontology_id}/scenes/{scene_id}/graph",
    response_model=SceneGraphRead,
    summary="Read an authorized, UI-ready scene graph projection",
)
async def get_scene_graph(
    ontology_id: int = Path(..., ge=1),
    scene_id: str = Path(..., min_length=1),
    actor: User = Depends(get_current_user),
    service: OntologyInstanceService = Depends(_atlas_world),
) -> SceneGraphRead:
    """Return canonical scene data and only perspectives visible to this actor."""
    try:
        role = actor.role.value if hasattr(actor.role, "value") else str(actor.role)
        return await service.get_scene_graph(
            ontology_id=ontology_id,
            scene_id=scene_id,
            include_private_perspectives=role.lower() == UserRole.ADMIN.value,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{ontology_id}/architect-review/entity-catalog",
    response_model=ArchitectReviewEntityCatalogPage,
    summary="List compact entities for Architect proposal review",
)
async def list_architect_review_entity_catalog(
    ontology_id: int = Path(..., ge=1),
    entity_definition_id: int | None = Query(None, ge=1),
    query: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> ArchitectReviewEntityCatalogPage:
    try:
        return await service.list_architect_review_entity_catalog(
            ontology_id=ontology_id,
            entity_definition_id=entity_definition_id,
            query=query,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/{ontology_id}/architect-review/entity-catalog/resolve",
    response_model=ArchitectReviewEntityCatalogResolveResponse,
    summary="Resolve compact entities for Architect proposals",
)
async def resolve_architect_review_entity_catalog(
    payload: ArchitectReviewEntityCatalogResolveRequest,
    ontology_id: int = Path(..., ge=1),
    service: OntologyInstanceService = Depends(get_ontology_instance_service),
    _: User = Depends(get_current_user),
) -> ArchitectReviewEntityCatalogResolveResponse:
    try:
        return await service.resolve_architect_review_entity_catalog(
            ontology_id=ontology_id,
            entity_instance_ids=payload.entity_instance_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _parse_ontology_ids_csv(value: str | None) -> list[int] | None:
    if value is None:
        return None

    parts = [item.strip() for item in value.split(",")]
    clean_parts = [item for item in parts if item]
    if not clean_parts:
        return []

    out: list[int] = []
    for raw in clean_parts:
        try:
            parsed = int(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid ontology id '{raw}' in ontology_ids",
            ) from exc
        if parsed <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid ontology id '{raw}' in ontology_ids",
            )
        out.append(parsed)

    # Preserve order while removing duplicates.
    deduped = list(dict.fromkeys(out))
    return deduped


def _world_stats_cache_key(
    ontology_ids: list[int] | None, include_content_counts: bool
) -> tuple[tuple[int, ...] | None, bool]:
    if ontology_ids is None:
        return (None, include_content_counts)
    return (tuple(sorted(ontology_ids)), include_content_counts)


async def _get_safe_ontology_embedding_stats(
    graph_session: Any, ontology_id: int
) -> dict[str, Any]:
    """Read embedding stats without triggering Neo4j unknown schema warnings."""
    stats_query = """
    MATCH (n)
    WHERE toInteger(n.ontology_id) = toInteger($ontology_id)
            AND any(label IN labels(n) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
    WITH CASE
            WHEN 'EntityInstance' IN labels(n) THEN 'entities'
            WHEN 'Scene' IN labels(n) THEN 'scenes'
            WHEN 'Milestone' IN labels(n) THEN 'milestones'
            ELSE 'other'
         END AS node_type,
         n
    WITH node_type,
         count(n) AS total,
         count(CASE WHEN coalesce(n["is_embedded"], false) = true THEN 1 END) AS embedded,
         count(CASE WHEN coalesce(n["is_embedded"], false) = false THEN 1 END) AS unembedded,
         count(CASE
             WHEN coalesce(n["is_embedded"], false) = true
             AND n["last_embedded_date"] IS NOT NULL
             AND coalesce(n["last_updated_date"], n["updated_at"]) IS NOT NULL
             AND coalesce(n["last_updated_date"], n["updated_at"]) > n["last_embedded_date"]
             THEN 1
         END) AS outdated
    RETURN node_type, total, embedded, unembedded, outdated
    """
    result = await graph_session.run(stats_query, ontology_id=ontology_id)
    rows = await result.data()
    breakdown = {
        "entities": {"total": 0, "embedded": 0, "unembedded": 0, "outdated": 0},
        "scenes": {"total": 0, "embedded": 0, "unembedded": 0, "outdated": 0},
        "milestones": {"total": 0, "embedded": 0, "unembedded": 0, "outdated": 0},
    }
    for row in rows:
        node_type = row.get("node_type")
        if node_type not in breakdown:
            continue
        breakdown[node_type] = {
            "total": int(row.get("total") or 0),
            "embedded": int(row.get("embedded") or 0),
            "unembedded": int(row.get("unembedded") or 0),
            "outdated": int(row.get("outdated") or 0),
        }

    totals = {
        "total": sum(item["total"] for item in breakdown.values()),
        "embedded": sum(item["embedded"] for item in breakdown.values()),
        "unembedded": sum(item["unembedded"] for item in breakdown.values()),
        "outdated": sum(item["outdated"] for item in breakdown.values()),
    }
    return {
        **totals,
        "breakdown": breakdown,
    }


async def _count_ontology_nodes_by_type(graph_session: Any, ontology_id: int) -> dict[str, int]:
    query = """
    MATCH (n)
    WHERE toInteger(n.ontology_id) = toInteger($ontology_id)
            AND any(label IN labels(n) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
    WITH CASE
            WHEN 'EntityInstance' IN labels(n) THEN 'entities'
            WHEN 'Scene' IN labels(n) THEN 'scenes'
            WHEN 'Milestone' IN labels(n) THEN 'milestones'
            ELSE 'other'
         END AS node_type,
         count(n) AS count
    RETURN node_type, count
    """
    result = await graph_session.run(query, ontology_id=ontology_id)
    rows = await result.data()
    counts = {"entities": 0, "scenes": 0, "milestones": 0}
    for row in rows:
        node_type = row.get("node_type")
        if node_type in counts:
            counts[node_type] = int(row.get("count") or 0)
    return counts


@router.post("/", response_model=OntologyRead, status_code=status.HTTP_201_CREATED)
async def create_ontology(
    payload: OntologyCreate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyRead:
    existing = await service.repository.get_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ontology name already exists"
        )

    ontology = await service.create_ontology(payload.model_dump())
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.ONTOLOGY,
        entity_id=ontology.id,
        payload=_sanitize_payload(payload.model_dump()),
        description="Created ontology",
    )
    return OntologyRead.model_validate(ontology)


@router.post(
    "/{target_ontology_id}/copy-definitions",
    response_model=OntologyCopyResponse,
)
async def copy_ontology_definitions(
    target_ontology_id: int,
    payload: OntologyCopyRequest,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyCopyResponse:
    try:
        result = await service.copy_definitions(
            payload.source_ontology_id, target_ontology_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return result


@router.get("/", response_model=list[OntologyRead])
async def list_ontologies(
    name: str | None = None,
    description: str | None = None,
    rpg_system: str | None = None,
    skip: int = 0,
    limit: int = 50,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> list[OntologyRead]:
    ontologies = await service.list_ontologies(
        name=name,
        description=description,
        rpg_system=rpg_system,
        skip=skip,
        limit=limit,
    )
    return [OntologyRead.model_validate(o) for o in ontologies]


@router.get("/world-stats", response_model=OntologyWorldStatsResponse)
async def get_world_stats(
    ontology_ids: str | None = None,
    include_content_counts: bool = True,
    service: OntologyService = Depends(get_ontology_service),
    graph_session: Any | None = Depends(get_optional_neo4j_session),
    _: User = Depends(get_current_user),
) -> OntologyWorldStatsResponse:
    parsed_ontology_ids = _parse_ontology_ids_csv(ontology_ids)
    if parsed_ontology_ids == []:
        return OntologyWorldStatsResponse(results=[])
    cache_key = _world_stats_cache_key(parsed_ontology_ids, include_content_counts)
    now = datetime.now(timezone.utc)

    cached = _world_stats_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    async with _world_stats_cache_lock:
        cached = _world_stats_cache.get(cache_key)
        now = datetime.now(timezone.utc)
        if cached and cached[0] > now:
            return cached[1]

        rows = await service.get_world_stats(
            ontology_ids=parsed_ontology_ids,
            include_content_counts=include_content_counts,
            graph_session=graph_session,
        )
        response = OntologyWorldStatsResponse(results=rows)

        expires_at = now + timedelta(seconds=_WORLD_STATS_CACHE_TTL_SECONDS)
        _world_stats_cache[cache_key] = (expires_at, response)
        return response


@router.get("/{ontology_id}", response_model=OntologyRead)
async def get_ontology(
    ontology_id: int,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> OntologyRead:
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )
    return OntologyRead.model_validate(ontology)


@router.put("/{ontology_id}", response_model=OntologyRead)
async def update_ontology(
    ontology_id: int,
    payload: OntologyUpdate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyRead:
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )
    update_data = payload.model_dump(exclude_unset=True)
    updated = await service.update_ontology(ontology, update_data)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.ONTOLOGY,
        entity_id=ontology_id,
        payload=_sanitize_payload(update_data),
        description="Updated ontology",
    )
    return OntologyRead.model_validate(updated)


@router.delete(
    "/{ontology_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_ontology(
    ontology_id: int,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )
    await service.delete_ontology(ontology)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.ONTOLOGY,
        entity_id=ontology_id,
        payload={"ontology_id": ontology_id, "name": ontology.name},
        description="Deleted ontology",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Entity endpoints -------------------------------------------------------


@router.post(
    "/{ontology_id}/entities",
    response_model=OntologyEntityRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_entity(
    ontology_id: int,
    payload: OntologyEntityCreate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyEntityRead:
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )
    entity = await service.add_entity(ontology_id, payload.model_dump())
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.ONTOLOGY_ENTITY,
        entity_id=entity.id,
        payload=_sanitize_payload(payload.model_dump() | {"ontology_id": ontology_id}),
        description="Created ontology entity",
    )
    return OntologyEntityRead.model_validate(entity)


@router.get("/{ontology_id}/entities", response_model=list[OntologyEntityRead])
async def list_entities(
    ontology_id: int,
    display_on_world: bool | None = None,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> list[OntologyEntityRead]:
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )
    entities = await service.list_entities(
        ontology_id, display_on_world=display_on_world
    )
    return [OntologyEntityRead.model_validate(e) for e in entities]


@router.put("/{ontology_id}/entities/{entity_id}", response_model=OntologyEntityRead)
async def update_entity(
    ontology_id: int,
    entity_id: int,
    payload: OntologyEntityUpdate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyEntityRead:
    entity = await service.get_entity(ontology_id, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    update_data = payload.model_dump(exclude_unset=True)
    updated = await service.update_entity(entity, update_data)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.ONTOLOGY_ENTITY,
        entity_id=entity_id,
        payload=_sanitize_payload(update_data | {"ontology_id": ontology_id}),
        description="Updated ontology entity",
    )
    return OntologyEntityRead.model_validate(updated)


@router.delete(
    "/{ontology_id}/entities/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_entity(
    ontology_id: int,
    entity_id: int,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    entity = await service.get_entity(ontology_id, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    await service.delete_entity(entity)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.ONTOLOGY_ENTITY,
        entity_id=entity_id,
        payload={
            "ontology_id": ontology_id,
            "entity_id": entity_id,
            "name": entity.name,
        },
        description="Deleted ontology entity",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Property endpoints -----------------------------------------------------


@router.post(
    "/{ontology_id}/entities/{entity_id}/properties",
    response_model=OntologyPropertyRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_property(
    ontology_id: int,
    entity_id: int,
    payload: OntologyPropertyCreate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyPropertyRead:
    entity = await service.get_entity(ontology_id, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    prop = await service.add_property(ontology_id, entity_id, payload.model_dump())
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.ONTOLOGY_PROPERTY,
        entity_id=prop.id,
        payload=_sanitize_payload(
            payload.model_dump()
            | {
                "ontology_id": ontology_id,
                "entity_id": entity_id,
            }
        ),
        description="Created entity property",
    )
    return OntologyPropertyRead.model_validate(prop)


@router.get(
    "/{ontology_id}/entities/{entity_id}/properties",
    response_model=list[OntologyPropertyRead],
)
async def list_properties(
    ontology_id: int,
    entity_id: int,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> list[OntologyPropertyRead]:
    entity = await service.get_entity(ontology_id, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    properties = await service.list_properties(ontology_id, entity_id)
    return [OntologyPropertyRead.model_validate(p) for p in properties]


@router.put(
    "/{ontology_id}/entities/{entity_id}/properties/{property_id}",
    response_model=OntologyPropertyRead,
)
async def update_property(
    ontology_id: int,
    entity_id: int,
    property_id: int,
    payload: OntologyPropertyUpdate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyPropertyRead:
    prop = await service.get_property(ontology_id, entity_id, property_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Property not found"
        )
    update_data = payload.model_dump(exclude_unset=True)
    updated = await service.update_property(prop, update_data)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.ONTOLOGY_PROPERTY,
        entity_id=property_id,
        payload=_sanitize_payload(
            update_data
            | {
                "ontology_id": ontology_id,
                "entity_id": entity_id,
                "property_id": property_id,
            }
        ),
        description="Updated entity property",
    )
    return OntologyPropertyRead.model_validate(updated)


@router.delete(
    "/{ontology_id}/entities/{entity_id}/properties/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_property(
    ontology_id: int,
    entity_id: int,
    property_id: int,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    prop = await service.get_property(ontology_id, entity_id, property_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Property not found"
        )
    await service.delete_property(prop)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.ONTOLOGY_PROPERTY,
        entity_id=property_id,
        payload={
            "ontology_id": ontology_id,
            "entity_id": entity_id,
            "property_id": property_id,
            "name": prop.name,
        },
        description="Deleted entity property",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Relationship endpoints -------------------------------------------------


@router.post(
    "/{ontology_id}/entities/{entity_id}/relationships",
    response_model=OntologyRelationshipRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_relationship(
    ontology_id: int,
    entity_id: int,
    payload: OntologyRelationshipCreate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyRelationshipRead:
    entity = await service.get_entity(ontology_id, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    relationship = await service.add_relationship(
        ontology_id, entity_id, payload.model_dump()
    )
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.CREATE,
        entity_type=AuditEntityType.ONTOLOGY_RELATIONSHIP,
        entity_id=relationship.id,
        payload=_sanitize_payload(
            payload.model_dump()
            | {
                "ontology_id": ontology_id,
                "entity_id": entity_id,
                "destiny_entity_id": payload.destiny_entity_id,
            }
        ),
        description="Created entity relationship",
    )
    return OntologyRelationshipRead.model_validate(relationship)


@router.get(
    "/{ontology_id}/entities/{entity_id}/relationships",
    response_model=list[OntologyRelationshipRead],
)
async def list_relationships(
    ontology_id: int,
    entity_id: int,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> list[OntologyRelationshipRead]:
    entity = await service.get_entity(ontology_id, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found"
        )
    relationships = await service.list_relationships(ontology_id, entity_id)
    return [OntologyRelationshipRead.model_validate(r) for r in relationships]


@router.put(
    "/{ontology_id}/entities/{entity_id}/relationships/{relationship_id}",
    response_model=OntologyRelationshipRead,
)
async def update_relationship(
    ontology_id: int,
    entity_id: int,
    relationship_id: int,
    payload: OntologyRelationshipUpdate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> OntologyRelationshipRead:
    relationship = await service.get_relationship(
        ontology_id, entity_id, relationship_id
    )
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found"
        )
    update_data = payload.model_dump(exclude_unset=True)
    updated = await service.update_relationship(relationship, update_data)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.UPDATE,
        entity_type=AuditEntityType.ONTOLOGY_RELATIONSHIP,
        entity_id=relationship_id,
        payload=_sanitize_payload(
            update_data
            | {
                "ontology_id": ontology_id,
                "entity_id": entity_id,
                "relationship_id": relationship_id,
                "destiny_entity_id": updated.destiny_entity_id,
            }
        ),
        description="Updated entity relationship",
    )
    return OntologyRelationshipRead.model_validate(updated)


@router.delete(
    "/{ontology_id}/entities/{entity_id}/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_relationship(
    ontology_id: int,
    entity_id: int,
    relationship_id: int,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    relationship = await service.get_relationship(
        ontology_id, entity_id, relationship_id
    )
    if not relationship:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Relationship not found"
        )
    await service.delete_relationship(relationship)
    await audit_service.log_action(
        actor_type=AuditActorType.USER,
        actor_user_id=current_user.id,
        action=AuditAction.DELETE,
        entity_type=AuditEntityType.ONTOLOGY_RELATIONSHIP,
        entity_id=relationship_id,
        payload={
            "ontology_id": ontology_id,
            "entity_id": entity_id,
            "relationship_id": relationship_id,
            "name": relationship.name,
            "destiny_entity_id": relationship.destiny_entity_id,
        },
        description="Deleted entity relationship",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Embedding endpoints ----------------------------------------------------


class EmbeddingStatsResponse(BaseModel):
    """Embedding statistics for an ontology."""

    ontology_id: int
    total_nodes: int
    embedded_nodes: int
    unembedded_nodes: int
    outdated_nodes: int
    entities: dict[str, int]
    scenes: dict[str, int]
    milestones: dict[str, int]


class TriggerEmbeddingRequest(BaseModel):
    """Request to trigger embedding for an ontology."""

    pass  # No additional fields needed


class TriggerEmbeddingResponse(BaseModel):
    """Response after triggering embedding."""

    job_id: str
    ontology_id: int
    message: str
    requested_entities: int
    requested_scenes: int
    requested_milestones: int


@router.get("/{ontology_id}/embedding-stats", response_model=EmbeddingStatsResponse)
async def get_embedding_stats(
    ontology_id: int,
    graph_session: Annotated[Any, Depends(get_neo4j_session)],
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> EmbeddingStatsResponse:
    """
    Get embedding statistics for an ontology.

    Returns counts of total, embedded, unembedded, and outdated nodes.
    """
    # Verify ontology exists
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )

    stats = await _get_safe_ontology_embedding_stats(graph_session, ontology_id)

    return EmbeddingStatsResponse(
        ontology_id=ontology_id,
        total_nodes=stats["total"],
        embedded_nodes=stats["embedded"],
        unembedded_nodes=stats["unembedded"],
        outdated_nodes=stats["outdated"],
        entities=stats["breakdown"]["entities"],
        scenes=stats["breakdown"]["scenes"],
        milestones=stats["breakdown"]["milestones"],
    )


@router.post(
    "/{ontology_id}/trigger-embedding",
    response_model=TriggerEmbeddingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_embedding(
    ontology_id: int,
    request: TriggerEmbeddingRequest,
    graph_session: Annotated[Any, Depends(get_neo4j_session)],
    service: OntologyService = Depends(get_ontology_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> TriggerEmbeddingResponse:
    """
    Trigger embedding job for an ontology.

    This will embed all unembedded or outdated nodes in the ontology.
    """
    # Verify ontology exists
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )

    # Trigger the Celery task
    counts = await _count_ontology_nodes_by_type(graph_session, ontology_id)
    result = embed_ontology.delay(
        ontology_id=ontology_id, author_type="user", author_id=str(current_user.id)
    )

    return TriggerEmbeddingResponse(
        job_id=result.id,
        ontology_id=ontology_id,
        message=f"Embedding job triggered for ontology {ontology_id}",
        requested_entities=counts["entities"],
        requested_scenes=counts["scenes"],
        requested_milestones=counts["milestones"],
    )


@router.get("/{ontology_id}/embedding-jobs", response_model=list[dict[str, Any]])
async def get_embedding_jobs(
    ontology_id: int,
    jobs_session: Annotated[AsyncSession, Depends(get_jobs_session)],
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Get recent embedding jobs for an ontology.

    Returns the last 10 embedding jobs by default.
    """
    # Verify ontology exists
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )

    # Query background jobs for this ontology
    job_service = BackgroundJobService(jobs_session)
    jobs = await job_service.list_jobs(
        job_type=JobType.NEO4J_EMBEDDING,
        ontology_id=ontology_id,
        limit=min(limit, 10),  # Cap at 10
        offset=0,
    )

    # Convert to frontend format
    return [
        {
            "kind": job.job_type,
            "job_id": str(job.id),
            "start_time": job.started_at.isoformat(),
            "status": job.status,
            "author_type": job.author_type,
            "author_id": job.author_id,
            "description": job.description,
            "details": job.details,
            "progress": job.progress,
            "error_message": job.error_message,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "duration_seconds": job.duration_seconds,
            "ontology_id": job.ontology_id,
            "updated_at": job.updated_at.isoformat(),
        }
        for job in jobs
    ]


# Migration endpoints ----------------------------------------------------


class MigrationResponse(BaseModel):
    """Response after running a migration."""

    nodes_migrated: int
    status: str
    message: str


@router.post(
    "/migrate-embedding-properties",
    response_model=MigrationResponse,
    status_code=status.HTTP_200_OK,
)
async def migrate_embedding_properties(
    graph_session: Annotated[Any, Depends(get_neo4j_session)],
    _: User = Depends(require_roles(UserRole.ADMIN)),
) -> MigrationResponse:
    """
    Migrate existing EntityInstance nodes to have embedding properties.

    This endpoint allows manual execution of the migration that adds is_embedded
    and last_embedded_date properties to existing EntityInstance nodes.

    This migration runs automatically on application startup, but this endpoint
    allows re-running it manually if needed.

    Only accessible to admin users.
    """
    from app.db.migrations import migrate_neo4j_embedding_properties

    result = await migrate_neo4j_embedding_properties(graph_session)

    return MigrationResponse(
        nodes_migrated=result["nodes_migrated"],
        status=result["status"],
        message=f"Successfully migrated {result['nodes_migrated']} nodes with embedding properties",
    )
