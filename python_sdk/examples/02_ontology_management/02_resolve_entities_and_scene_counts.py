import asyncio
import os

from shrecknet_client import Shrecknet
try:
    from ._bootstrap import ensure_user_and_login
except ImportError:
    from _bootstrap import ensure_user_and_login



# Step 1: connect to Shrecknet URLs from environment.
# Step 2: ensure user bootstrap/login (first user becomes admin).
# Step 3: execute domain workflow actions and print results.

async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    ontology_id = int(os.getenv("SHRECKNET_ONTOLOGY_ID", "1"))

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        instances = await sdk.ontology_instances.list(ontology_id=ontology_id, limit=5)
        ids = [item.id for item in instances]
        print("instance_ids:", ids)

        counts = await sdk.ontology_instances.scene_counts(ids)
        print("scene_counts:", counts.counts)

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
