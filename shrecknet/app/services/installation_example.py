"""Small, explicitly labelled example using the established Atlas authoring service."""
from uuid import NAMESPACE_URL, uuid5
from app.schemas.ontology_instance import OntologyInstanceCreate, OntologyInstanceEntityCreate, SceneCreate, SceneDerivedFrom, SceneEntityRelation, MilestoneCreate, MilestoneDerivedFrom
from app.models.ontology import AuthorType

TITLE = "ShreckOS starter example: The Lantern Bridge"
STORY = "At sunset, Mira the bridge keeper found a copper key beneath the lantern on Lantern Bridge. She placed the key in a blue box inside the watch house. The key opens the old observatory. This is optional starter content supplied by ShreckOS."

async def ensure_example(world_id: int, instance_service, definitions) -> tuple[str, str]:
    matches = await instance_service.list_instances(ontology_id=world_id, search=TITLE, limit=10)
    page = next((item for item in matches if item.name == TITLE), None)
    by_name = {item.name: item.id for item in definitions}
    if page is None:
        page = await instance_service.create_instance(OntologyInstanceCreate(
            ontology_id=world_id, name=TITLE,
            entities=[OntologyInstanceEntityCreate(definition_id=by_name[kind], alias=name, text=text, author_type=AuthorType.AGENT, author_id="shreckos-installation") for kind, name, text in [
                ("Story", "The Lantern Bridge", STORY),
                ("NPCs", "Mira", "Mira keeps Lantern Bridge. She found a copper key and stored it in a blue box inside the watch house."),
                ("Places", "Lantern Bridge", "Lantern Bridge connects the village to the old observatory. A watch house stands beside its lantern."),
            ]],
        ), trigger_background_jobs=False)
    source = next(entity for entity in page.entities if entity.alias == "The Lantern Bridge")
    scene_id = str(uuid5(NAMESPACE_URL, f"shreckos-installation:{world_id}:lantern-bridge"))
    scenes = await instance_service.list_scenes(page.instance_id)
    if not any(scene.id == scene_id for scene in scenes):
        await instance_service.create_scene(page.instance_id, SceneCreate(
            id=scene_id, name="Starter example: A key at sunset", description=STORY,
            created_by_type="agent", created_by_author="shreckos-installation",
            derived_from=SceneDerivedFrom(entity_instance_id=source.entity_instance_id),
            relates_to=[SceneEntityRelation(entity_instance_id=entity.entity_instance_id, label="features") for entity in page.entities if entity.entity_instance_id != source.entity_instance_id],
            milestones=[MilestoneCreate(name="Mira stores the key", description="Mira places the copper key in a blue box inside the watch house.", created_by_type="agent", created_by_author="shreckos-installation", derived_from=MilestoneDerivedFrom(entity_instance_id=source.entity_instance_id))],
        ), trigger_background_jobs=False)
    return page.instance_id, scene_id
