import asyncio
import os
import uuid

from shrecknet_client import Shrecknet
from _bootstrap import ensure_user_and_login
from shrecknet_client.models import OntologyInstanceCreate, OntologyInstanceEntity



# Step 1: connect to Shrecknet URLs from environment.
# Step 2: ensure user bootstrap/login (first user becomes admin).
# Step 3: execute domain workflow actions and print results.

async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    
    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        suffix = uuid.uuid4().hex[:8]
        ontology = await sdk.ontologies.create(name=f"sdk-demo-{suffix}", description="SDK demo ontology")
        print("created_ontology:", ontology.id)

        instance = await sdk.ontology_instances.create(
            OntologyInstanceCreate(
                ontology_id=ontology.id,
                name=f"instance-{suffix}",
                entities=[
                    OntologyInstanceEntity(
                        definition_id=1,
                        alias=f"hero-{suffix}",
                        text="A protagonist",
                        author_type="human",
                        author_id="sdk",
                    )
                ],
            )
        )
        print("created_instance:", instance.id)

        from shrecknet_client.models import OntologyInstanceUpdate

        updated = await sdk.ontology_instances.update(instance.id, OntologyInstanceUpdate(name=f"instance-{suffix}-updated"))
        print("updated_instance_name:", updated.name)

        await sdk.ontology_instances.delete(instance.id)
        await sdk.ontologies.delete(ontology.id)
        print("cleanup_done")


if __name__ == "__main__":
    asyncio.run(main())
