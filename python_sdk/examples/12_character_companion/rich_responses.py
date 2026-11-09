"""Print cited-source rich blocks from a saved turn; use environment-held credentials.

SHRECKNET_URL=http://localhost:8100 SHRECKNET_TOKEN=... COMPANION_TURN_ID=... \
    python examples/12_character_companion/rich_responses.py
The server must enable rich responses on evidence_v2 before creating the turn.
"""
import asyncio
import os
from shrecknet_client import Shrecknet


async def main():
    async with Shrecknet(base_url=os.getenv('SHRECKNET_URL','http://localhost:8100'),token=os.environ['SHRECKNET_TOKEN']) as sdk:
        turn=await sdk.character_companion.turn(os.environ['COMPANION_TURN_ID'])
        print((turn.result or {}).get('text','Reply not completed.'))
        if turn.rich_response:
            for block in turn.rich_response.blocks:
                print(block.model_dump_json())
        else:
            print('This reply has no supported rich-response envelope.')


if __name__=='__main__':asyncio.run(main())
