import asyncio
import os
import uuid
from importlib import import_module

from shrecknet_client import Shrecknet
from shrecknet_client.models import OntologyInstanceCreate, OntologyInstanceEntity, OntologyInstanceUpdate

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Show full create/update/delete lifecycle for ontology content.
# Expected result:
# - Creates ontology + instance, updates instance name, then cleans all artifacts.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        suffix = uuid.uuid4().hex[:8]

        # Create isolated ontology for this run.
        ontology = await sdk.ontologies.create(name=f"sdk-demo-{suffix}", description="SDK demo ontology")
        print("created_ontology:", ontology.id)

        # Create one ontology instance with a sample entity payload.
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

        # Update instance to prove mutation path.
        updated = await sdk.ontology_instances.update(instance.id, OntologyInstanceUpdate(name=f"instance-{suffix}-updated"))
        print("updated_instance_name:", updated.name)

        # Cleanup keeps this example idempotent for repeated runs.
        await sdk.ontology_instances.delete(instance.id)
        await sdk.ontologies.delete(ontology.id)
        print("cleanup_done")


if __name__ == "__main__":
    asyncio.run(main())
