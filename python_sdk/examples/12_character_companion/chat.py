"""Run with SHRECKNET_URL, SHRECKNET_TOKEN and CHARACTER_AGENT_ID configured."""
import asyncio
import os
from uuid import uuid4
from shrecknet_client import Shrecknet


async def main():
    async with Shrecknet(os.environ['SHRECKNET_URL'], token=os.environ['SHRECKNET_TOKEN']) as sdk:
        api = sdk.character_companion
        current = await api.selection()
        selection = await api.select(os.environ['CHARACTER_AGENT_ID'],current.version)
        chat = await api.create_chat(selection.character_agent_id,selection.version,idempotency_key=str(uuid4()))
        turn = await api.send(chat['id'],'Hello! How are you?',selection.version,idempotency_key=str(uuid4()))
        async for name, event in api.events(turn.turn_id):
            if name == 'response.delta':
                print(event.payload['delta'],end='',flush=True)
            elif name == 'response.final':
                print('\nValidated:',event.payload['text'])
        print(await api.turn(turn.turn_id))


if __name__ == '__main__':
    asyncio.run(main())
