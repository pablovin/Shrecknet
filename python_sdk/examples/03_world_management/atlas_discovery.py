"""Read bounded source and search pages: set SHRECKNET_URL, SHRECKNET_TOKEN, WORLD_ID."""
import asyncio
import os
from shrecknet_client.client import AsyncShrecknetClient
from shrecknet_client.resources import OntologiesAPI


async def main():
    client = AsyncShrecknetClient(base_url=os.environ['SHRECKNET_URL'], token=os.environ['SHRECKNET_TOKEN'])
    try:
        atlas = OntologiesAPI(client)
        world_id = int(os.environ['WORLD_ID'])
        sources = await atlas.scene_sources(world_id)
        print(sources.model_dump_json(indent=2))
        scenes = await atlas.scenes(world_id, query='arrival')
        print(scenes.model_dump_json(indent=2))
    finally:
        await client.aclose()


if __name__ == '__main__':
    asyncio.run(main())
