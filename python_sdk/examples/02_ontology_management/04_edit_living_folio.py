"""Rename a Folio page using an existing administrator account."""
import asyncio
import os

from shrecknet_client import Shrecknet
from shrecknet_client.atlas import AtlasFolioPatch


async def main() -> None:
    async with Shrecknet(base_url=os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")) as sdk:
        await sdk.login(username_or_email=os.environ["SHRECKNET_ADMIN_USERNAME"], password=os.environ["SHRECKNET_ADMIN_PASSWORD"])
        world_id = int(os.environ["SHRECKNET_ONTOLOGY_ID"])
        page_id = os.environ["SHRECKNET_FOLIO_PAGE_ID"]
        page = await sdk.ontologies.folio_detail(world_id, page_id)
        updated = await sdk.ontologies.patch_folio(world_id, page_id, AtlasFolioPatch(
            expected_updated_at=page.updated_at,
            entity_instance_id=page.entities[0].entity_instance_id,
            name=os.environ["SHRECKNET_FOLIO_NEW_TITLE"],
        ))
        print(updated.instance_id, updated.name, updated.updated_at)


if __name__ == "__main__":
    asyncio.run(main())
