from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_audit_service,
    get_current_user,
    get_ontology_service,
    require_roles,
)
from app.db.jobs_session import get_jobs_session
from app.graph.neo4j import get_neo4j_session
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
    OntologyUpdate,
)
from app.services.audit_service import AuditService
from app.services.background_job_service import BackgroundJobService
from app.services.ontology_service import OntologyService
from app.tasks.neo4j_embedding import embed_ontology

router = APIRouter(prefix="/ontologies", tags=["ontologies"])


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


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


@router.get("/", response_model=list[OntologyRead])
async def list_ontologies(
    name: str | None = None,
    description: str | None = None,
    skip: int = 0,
    limit: int = 50,
    service: OntologyService = Depends(get_ontology_service),
    _: User = Depends(get_current_user),
) -> list[OntologyRead]:
    ontologies = await service.list_ontologies(
        name=name,
        description=description,
        skip=skip,
        limit=limit,
    )
    return [OntologyRead.model_validate(o) for o in ontologies]


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


class TriggerEmbeddingRequest(BaseModel):
    """Request to trigger embedding for an ontology."""

    pass  # No additional fields needed


class TriggerEmbeddingResponse(BaseModel):
    """Response after triggering embedding."""

    job_id: str
    ontology_id: int
    message: str


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

    # Query Neo4j for statistics
    stats_query = """
    MATCH (n:EntityInstance)
    WHERE n.ontology_id = $ontology_id
    WITH count(n) AS total,
         count(CASE WHEN n.is_embedded = true THEN 1 END) AS embedded,
         count(CASE WHEN n.is_embedded IS NULL OR n.is_embedded = false THEN 1 END) AS unembedded,
         count(CASE WHEN n.is_embedded = true AND n.last_embedded_date IS NOT NULL AND n.last_updated_date > n.last_embedded_date THEN 1 END) AS outdated
    RETURN total, embedded, unembedded, outdated
    """

    result = await graph_session.run(stats_query, ontology_id=ontology_id)
    record = await result.single()

    if not record:
        # No nodes found for this ontology
        return EmbeddingStatsResponse(
            ontology_id=ontology_id,
            total_nodes=0,
            embedded_nodes=0,
            unembedded_nodes=0,
            outdated_nodes=0,
        )

    return EmbeddingStatsResponse(
        ontology_id=ontology_id,
        total_nodes=record["total"],
        embedded_nodes=record["embedded"],
        unembedded_nodes=record["unembedded"],
        outdated_nodes=record["outdated"],
    )


@router.post(
    "/{ontology_id}/trigger-embedding",
    response_model=TriggerEmbeddingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_embedding(
    ontology_id: int,
    request: TriggerEmbeddingRequest,
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
    result = embed_ontology.delay(
        ontology_id=ontology_id, author_type="user", author_id=str(current_user.id)
    )

    return TriggerEmbeddingResponse(
        job_id=result.id,
        ontology_id=ontology_id,
        message=f"Embedding job triggered for ontology {ontology_id}",
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
