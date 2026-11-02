from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.core import config_store
from app.core.config_store import update_settings
from app.services.media_service import MediaService
from app.services.setup_service import SetupService


class _DummyOntologyRepository:
    async def get_by_name(self, _name: str):
        return None


class _DummyOntologyService:
    def __init__(self) -> None:
        self.repository = _DummyOntologyRepository()


def _reset_settings_cache() -> None:
    config_store._settings_cache = None


def test_resolve_default_images_root_supports_package_local_layout() -> None:
    service = SetupService(
        ontology_service=_DummyOntologyService(),
        media_service=None,
    )

    expected = (
        Path(__file__).resolve().parents[1] / "default" / "images" / "world"
    )
    assert service.default_images_root == expected


@pytest.mark.asyncio
async def test_copy_default_world_images_writes_expected_media_paths(
    monkeypatch, tmp_path
) -> None:
    media_root = tmp_path / "media"
    source_root = tmp_path / "defaults"
    source_root.mkdir()

    def write_png(path: Path, size: tuple[int, int]) -> None:
        image = Image.new("RGB", size, color="red")
        image.save(path, format="PNG")

    write_png(source_root / "World.png", (2500, 1250))
    write_png(source_root / "Adventure.png", (2500, 1250))

    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHRECKNET_MEDIA_BASE_URL", "/media")
    monkeypatch.delenv("SHRECKNET_MEDIA_PUBLIC_URL", raising=False)
    _reset_settings_cache()
    update_settings(
        {
            "media_root": str(media_root),
            "media_base_url": "/media",
            "media_public_url": None,
        }
    )

    service = SetupService(
        ontology_service=_DummyOntologyService(),
        media_service=MediaService(base_path=media_root),
        default_images_root=source_root,
    )

    ontology_url = await service._copy_ontology_image(42)
    entity_url = await service._copy_entity_image(7, "Adventure.png")

    assert ontology_url == "/media/ontology/42/file.png"
    assert entity_url == "/media/entity/7/file.png"
    with Image.open(media_root / "ontology" / "42" / "file.png") as image:
        assert image.size == (640, 320)
    with Image.open(media_root / "entity" / "7" / "file.png") as image:
        assert image.size == (640, 320)

    _reset_settings_cache()


@pytest.mark.asyncio
async def test_first_world_persists_properties_relationships_and_resumes(session_maker, monkeypatch, tmp_path):
    from app.models.ontology import Cardinality, PropertyDataType
    from app.services.ontology_service import OntologyService

    monkeypatch.setattr(OntologyService, '_enqueue_definition_embedding', lambda *args: None)
    async with session_maker() as session:
        ontologies = OntologyService(session)
        setup = SetupService(ontology_service=ontologies, media_service=None, default_images_root=tmp_path)
        first = await setup.resume_default_world('first_world')
        assert first.name == 'First World'
        entities = {entity.name: entity for entity in await ontologies.repository.list_entities(first.ontology_id)}
        assert set(entities) == {'Story', 'Character', 'Important Place', 'Important Item', 'Creature'}
        assert not entities['Story'].auto_generatable
        assert all(entity.auto_generatable for name, entity in entities.items() if name != 'Story')
        property_ids = set()
        relationships = set()
        for kind, entity in entities.items():
            properties = await ontologies.list_properties(first.ontology_id, entity.id)
            assert properties
            assert all(prop.auto_generatable == (kind != 'Story') for prop in properties)
            assert all(prop.data_type == PropertyDataType.TEXT for prop in properties)
            property_ids.update(prop.id for prop in properties)
            if kind == 'Character':
                assert next(prop for prop in properties if prop.name == 'aliases').cardinality == Cardinality.MANY
            for relation in await ontologies.repository.list_relationships(first.ontology_id, entity.id):
                assert relation.destiny_entity_id in {item.id for item in entities.values()}
                assert relation.auto_generatable
                relationships.add(relation.id)
        assert len(relationships) == 11
        assert len(property_ids) == 18
        again = await setup.resume_default_world('first_world')
        assert again.ontology_id == first.ontology_id
        assert {entity.id for entity in await ontologies.repository.list_entities(first.ontology_id)} == {entity.id for entity in entities.values()}
        assert {prop.id for entity in entities.values() for prop in await ontologies.list_properties(first.ontology_id, entity.id)} == property_ids
        assert {rel.id for entity in entities.values() for rel in await ontologies.repository.list_relationships(first.ontology_id, entity.id)} == relationships
        assert await setup._create_world('first_world') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('bidirectional', [False, True])
async def test_same_type_relationship_survives_creation_update_and_deletion(session_maker, monkeypatch, bidirectional):
    from app.models.ontology import AuthorType
    from app.services.ontology_service import OntologyService

    monkeypatch.setattr(OntologyService, '_enqueue_definition_embedding', lambda *args: None)
    async with session_maker() as session:
        service = OntologyService(session)
        world = await service.create_ontology({'name': 'self-link regression'})
        author = {'author_type': AuthorType.AGENT, 'agent_id': 'system'}
        entity = await service.add_entity(world.id, {'name': 'Place', **author})
        relation = await service.add_relationship(world.id, entity.id, {
            'name': 'located in', 'destiny_entity_id': entity.id,
            'bi_directional': bidirectional, **author,
        })
        assert [rel.id for rel in await service.list_relationships(world.id, entity.id)] == [relation.id]
        updated = await service.update_relationship(relation, {'bi_directional': not bidirectional})
        assert updated.id == relation.id
        assert updated.bi_directional == (not bidirectional)
        await service.delete_relationship(updated)
        assert await service.list_relationships(world.id, entity.id) == []
