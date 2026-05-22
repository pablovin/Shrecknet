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

        count = await sdk.ontology_instances.count(ontology_id=ontology_id)
        print("instance_count:", count.total)

        basic = await sdk.ontology_instances.basic(ontology_id=ontology_id, limit=20)
        print("basic_total:", basic.total)
        print("basic_sample:", basic.items[:3])

        search = await sdk.ontology_instances.search(query="hero", ontology_id=ontology_id)
        print("search_entities:", len(search.entities))
        print("search_scenes:", len(search.scenes))
        print("search_milestones:", len(search.milestones))


if __name__ == "__main__":
    asyncio.run(main())
