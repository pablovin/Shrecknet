"""Read compact topology with an already authenticated Shrecknet client."""

from shrecknet_client import Shrecknet
from shrecknet_client.atlas import AtlasWorldWeb


async def read_world_web(client: Shrecknet, ontology_id: int) -> AtlasWorldWeb:
    web = await client.ontologies.world_web(ontology_id)
    print(f"{len(web.nodes)} nodes, {len(web.edges)} edges; truncated={web.truncated}")
    return web
