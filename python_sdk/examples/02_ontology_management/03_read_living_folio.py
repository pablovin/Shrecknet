import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module(
    "python_sdk.examples.01_login_and_user_creation.00_user_registration"
).ensure_user_and_login


async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    ontology_id = int(os.getenv("SHRECKNET_ONTOLOGY_ID", "1"))
    page_id = os.getenv("SHRECKNET_FOLIO_PAGE_ID", "page-id")

    async with Shrecknet(base_url=base_url) as sdk:
        await ensure_user_and_login(sdk)
        page = await sdk.ontologies.folio_detail(ontology_id, page_id)
        authors = await sdk.ontologies.folio_authors(ontology_id, page_id)

        print("page:", page.instance_id, page.name)
        print("folio projection:", page.folio_projection.model_dump())
        print("authors:", [author.display_name for author in authors])

        if page.entities:
            relationships = await sdk.ontologies.folio_relationships(
                ontology_id,
                page_id,
                entity_id=page.entities[0].entity_instance_id,
                limit=30,
            )
            print("relationship count:", relationships.total)


if __name__ == "__main__":
    asyncio.run(main())
