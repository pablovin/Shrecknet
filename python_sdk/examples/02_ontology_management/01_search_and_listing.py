import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Explore ontology instances with listing and text search.
# Expected result:
# - Prints instance count, summary page samples, and search hit distribution.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    ontology_id = int(os.getenv("SHRECKNET_ONTOLOGY_ID", "1"))

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        # Count instances for ontology scope.
        count = await sdk.ontology_instances.count(ontology_id=ontology_id)
        print("instance_count:", count.total)

        # Retrieve compact summary page for browsing UIs.
        basic = await sdk.ontology_instances.basic(ontology_id=ontology_id, limit=20)
        print("basic_total:", basic.total)
        print("basic_sample:", basic.items[:3])

        # Run scoped search across entities/scenes/milestones.
        search = await sdk.ontology_instances.search(query="hero", ontology_id=ontology_id)
        print("search_entities:", len(search.entities))
        print("search_scenes:", len(search.scenes))
        print("search_milestones:", len(search.milestones))


if __name__ == "__main__":
    asyncio.run(main())
