"""Inspect an existing reply with SHRECKNET_URL, SHRECKNET_TOKEN and TURN_ID."""
import asyncio
import os
from shrecknet_client import Shrecknet


async def main():
    async with Shrecknet(os.environ['SHRECKNET_URL'], token=os.environ['SHRECKNET_TOKEN']) as sdk:
        turn = await sdk.character_companion.turn(os.environ['TURN_ID'])
        result = getattr(turn, 'result', None) or {}
        if 'tool_calls' not in result:
            print('No answer breakdown available for this turn.')
            return
        evidence = {row['evidence_id']: row for row in result['evidence']}
        for call in result['tool_calls']:
            print(call['tool'], call['status'], 'cached:', call['cache_hit'])
            for identifier in call['evidence_ids']:
                print(identifier, evidence[identifier]['excerpt'], evidence[identifier]['reference'])
        for segment in result['segments']:
            print(segment['id'], segment['text'], 'sources:', segment['evidence_ids'])


if __name__ == '__main__':
    asyncio.run(main())
