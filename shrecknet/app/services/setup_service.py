from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path

from fastapi import UploadFile

from app.models.ontology import AuthorType
from app.schemas.setup import (
    DefaultWorldEntityResult,
    DefaultWorldRelationshipResult,
    DefaultWorldResult,
    DefaultWorldsResponse,
)
from app.services.media_service import MediaService
from app.services.ontology_service import OntologyService


@dataclass(frozen=True)
class _WorldEntitySpec:
    name: str
    description: str
    auto_generatable: bool
    display_on_world: bool
    image_filename: str


@dataclass(frozen=True)
class _WorldRelationshipSpec:
    name: str
    description: str
    source_entity: str
    destiny_entity: str
    bi_directional: bool = False


class SetupService:
    def __init__(
        self,
        *,
        ontology_service: OntologyService,
        media_service: MediaService,
        default_images_root: Path | None = None,
    ) -> None:
        self.ontology_service = ontology_service
        self.media_service = media_service
        self.default_images_root = default_images_root or self._resolve_default_images_root()

    async def create_default_worlds(self, worlds: list[str]) -> DefaultWorldsResponse:
        response = DefaultWorldsResponse()
        normalized = [w.strip().lower() for w in worlds if w and w.strip()]
        for world in normalized:
            if world not in {"fantasy", "horror", "scifi"}:
                response.skipped.append(world)
                continue

            created = await self._create_world(world)
            if created is None:
                response.skipped.append(world)
                continue
            response.created.append(created)
        return response

    async def resume_default_world(self, world: str) -> DefaultWorldResult:
        if world not in {"fantasy", "horror", "scifi"}:
            raise ValueError("Unknown starter template")
        result = await self._create_world(world, resume=True)
        assert result is not None
        return result

    async def _create_world(self, world: str, *, resume: bool = False) -> DefaultWorldResult | None:
        existing = await self.ontology_service.repository.get_by_name(world)
        if existing and not resume:
            return None

        ontology = existing or await self.ontology_service.create_ontology(
            {
                "name": world,
                "description": self._world_description(world),
            }
        )
        ontology_image_url = await self._copy_ontology_image(ontology.id)
        if ontology_image_url:
            ontology = await self.ontology_service.update_ontology(
                ontology, {"image_url": ontology_image_url}
            )

        entities_spec = self._entity_specs()
        entity_results: list[DefaultWorldEntityResult] = []
        entity_id_by_name: dict[str, int] = {}

        existing_entities = {entity.name: entity for entity in await self.ontology_service.repository.list_entities(ontology.id)} if resume else {}
        for spec in entities_spec:
            entity = existing_entities.get(spec.name) or await self.ontology_service.add_entity(
                ontology.id,
                {
                    "name": spec.name,
                    "description": spec.description,
                    "display_on_world": spec.display_on_world,
                    "auto_generatable": spec.auto_generatable,
                    "author_type": AuthorType.AGENT,
                    "agent_id": "system",
                },
            )

            image_url = await self._copy_entity_image(entity.id, spec.image_filename)
            if image_url:
                entity = await self.ontology_service.update_entity(
                    entity, {"image_url": image_url}
                )

            entity_results.append(
                DefaultWorldEntityResult(
                    id=entity.id, name=entity.name, image_url=entity.image_url
                )
            )
            entity_id_by_name[spec.name] = entity.id

        relationships_spec = self._relationship_specs()
        relationship_results: list[DefaultWorldRelationshipResult] = []
        for spec in relationships_spec:
            source_id = entity_id_by_name.get(spec.source_entity)
            destiny_id = entity_id_by_name.get(spec.destiny_entity)
            if not source_id or not destiny_id:
                continue

            existing_relationships = await self.ontology_service.repository.list_relationships(ontology.id, source_id) if resume else []
            relationship = next((rel for rel in existing_relationships if rel.name == spec.name and rel.destiny_entity_id == destiny_id), None) or await self.ontology_service.add_relationship(
                ontology.id,
                source_id,
                {
                    "name": spec.name,
                    "description": spec.description,
                    "destiny_entity_id": destiny_id,
                    "bi_directional": spec.bi_directional,
                    "auto_generatable": False,
                    "author_type": AuthorType.AGENT,
                    "agent_id": "system",
                },
            )
            relationship_results.append(
                DefaultWorldRelationshipResult(
                    id=relationship.id,
                    name=relationship.name,
                    source_entity_id=source_id,
                    destiny_entity_id=destiny_id,
                )
            )

        return DefaultWorldResult(
            ontology_id=ontology.id,
            name=ontology.name,
            entities=entity_results,
            relationships=relationship_results,
        )

    def _world_description(self, world: str) -> str:
        if world == "fantasy":
            return "A realm of magic, ancient ruins, and heroic quests."
        if world == "horror":
            return "A world of dread, mysteries, and unsettling truths."
        return "A world of advanced technology, distant stars, and bold exploration."

    def _resolve_default_images_root(self) -> Path:
        env_root = os.getenv("SHRECKNET_DEFAULT_IMAGES_ROOT")
        if env_root:
            return Path(env_root)
        docker_root = Path("/app/default/images/world")
        if docker_root.exists():
            return docker_root
        candidate_roots = [
            Path(__file__).resolve().parents[2] / "default" / "images" / "world",
            Path(__file__).resolve().parents[3] / "default" / "images" / "world",
            Path("shrecknet/default/images/world"),
            Path("default/images/world"),
        ]
        for candidate in candidate_roots:
            if candidate.exists():
                return candidate
        return candidate_roots[0]

    def _entity_specs(self) -> list[_WorldEntitySpec]:
        return [
            _WorldEntitySpec(
                name="Adventures",
                description="A curated set of adventures that define the campaign arcs.",
                auto_generatable=False,
                display_on_world=True,
                image_filename="Adventure.png",
            ),
            _WorldEntitySpec(
                name="Story",
                description="Narrative story arcs that unfold within each adventure.",
                auto_generatable=False,
                display_on_world=False,
                image_filename="Story.png",
            ),
            _WorldEntitySpec(
                name="NPCs",
                description="Key non-player characters that populate the world.",
                auto_generatable=True,
                display_on_world=True,
                image_filename="NPC.png",
            ),
            _WorldEntitySpec(
                name="Players",
                description="Player characters and their evolving identities.",
                auto_generatable=True,
                display_on_world=True,
                image_filename="Player.png",
            ),
            _WorldEntitySpec(
                name="Places",
                description="Important locations, landmarks, and points of interest.",
                auto_generatable=True,
                display_on_world=True,
                image_filename="Place.png",
            ),
        ]

    def _relationship_specs(self) -> list[_WorldRelationshipSpec]:
        return [
            _WorldRelationshipSpec(
                name="has story",
                description="Stories that belong to this adventure.",
                source_entity="Adventures",
                destiny_entity="Story",
                bi_directional=True,
            ),
        ]

    async def _copy_entity_image(self, entity_id: int, filename: str) -> str | None:
        source_path = self.default_images_root / filename
        if not source_path.exists():
            return None

        return await self._save_default_image(source_path, "entity", str(entity_id))

    async def _copy_ontology_image(self, ontology_id: int) -> str | None:
        source_path = self.default_images_root / "World.png"
        if not source_path.exists():
            return None

        return await self._save_default_image(source_path, "ontology", str(ontology_id))

    async def _save_default_image(
        self, source_path: Path, content_type: str, content_id: str
    ) -> str:
        upload = UploadFile(
            file=BytesIO(source_path.read_bytes()),
            filename=source_path.name,
        )
        return await self.media_service.save_content_image(
            upload,
            content_type=content_type,
            content_id=content_id,
            is_main=True,
        )
