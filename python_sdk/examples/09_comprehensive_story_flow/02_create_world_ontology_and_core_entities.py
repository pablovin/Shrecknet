import asyncio
import os
import uuid

from shrecknet_client import OntologyInstanceCreate, OntologyInstanceEntity, Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login, load_state, save_state


CORE_TYPES = [
    "adventure",
    "story",
    "character",
    "important location",
    "important items",
    "npcs",
]


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        state = load_state()
        suffix = uuid.uuid4().hex[:8]
        ontology = await sdk.ontologies.create(
            name=f"sdk-comprehensive-{suffix}",
            description="Comprehensive story flow ontology",
        )

        core_ids = {}
        for i, name in enumerate(CORE_TYPES, start=1):
            inst = await sdk.ontology_instances.create(
                OntologyInstanceCreate(
                    ontology_id=ontology.id,
                    name=name,
                    entities=[
                        OntologyInstanceEntity(
                            definition_id=1,
                            alias=name,
                            text=f"Core type seed: {name}",
                            author_type="human",
                            author_id="sdk",
                        )
                    ],
                )
            )
            core_ids[name] = inst.id

        state["ontology_id"] = ontology.id
        state["core_type_instance_ids"] = core_ids
        save_state(state)
        print("ontology_id:", ontology.id)


if __name__ == "__main__":
    asyncio.run(main())
