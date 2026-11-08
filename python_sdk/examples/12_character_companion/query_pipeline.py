"""Use a pre-existing chat with evidence v2; no model configuration is changed.

Required: SHRECKNET_URL, SHRECKNET_TOKEN, CHAT_ID, QUESTION.
Optional ONTOLOGY_ID supplies explicit scope; USE_PAGE_SCOPE=1 releases an override.
GENERAL_KNOWLEDGE_OPT_IN=1 requests labeled fallback if the administrator permits.
"""
import asyncio
import os
from uuid import uuid4
from shrecknet_client import Shrecknet


async def main():
    async with Shrecknet(os.environ['SHRECKNET_URL'], token=os.environ['SHRECKNET_TOKEN']) as sdk:
        api = sdk.character_companion
        selection = await api.selection()
        chat = await api.chat(os.environ['CHAT_ID'])
        context = {'surface':'global'}
        if os.environ.get('ONTOLOGY_ID'):
            context['ontology_id'] = int(os.environ['ONTOLOGY_ID'])
        pending = chat.get('pending_clarification')
        turn = await api.send(chat['id'], os.environ['QUESTION'], selection.version,
            idempotency_key=str(uuid4()), context=context,
            clarification_id=pending['id'] if pending else None,
            use_page_scope=os.environ.get('USE_PAGE_SCOPE') == '1',
            general_knowledge_opt_in=os.environ.get('GENERAL_KNOWLEDGE_OPT_IN') == '1')
        async for name, event in api.events(turn.turn_id):
            if name == 'message.committed':
                print(event.payload['content'], flush=True)
            elif name == 'pipeline.status':
                print('Status:', event.payload['stage'], flush=True)
            elif name == 'response.final':
                print(event.payload['text'])


if __name__ == '__main__':
    asyncio.run(main())
