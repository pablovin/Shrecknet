import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Resolve entity ids and aggregate scene counts for instance subsets.
# Expected result:
# - Prints selected instance ids, scene count map, and resolve summary.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    ontology_id = int(os.getenv("SHRECKNET_ONTOLOGY_ID", "1"))

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        # Take a small working set of instances.
        instances = await sdk.ontology_instances.list(ontology_id=ontology_id, limit=5)
        ids = [item.id for item in instances]
        print("instance_ids:", ids)

        # Get scene totals by instance id.
        counts = await sdk.ontology_instances.scene_counts(ids)
        print("scene_counts:", counts.counts)

        # Resolve sample entity_instance_ids from first instance (if present).
        if instances and instances[0].entities:
            candidate_ids = []
            for entity in instances[0].entities[:3]:
                if isinstance(entity, dict) and entity.get("entity_instance_id"):
                    candidate_ids.append(entity["entity_instance_id"])
            if candidate_ids:
                resolved = await sdk.ontology_instances.resolve_entities(
                    ontology_id=ontology_id,
                    entity_instance_ids=candidate_ids,
                )
                print("resolved_count:", len(resolved.results))
                print("missing:", resolved.missing_entity_instance_ids)


if __name__ == "__main__":
    asyncio.run(main())
