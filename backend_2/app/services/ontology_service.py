from __future__ import annotations

from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology import (
    AuthorType,
    Ontology,
    OntologyEntity,
    OntologyProperty,
    OntologyRelationship,
)
from app.repositories.ontology_repository import OntologyRepository


class OntologyService:
    """Business logic for managing ontologies and related resources."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = OntologyRepository(session)

    # Ontologies --------------------------------------------------------
    async def create_ontology(self, data: dict) -> Ontology:
        ontology = await self.repository.create(data)
        await self.session.commit()
        return ontology

    async def list_ontologies(
        self,
        *,
        skip: int = 0,
        limit: int = 50,
        name: str | None = None,
        description: str | None = None,
    ) -> Sequence[Ontology]:
        return await self.repository.list(
            skip=skip, limit=limit, name=name, description=description
        )

    async def get_ontology(self, ontology_id: int) -> Ontology | None:
        return await self.repository.get(ontology_id)

    async def update_ontology(self, ontology: Ontology, data: dict) -> Ontology:
        updated = await self.repository.update(ontology, data)
        await self.session.commit()
        return updated

    async def delete_ontology(self, ontology: Ontology) -> None:
        await self.repository.remove(ontology)
        await self.session.commit()

    # Entities ----------------------------------------------------------
    async def add_entity(self, ontology_id: int, data: dict) -> OntologyEntity:
        self._validate_author_payload(data)
        entity = await self.repository.add_entity(ontology_id, data)
        await self.session.commit()
        return entity

    async def list_entities(self, ontology_id: int) -> Sequence[OntologyEntity]:
        return await self.repository.list_entities(ontology_id)

    async def get_entity(
        self, ontology_id: int, entity_id: int
    ) -> OntologyEntity | None:
        return await self.repository.get_entity(ontology_id, entity_id)

    async def update_entity(self, entity: OntologyEntity, data: dict) -> OntologyEntity:
        self._validate_author_payload(data, allow_missing=True)
        updated = await self.repository.update_entity(entity, data)
        await self.session.commit()
        return updated

    async def delete_entity(self, entity: OntologyEntity) -> None:
        await self.repository.remove_entity(entity)
        await self.session.commit()

    # Properties --------------------------------------------------------
    async def add_property(
        self, ontology_id: int, entity_id: int, data: dict
    ) -> OntologyProperty:
        self._validate_author_payload(data)
        prop = await self.repository.add_property(ontology_id, entity_id, data)
        await self.session.commit()
        return prop

    async def list_properties(
        self, ontology_id: int, entity_id: int
    ) -> Sequence[OntologyProperty]:
        return await self.repository.list_properties(ontology_id, entity_id)

    async def get_property(
        self, ontology_id: int, entity_id: int, property_id: int
    ) -> OntologyProperty | None:
        return await self.repository.get_property(ontology_id, entity_id, property_id)

    async def update_property(
        self, prop: OntologyProperty, data: dict
    ) -> OntologyProperty:
        self._validate_author_payload(data, allow_missing=True)
        updated = await self.repository.update_property(prop, data)
        await self.session.commit()
        return updated

    async def delete_property(self, prop: OntologyProperty) -> None:
        await self.repository.remove_property(prop)
        await self.session.commit()

    # Relationships -----------------------------------------------------
    async def add_relationship(
        self, ontology_id: int, entity_id: int, data: dict
    ) -> OntologyRelationship:
        self._validate_author_payload(data)
        await self._validate_relationship_entities(
            ontology_id, entity_id, data, allow_missing=True
        )
        rel = await self.repository.add_relationship(ontology_id, entity_id, data)
        await self._sync_bidirectional_relationship(ontology_id, rel)
        await self.session.commit()
        return rel

    async def list_relationships(
        self, ontology_id: int, entity_id: int
    ) -> Sequence[OntologyRelationship]:
        return await self.repository.list_relationships(ontology_id, entity_id)

    async def get_relationship(
        self, ontology_id: int, entity_id: int, relationship_id: int
    ) -> OntologyRelationship | None:
        return await self.repository.get_relationship(
            ontology_id, entity_id, relationship_id
        )

    async def update_relationship(
        self, relationship: OntologyRelationship, data: dict,
    ) -> OntologyRelationship:
        self._validate_author_payload(data, allow_missing=True)
        ontology_id = relationship.entity.ontology_id
        entity_id = relationship.entity_id
        await self._validate_relationship_entities(
            ontology_id, entity_id, data, allow_missing=True
        )
        updated = await self.repository.update_relationship(relationship, data)
        await self._sync_bidirectional_relationship(ontology_id, updated)
        await self.session.commit()
        return updated

    async def delete_relationship(self, relationship: OntologyRelationship) -> None:
        ontology_id = relationship.entity.ontology_id
        await self._remove_mirror_relationship(ontology_id, relationship)
        await self.repository.remove_relationship(relationship)
        await self.session.commit()

    # Helpers -----------------------------------------------------------
    def _validate_author_payload(
        self, payload: dict, *, allow_missing: bool = False
    ) -> None:
        if "author_type" not in payload:
            if allow_missing:
                return
            raise ValueError("author_type must be provided")

        raw_author_type = payload.get("author_type")
        try:
            author_type = AuthorType(raw_author_type)
        except ValueError as exc:
            raise ValueError(f"Invalid author_type: {raw_author_type}") from exc
        user_id = payload.get("user_id")
        agent_id = payload.get("agent_id")

        if author_type == AuthorType.HUMAN:
            if not user_id:
                raise ValueError("user_id is required when author_type is human")
            payload["agent_id"] = None
        elif author_type == AuthorType.AGENT:
            if not agent_id:
                raise ValueError("agent_id is required when author_type is agent")
            payload["user_id"] = None

        if user_id and agent_id:
            raise ValueError("Only one of user_id or agent_id can be set")

    async def _validate_relationship_entities(
        self,
        ontology_id: int,
        entity_id: int,
        data: dict,
        *,
        allow_missing: bool = False,
    ) -> None:
        if allow_missing and "destiny_entity_id" not in data:
            return
        destiny_id = data.get("destiny_entity_id")
        if destiny_id is None:
            return
        destiny = await self.repository.get_entity(ontology_id, destiny_id)
        if destiny is None:
            raise ValueError("Destiny entity must belong to the same ontology")

    async def _sync_bidirectional_relationship(
        self, ontology_id: int, relationship: OntologyRelationship
    ) -> None:
        destiny_id = relationship.destiny_entity_id
        source_id = relationship.entity_id
        if destiny_id is None:
            await self._remove_mirror_relationship(ontology_id, relationship)
            return

        mirror = await self.repository.find_relationship_between(
            ontology_id, destiny_id, source_id
        )

        if relationship.bi_directional:
            if mirror is None:
                mirror_data = {
                    "name": relationship.name,
                    "description": relationship.description,
                    "image_urls": relationship.image_urls,
                    "bi_directional": True,
                    "destiny_entity_id": source_id,
                    "auto_generatable": relationship.auto_generatable,
                    "author_type": relationship.author_type,
                    "user_id": relationship.user_id,
                    "agent_id": relationship.agent_id,
                }
                await self.repository.add_relationship(
                    ontology_id, destiny_id, mirror_data
                )
            else:
                mirror.name = relationship.name
                mirror.description = relationship.description
                mirror.image_urls = relationship.image_urls
                mirror.bi_directional = True
                mirror.destiny_entity_id = source_id
                mirror.auto_generatable = relationship.auto_generatable
                mirror.author_type = relationship.author_type
                mirror.user_id = relationship.user_id
                mirror.agent_id = relationship.agent_id
                await self.repository.save(mirror)
        else:
            if mirror is not None:
                await self.repository.remove_relationship(mirror)

    async def _remove_mirror_relationship(
        self, ontology_id: int, relationship: OntologyRelationship
    ) -> None:
        destiny_id = relationship.destiny_entity_id
        if destiny_id is None:
            return
        mirror = await self.repository.find_relationship_between(
            ontology_id, destiny_id, relationship.entity_id
        )
        if mirror is not None:
            await self.repository.remove_relationship(mirror)
