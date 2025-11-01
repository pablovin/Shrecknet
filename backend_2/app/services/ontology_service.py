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
from app.schemas.ontology import (
    OntologyCopyEntityResult,
    OntologyCopyResponse,
)


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
            skip=skip,
            limit=limit,
            name=name,
            description=description,
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

    async def list_entities(
        self, ontology_id: int, *, display_on_world: bool | None = None
    ) -> Sequence[OntologyEntity]:
        return await self.repository.list_entities(
            ontology_id, display_on_world=display_on_world
        )

    async def get_entity(
        self, ontology_id: int, entity_id: int
    ) -> OntologyEntity | None:
        return await self.repository.get_entity(ontology_id, entity_id)

    async def get_entity_by_id(self, entity_id: int) -> OntologyEntity | None:
        return await self.repository.get_entity_by_id(entity_id)

    async def update_entity(self, entity: OntologyEntity, data: dict) -> OntologyEntity:
        self._ensure_author_defaults(
            data,
            existing_user_id=entity.user_id,
            existing_agent_id=entity.agent_id,
        )
        self._validate_author_payload(data, allow_missing=True)
        updated = await self.repository.update_entity(entity, data)
        await self.session.commit()
        await self.session.refresh(updated)
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

    async def get_property_by_id(self, property_id: int) -> OntologyProperty | None:
        return await self.repository.get_property_by_id(property_id)

    async def update_property(
        self, prop: OntologyProperty, data: dict
    ) -> OntologyProperty:
        self._ensure_author_defaults(
            data,
            existing_user_id=prop.user_id,
            existing_agent_id=prop.agent_id,
        )
        self._validate_author_payload(data, allow_missing=True)
        updated = await self.repository.update_property(prop, data)
        await self.session.commit()
        await self.session.refresh(updated)
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

    async def get_relationship_by_id(
        self, relationship_id: int
    ) -> OntologyRelationship | None:
        return await self.repository.get_relationship_by_id(relationship_id)

    async def update_relationship(
        self,
        relationship: OntologyRelationship,
        data: dict,
    ) -> OntologyRelationship:
        self._ensure_author_defaults(
            data,
            existing_user_id=relationship.user_id,
            existing_agent_id=relationship.agent_id,
        )
        self._validate_author_payload(data, allow_missing=True)
        ontology_id = relationship.entity.ontology_id
        entity_id = relationship.entity_id
        await self._validate_relationship_entities(
            ontology_id, entity_id, data, allow_missing=True
        )
        updated = await self.repository.update_relationship(relationship, data)
        await self._sync_bidirectional_relationship(ontology_id, updated)
        await self.session.commit()
        await self.session.refresh(updated)
        return updated

    async def delete_relationship(self, relationship: OntologyRelationship) -> None:
        ontology_id = relationship.entity.ontology_id
        await self._remove_mirror_relationship(ontology_id, relationship)
        await self.repository.remove_relationship(relationship)
        await self.session.commit()

    # Copy definitions -------------------------------------------------
    async def copy_definitions(
        self, source_ontology_id: int, target_ontology_id: int
    ) -> OntologyCopyResponse:
        if source_ontology_id == target_ontology_id:
            raise ValueError("Source and target ontology must be different")

        source = await self.repository.get(source_ontology_id)
        if not source:
            raise ValueError("Source ontology not found")

        target = await self.repository.get(target_ontology_id)
        if not target:
            raise ValueError("Target ontology not found")

        def _normalize(name: str) -> str:
            return name.strip().lower()

        source_entities = await self.repository.list_entities(source_ontology_id)
        target_entities = await self.repository.list_entities(target_ontology_id)

        target_by_name = {
            _normalize(entity.name): entity for entity in target_entities
        }

        entity_map: dict[int, OntologyEntity] = {}
        copied_records: dict[int, dict[str, list[str]]] = {}
        existing_entities: list[str] = []
        new_entity_pairs: list[tuple[OntologyEntity, OntologyEntity]] = []

        for source_entity in source_entities:
            norm_name = _normalize(source_entity.name)
            if norm_name in target_by_name:
                existing_entity = target_by_name[norm_name]
                entity_map[source_entity.id] = existing_entity
                existing_entities.append(source_entity.name)
                continue

            new_entity = OntologyEntity(
                ontology_id=target_ontology_id,
                name=source_entity.name,
                description=source_entity.description,
                image_url=source_entity.image_url,
                keywords=list(source_entity.keywords or []),
                display_on_world=source_entity.display_on_world,
                auto_generatable=source_entity.auto_generatable,
                author_type=source_entity.author_type,
                user_id=source_entity.user_id,
                agent_id=source_entity.agent_id,
            )
            self.session.add(new_entity)
            await self.session.flush()

            entity_map[source_entity.id] = new_entity
            target_by_name[norm_name] = new_entity
            new_entity_pairs.append((source_entity, new_entity))
            copied_records[new_entity.id] = {
                "name": source_entity.name,
                "properties": [],
                "relationships": [],
                "skipped_relationships": [],
            }

        for source_entity, target_entity in new_entity_pairs:
            record = copied_records[target_entity.id]
            for prop in source_entity.properties or []:
                new_prop = OntologyProperty(
                    entity_id=target_entity.id,
                    name=prop.name,
                    description=prop.description,
                    image_url=prop.image_url,
                    cardinality=prop.cardinality,
                    data_type=prop.data_type,
                    auto_generatable=prop.auto_generatable,
                    author_type=prop.author_type,
                    user_id=prop.user_id,
                    agent_id=prop.agent_id,
                )
                self.session.add(new_prop)
                record["properties"].append(prop.name)

        for source_entity, target_entity in new_entity_pairs:
            record = copied_records[target_entity.id]
            for rel in source_entity.relationships or []:
                destiny_entity = None
                if rel.destiny_entity_id is not None:
                    destiny_entity = entity_map.get(rel.destiny_entity_id)
                    if destiny_entity is None:
                        record["skipped_relationships"].append(rel.name)
                        continue

                new_rel = OntologyRelationship(
                    entity_id=target_entity.id,
                    destiny_entity_id=destiny_entity.id if destiny_entity else None,
                    name=rel.name,
                    description=rel.description,
                    image_urls=list(rel.image_urls or []),
                    bi_directional=rel.bi_directional,
                    auto_generatable=rel.auto_generatable,
                    author_type=rel.author_type,
                    user_id=rel.user_id,
                    agent_id=rel.agent_id,
                )
                self.session.add(new_rel)
                record["relationships"].append(rel.name)

        await self.session.commit()

        copied_entities = [
            OntologyCopyEntityResult(
                name=data["name"],
                properties=data["properties"],
                relationships=data["relationships"],
                skipped_relationships=data["skipped_relationships"],
            )
            for data in copied_records.values()
        ]

        return OntologyCopyResponse(
            copied_entities=copied_entities, existing_entities=existing_entities
        )

    # Helpers -----------------------------------------------------------
    def _ensure_author_defaults(
        self,
        payload: dict,
        *,
        existing_user_id: str | None,
        existing_agent_id: str | None,
    ) -> None:
        if "author_type" not in payload:
            return

        raw_author_type = payload["author_type"]
        try:
            author_type = (
                raw_author_type
                if isinstance(raw_author_type, AuthorType)
                else AuthorType(raw_author_type)
            )
        except ValueError:
            # Let validation handle invalid values later
            return

        if author_type == AuthorType.HUMAN and "user_id" not in payload:
            payload["user_id"] = existing_user_id
        elif author_type == AuthorType.AGENT and "agent_id" not in payload:
            payload["agent_id"] = existing_agent_id

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
