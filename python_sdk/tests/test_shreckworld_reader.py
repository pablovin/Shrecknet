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


def test_shreckworld_query_supports_multi_source_and_grounded_answer_mode():
    async def scenario():
        async with Shrecknet(token='token') as sdk:
            async def handler(request):
                import json
                payload = json.loads(request.content)
                assert payload['library_item_ids'] == ['book-a', 'book-b']
                assert payload['mode'] == 'answer'
                return httpx.Response(200, json={
                    'shreckworld_id': 'world',
                    'query': 'Explain',
                    'answer': 'The source establishes this. [source-1]',
                    'citations': [],
                    'mode': 'answer',
                    'answer_status': 'complete',
                    'claims': [{
                        'text': 'The source establishes this.',
                        'citation_ids': ['source-1'],
                        'kind': 'fact',
                    }],
                    'missing_information': [],
                    'conflicts': [],
                    'source_coverage': {
                        'requested_source_count': 2,
                        'searched_source_count': 2,
                        'sources_without_ready_index': 0,
                        'sources_requiring_reindex': 0,
                    },
                })
            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(base_url='http://world.test', transport=httpx.MockTransport(handler))
            answer = await sdk.shreckworld.query(
                'world', 'Explain', library_item_ids=['book-a', 'book-b'], mode='answer',
            )
            assert answer.answer_status == 'complete'
            assert answer.claims[0].citation_ids == ['source-1']
            assert answer.source_coverage is not None
            assert answer.source_coverage.searched_source_count == 2
            with pytest.raises(ValueError, match='either library_item_id'):
                await sdk.shreckworld.query(
                    'world', 'Explain', library_item_id='book-a', library_item_ids=['book-b'],
                )
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
