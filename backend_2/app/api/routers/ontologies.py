from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.api.deps import (
    get_audit_service,
    get_current_user,
    get_ontology_service,
    require_roles,
)
from app.models.audit import AuditAction, AuditActorType, AuditEntityType
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
from app.services.ontology_service import OntologyService

router = APIRouter(
    prefix="/ontologies",
    tags=["ontologies"],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


@router.post("/", response_model=OntologyRead, status_code=status.HTTP_201_CREATED)
async def create_ontology(
    payload: OntologyCreate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(get_current_user),
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
) -> list[OntologyRead]:
    ontologies = await service.list_ontologies(
        name=name, description=description, skip=skip, limit=limit
    )
    return [OntologyRead.model_validate(o) for o in ontologies]


@router.get("/{ontology_id}", response_model=OntologyRead)
async def get_ontology(
    ontology_id: int, service: OntologyService = Depends(get_ontology_service),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    ontology_id: int, service: OntologyService = Depends(get_ontology_service),
) -> list[OntologyEntityRead]:
    ontology = await service.get_ontology(ontology_id)
    if not ontology:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ontology not found"
        )
    entities = await service.list_entities(ontology_id)
    return [OntologyEntityRead.model_validate(e) for e in entities]


@router.put("/{ontology_id}/entities/{entity_id}", response_model=OntologyEntityRead)
async def update_entity(
    ontology_id: int,
    entity_id: int,
    payload: OntologyEntityUpdate,
    service: OntologyService = Depends(get_ontology_service),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
            payload.model_dump() | {"ontology_id": ontology_id, "entity_id": entity_id,}
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
