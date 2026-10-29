"""Rename a Folio page using an existing administrator account."""
import asyncio
import os

from shrecknet_client import Shrecknet
from shrecknet_client.atlas import AtlasFolioPatch, AtlasRelationshipChanges


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
        # Optional: remove one explicit outgoing edge; every other edge is preserved.
        remove_id = os.getenv("SHRECKNET_FOLIO_REMOVE_EDGE_ID")
        if remove_id:
            if updated.folio_projection.relationship_editing != "delta-v1":
                raise RuntimeError("Deploy the delta-v1 Folio API first")
            await sdk.ontologies.patch_folio(world_id, page_id, AtlasFolioPatch(
                expected_updated_at=updated.updated_at,
                entity_instance_id=updated.entities[0].entity_instance_id,
                relationship_changes=AtlasRelationshipChanges(remove=[remove_id]),
            ))


if __name__ == "__main__":
    asyncio.run(main())
