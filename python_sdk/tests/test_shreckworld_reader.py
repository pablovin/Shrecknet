import asyncio
import httpx
import pytest
from shrecknet_client import Shrecknet, SelectedPassage


def test_shreckworld_reads_current_identity_and_encodes_world_scope():
    async def scenario():
        async with Shrecknet(token='first-token') as sdk:
            async def handler(request):
                assert request.headers['Authorization'] == 'Bearer second-token'
                assert '/shreckworlds/world%2Fa/query' in str(request.url)
                import json
                payload = json.loads(request.content)
                assert payload['library_item_id'] == 'book'
                assert payload['selection']['page_number'] == 2
                return httpx.Response(200, json={'shreckworld_id':'world/a', 'query':'Explain', 'answer':'Evidence', 'citations':[]})
            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(base_url='http://world.test', transport=httpx.MockTransport(handler))
            sdk.set_token('second-token')
            answer = await sdk.shreckworld.query('world/a', 'Explain', library_item_id='book', selection=SelectedPassage(library_item_id='book', source_sha256='a'*64, page_number=2, text='Excerpt'))
            assert answer.answer == 'Evidence'
    asyncio.run(scenario())


def test_rule_review_retains_ambiguity_and_missing_facts():
    async def scenario():
        async with Shrecknet(token='token') as sdk:
            async def handler(request):
                assert request.url.path.endswith('/resolve-action')
                return httpx.Response(200, json={'status':'ambiguous', 'policy_revision':'source-priority-v1', 'explanation':'Confirm edition', 'citations':[], 'missing_facts':['Edition']})
            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(base_url='http://world.test', transport=httpx.MockTransport(handler))
            review = await sdk.shreckworld.resolve_action('world', 'Attack')
            assert review.status == 'ambiguous'
            assert review.missing_facts == ['Edition']
    asyncio.run(scenario())
