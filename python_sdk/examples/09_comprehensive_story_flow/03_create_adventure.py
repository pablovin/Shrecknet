import asyncio
import os

from shrecknet_client import OntologyInstanceCreate, OntologyInstanceEntity, Shrecknet
from python_sdk.examples.09_comprehensive_story_flow._bootstrap import ensure_user_and_login, load_state, save_state


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await ensure_user_and_login(sdk)
        state = load_state()
        ontology_id = int(state["ontology_id"])

        adventure = await sdk.ontology_instances.create(
            OntologyInstanceCreate(
                ontology_id=ontology_id,
                name="The Ashen Gate",
                entities=[
                    OntologyInstanceEntity(
                        definition_id=1,
                        alias="The Ashen Gate",
                        text="An expedition to seal a fractured planar gate.",
                        author_type="human",
                        author_id="sdk",
                    )
                ],
            )
        )

        state["adventure_instance_id"] = adventure.id
        save_state(state)
        print("adventure_instance_id:", adventure.id)


if __name__ == "__main__":
    asyncio.run(main())
