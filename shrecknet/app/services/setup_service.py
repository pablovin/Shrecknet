from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path

from fastapi import UploadFile

from app.models.ontology import AuthorType, Cardinality, PropertyDataType
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
            if world not in {"first_world", "fantasy", "horror", "scifi"}:
                response.skipped.append(world)
                continue

            created = await self._create_world(world)
            if created is None:
                response.skipped.append(world)
                continue
            response.created.append(created)
        return response

    async def resume_default_world(self, world: str) -> DefaultWorldResult:
        if world not in {"first_world", "fantasy", "horror", "scifi"}:
            raise ValueError("Unknown starter template")
        result = await self._create_world(world, resume=True)
        assert result is not None
        return result

    async def _create_world(self, world: str, *, resume: bool = False) -> DefaultWorldResult | None:
        name = "First World" if world == "first_world" else world
        existing = await self.ontology_service.repository.get_by_name(name)
        if existing and not resume:
            return None

        ontology = existing or await self.ontology_service.create_ontology(
            {
                "name": name,
                "description": self._world_description(world),
            }
        )
        ontology_image_url = await self._copy_ontology_image(ontology.id)
        if ontology_image_url:
            ontology = await self.ontology_service.update_ontology(
                ontology, {"image_url": ontology_image_url}
            )

        entities_spec = self._first_world_entity_specs() if world == "first_world" else self._entity_specs()
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

        if world == "first_world":
            await self._ensure_first_world_properties(ontology.id, entity_id_by_name)

        relationships_spec = self._first_world_relationship_specs() if world == "first_world" else self._relationship_specs()
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
                    "auto_generatable": world == "first_world",
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
        if world == "first_world":
            return "A story-grounded world of characters, significant places, named items, and creatures extracted from your original stories."
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

    def _first_world_entity_specs(self) -> list[_WorldEntitySpec]:
        return [
            _WorldEntitySpec("Story", "Original story source supplied by a person and stored verbatim in the graph. Never generate, rewrite, or invent source text with AI.", False, True, "Story.png"),
            _WorldEntitySpec("Character", "An identifiable person or person-like participant who appears in the source story and acts, speaks, or influences events. Extract only supported facts; do not invent characters or biographies.", True, True, "NPC.png"),
            _WorldEntitySpec("Important Place", "A location, landmark, city, region, or country explicitly mentioned in the story and essential to its events or stakes. Exclude incidental locations and passing geographic references.", True, True, "Place.png"),
            _WorldEntitySpec("Important Item", "An object with its own explicit proper name in the source story that is central to the plot, conflict, or a consequential action. Exclude unnamed objects, generic equipment, and incidental possessions.", True, True, "Item.png"),
            _WorldEntitySpec("Creature", "A narratively significant non-person creature, beast, monster, or creature kind explicitly mentioned in the source story. Exclude incidental wildlife; represent speaking person-like participants as Characters rather than duplicating them as Creatures.", True, True, "Creature.png"),
        ]

    async def _ensure_first_world_properties(self, ontology_id: int, entities: dict[str, int]) -> None:
        # Text/alias already store the source and canonical name; properties add structured facts.
        specs = {
            "Story": [("title", "Title supplied with the original story."), ("author", "Original author, when supplied."), ("source_reference", "Original document, URL, or citation supplied by the user.")],
            "Character": [("aliases", "Other names explicitly used in the source."), ("role", "Role in the events, supported by the source."), ("traits", "Explicitly described appearance or personality."), ("motivation", "Goals stated or directly supported by the story.")],
            "Important Place": [("aliases", "Other names explicitly used in the source."), ("place_type", "For example landmark, building, city, region, or country."), ("significance", "Why this location is essential to the story.")],
            "Important Item": [("aliases", "Other explicit names of this named object."), ("item_type", "Kind of object described by the story."), ("significance", "Why this named item is central to the story."), ("abilities", "Powers or functions explicitly described in the source.")],
            "Creature": [("aliases", "Other names explicitly used in the source."), ("creature_kind", "Species or kind, only when identified in the source."), ("significance", "Why this creature matters to the story."), ("abilities", "Abilities explicitly described in the source.")],
        }
        for kind, properties in specs.items():
            entity_id = entities[kind]
            existing = {prop.name for prop in await self.ontology_service.list_properties(ontology_id, entity_id)}
            for name, description in properties:
                if name not in existing:
                    await self.ontology_service.add_property(ontology_id, entity_id, {
                        "name": name, "description": description + " Leave unknown facts empty; never invent details.",
                        "cardinality": Cardinality.MANY if name in {"aliases", "traits", "abilities"} else Cardinality.ONE,
                        "data_type": PropertyDataType.TEXT, "auto_generatable": kind != "Story",
                        "author_type": AuthorType.AGENT, "agent_id": "system",
                    })

    def _first_world_relationship_specs(self) -> list[_WorldRelationshipSpec]:
        relations = [
            ("features character", "Story", "Character"),
            ("features important place", "Story", "Important Place"),
            ("features important item", "Story", "Important Item"),
            ("features creature", "Story", "Creature"),
            ("interacts with", "Character", "Character"),
            ("visits", "Character", "Important Place"),
            ("possesses", "Character", "Important Item"),
            ("encounters", "Character", "Creature"),
            ("located in", "Important Place", "Important Place"),
            ("located at", "Important Item", "Important Place"),
            ("inhabits", "Creature", "Important Place"),
        ]
        return [_WorldRelationshipSpec(
            name, f"{source} {name} {target}, only when explicitly supported by the original story. Do not infer unsupported links.",
            source, target, bi_directional=name == "interacts with",
        ) for name, source, target in relations]

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
