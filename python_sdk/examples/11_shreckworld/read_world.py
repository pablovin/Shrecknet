"""Browse/query a ShreckWorld with an existing Shrecknet session token."""
import argparse
import asyncio
import os
from shrecknet_client import Shrecknet


async def run(args):
    token = os.environ.get('SHRECKNET_TOKEN')
    if not token:
        raise SystemExit('Set SHRECKNET_TOKEN to a current Shrecknet bearer token.')
    async with Shrecknet(token=token, shreckworld_base_url=args.base_url) as sdk:
        worlds = await sdk.shreckworld.worlds()
        for world in worlds:
            print(world.id, world.name)
        if args.world_id:
            answer = await sdk.shreckworld.query(args.world_id, args.question)
            print(answer.answer)
            for citation in answer.citations:
                print(citation.title, 'page', citation.page_number, citation.excerpt)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8121')
    parser.add_argument('--world-id')
    parser.add_argument('--question', default='How does mounted combat work?')
    asyncio.run(run(parser.parse_args()))
