"""Create one admin-owned page; reuse the printed request ID after an uncertain result."""
import asyncio
import os
from uuid import uuid4

from shrecknet_client import Shrecknet
from shrecknet_client.atlas import AtlasFolioCreate


async def main() -> None:
    request_id = os.getenv('SHRECKNET_FOLIO_REQUEST_ID') or str(uuid4())
    print('Creation request:', request_id)
    async with Shrecknet(base_url=os.getenv('SHRECKNET_BASE_URL', 'http://localhost:8100')) as sdk:
        await sdk.login(username_or_email=os.environ['SHRECKNET_ADMIN_USERNAME'], password=os.environ['SHRECKNET_ADMIN_PASSWORD'])
        world_id = int(os.environ['SHRECKNET_ONTOLOGY_ID'])
        existing = await sdk.ontologies.find_folio_creation(world_id, request_id)
        page = existing or await sdk.ontologies.create_folio(world_id, AtlasFolioCreate(
            request_id=request_id, definition_id=int(os.environ['SHRECKNET_ENTITY_DEFINITION_ID']),
            name=os.environ['SHRECKNET_FOLIO_NEW_TITLE'],
        ))
        print(page.instance_id, page.name)


if __name__ == '__main__':
    asyncio.run(main())
