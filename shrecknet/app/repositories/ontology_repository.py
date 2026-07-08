from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology import (
    Ontology,
    OntologyEntity,
    OntologyProperty,
    OntologyRelationship,
)
from app.repositories.base import BaseRepository


class OntologyRepository(BaseRepository):
    """Data access helpers for ontologies and related objects."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        name: str | None = None,
        description: str | None = None,
        rpg_system: str | None = None,
    ) -> Sequence[Ontology]:
        query: Select[tuple[Ontology]] = select(Ontology).offset(skip).limit(limit)
        if name:
            query = query.where(Ontology.name.ilike(f"%{name}%"))
        if description:
            query = query.where(Ontology.description.ilike(f"%{description}%"))
        if rpg_system:
            query = query.where(Ontology.rpg_system.ilike(f"%{rpg_system}%"))
        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def get(self, ontology_id: int) -> Ontology | None:
        result = await self.session.execute(
            select(Ontology).where(Ontology.id == ontology_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Ontology | None:
        result = await self.session.execute(
            select(Ontology).where(Ontology.name == name)
        )
        return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> Ontology:
        ontology = Ontology(**data)
        await self.save(ontology)
        await self.session.refresh(ontology)
        return ontology

    async def update(self, ontology: Ontology, data: dict[str, Any]) -> Ontology:
        for key, value in data.items():
            setattr(ontology, key, value)
        await self.save(ontology)
        await self.session.refresh(ontology)
        return ontology

    async def remove(self, ontology: Ontology) -> None:
        await self.delete(ontology)

    # Entity operations --------------------------------------------------
    async def add_entity(
        self,
        ontology_id: int,
        data: dict[str, Any],
    ) -> OntologyEntity:
        entity = OntologyEntity(ontology_id=ontology_id, **data)
        await self.save(entity)
        await self.session.refresh(entity)
        return entity

    async def get_entity(
        self, ontology_id: int, entity_id: int
    ) -> OntologyEntity | None:
        result = await self.session.execute(
            select(OntologyEntity)
            .options(
                selectinload(OntologyEntity.properties),
                selectinload(OntologyEntity.relationships),
            )
            .where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyEntity.id == entity_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_entity_by_id(self, entity_id: int) -> OntologyEntity | None:
        result = await self.session.execute(
            select(OntologyEntity).where(OntologyEntity.id == entity_id)
        )
        return result.scalar_one_or_none()

    async def list_entities(
        self, ontology_id: int, *, display_on_world: bool | None = None
    ) -> Sequence[OntologyEntity]:
        query = (
            select(OntologyEntity)
            .options(
                selectinload(OntologyEntity.properties),
                selectinload(OntologyEntity.relationships),
            )
            .where(OntologyEntity.ontology_id == ontology_id)
        )
        if display_on_world is not None:
            query = query.where(OntologyEntity.display_on_world == display_on_world)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def update_entity(
        self,
        entity: OntologyEntity,
        data: dict[str, Any],
    ) -> OntologyEntity:
        for key, value in data.items():
            setattr(entity, key, value)
        await self.save(entity)
        await self.session.refresh(entity)
        return entity

    async def remove_entity(self, entity: OntologyEntity) -> None:
        await self.delete(entity)

    # Property operations -----------------------------------------------
    async def add_property(
        self, ontology_id: int, entity_id: int, data: dict[str, Any]
    ) -> OntologyProperty:
        entity = await self.get_entity(ontology_id, entity_id)
        if entity is None:
            raise ValueError("Entity not found for ontology")
        prop = OntologyProperty(entity_id=entity_id, **data)
        await self.save(prop)
        await self.session.refresh(prop)
        return prop

    async def get_property(
        self, ontology_id: int, entity_id: int, property_id: int
    ) -> OntologyProperty | None:
        result = await self.session.execute(
            select(OntologyProperty)
            .join(OntologyEntity)
            .where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyProperty.entity_id == entity_id,
                OntologyProperty.id == property_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_properties(
        self, ontology_id: int, entity_id: int
    ) -> Sequence[OntologyProperty]:
        result = await self.session.execute(
            select(OntologyProperty)
            .join(OntologyEntity)
            .where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyProperty.entity_id == entity_id,
            )
        )
        return result.scalars().all()

    async def update_property(
        self,
        prop: OntologyProperty,
        data: dict[str, Any],
    ) -> OntologyProperty:
        for key, value in data.items():
            setattr(prop, key, value)
        await self.save(prop)
        await self.session.refresh(prop)
        return prop

    async def remove_property(self, prop: OntologyProperty) -> None:
        await self.delete(prop)

    async def get_property_by_id(self, property_id: int) -> OntologyProperty | None:
        result = await self.session.execute(
            select(OntologyProperty).where(OntologyProperty.id == property_id)
        )
        return result.scalar_one_or_none()

    # Relationship operations ------------------------------------------
    async def add_relationship(
        self, ontology_id: int, entity_id: int, data: dict[str, Any]
    ) -> OntologyRelationship:
        entity = await self.get_entity(ontology_id, entity_id)
        if entity is None:
            raise ValueError("Entity not found for ontology")
        destiny_id = data.get("destiny_entity_id")
        if destiny_id is not None:
            destiny = await self.get_entity(ontology_id, destiny_id)
            if destiny is None:
                raise ValueError("Destiny entity not found in ontology")
        rel = OntologyRelationship(entity_id=entity_id, **data)
        await self.save(rel)
        await self.session.refresh(rel, attribute_names=["entity", "destiny_entity"])
        return rel

    async def get_relationship(
        self, ontology_id: int, entity_id: int, relationship_id: int
    ) -> OntologyRelationship | None:
        result = await self.session.execute(
            select(OntologyRelationship)
            .options(selectinload(OntologyRelationship.entity))
            .join(
                OntologyEntity,
                OntologyRelationship.entity_id == OntologyEntity.id,
            )
            .where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyRelationship.entity_id == entity_id,
                OntologyRelationship.id == relationship_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_relationships(
        self, ontology_id: int, entity_id: int
    ) -> Sequence[OntologyRelationship]:
        result = await self.session.execute(
            select(OntologyRelationship)
            .options(
                selectinload(OntologyRelationship.entity),
                selectinload(OntologyRelationship.destiny_entity),
            )
            .join(
                OntologyEntity,
                OntologyRelationship.entity_id == OntologyEntity.id,
            )
            .where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyRelationship.entity_id == entity_id,
            )
        )
        return result.scalars().all()

    async def update_relationship(
        self,
        relationship: OntologyRelationship,
        data: dict[str, Any],
    ) -> OntologyRelationship:
        for key, value in data.items():
            setattr(relationship, key, value)
        await self.save(relationship)
        await self.session.refresh(
            relationship, attribute_names=["entity", "destiny_entity"]
        )
        return relationship

    async def get_relationship_by_id(
        self, relationship_id: int
    ) -> OntologyRelationship | None:
        result = await self.session.execute(
            select(OntologyRelationship).where(
                OntologyRelationship.id == relationship_id
            )
        )
        return result.scalar_one_or_none()

    async def remove_relationship(self, relationship: OntologyRelationship) -> None:
        await self.delete(relationship)

    async def find_relationship_between(
        self, ontology_id: int, source_entity_id: int, destiny_entity_id: int | None
    ) -> OntologyRelationship | None:
        if destiny_entity_id is None:
            return None
        result = await self.session.execute(
            select(OntologyRelationship)
            .options(
                selectinload(OntologyRelationship.entity),
                selectinload(OntologyRelationship.destiny_entity),
            )
            .join(
                OntologyEntity,
                OntologyRelationship.entity_id == OntologyEntity.id,
            )
            .where(
                OntologyEntity.ontology_id == ontology_id,
                OntologyRelationship.entity_id == source_entity_id,
                OntologyRelationship.destiny_entity_id == destiny_entity_id,
            )
        )
        return result.scalar_one_or_none()
