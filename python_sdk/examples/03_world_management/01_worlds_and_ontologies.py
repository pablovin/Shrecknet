import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Read world-level context and its ontology linkage.
# Expected result:
# - Prints all visible worlds, ontology count, and one ontology detail sample.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)

        # List all worlds the current user can access.
        worlds = await sdk.worlds.list()
        print("worlds:", [w.model_dump() for w in worlds])

        # List ontologies and fetch first item details for inspection.
        ontologies = await sdk.ontologies.list(limit=10)
        print("ontologies_count:", len(ontologies))
        if ontologies:
            fetched = await sdk.ontologies.get(ontologies[0].id)
            print("first_ontology:", fetched.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
