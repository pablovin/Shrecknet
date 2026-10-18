import asyncio
import httpx
import pytest
from shrecknet_client import KnowledgeModelTarget, Shrecknet, SelectedPassage


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


def test_shreckworld_planned_strategy_and_agent_job_lifecycle():
    async def scenario():
        async with Shrecknet(token='token') as sdk:
            async def handler(request):
                if request.method == "POST" and request.url.path.endswith("/agent-jobs"):
                    assert "/shreckworlds/world%2Fa/agent-jobs" in str(request.url)
                    import json
                    payload = json.loads(request.content)
                    assert payload["retrieval_strategy"] == "planned"
                    assert payload["mode"] == "evidence"
                    return httpx.Response(202, json={
                        "id": "job/1", "shreckworld_id": "world/a", "status": "queued",
                        "stage": "queued", "created_at": "2026-10-18T12:00:00Z",
                    })
                assert request.method == "GET"
                assert "/shreckworlds/world%2Fa/agent-jobs/job%2F1" in str(request.url)
                return httpx.Response(200, json={
                    "id": "job/1", "shreckworld_id": "world/a", "status": "completed",
                    "stage": "completed", "created_at": "2026-10-18T12:00:00Z",
                    "completed_at": "2026-10-18T12:00:01Z",
                    "result": {
                        "shreckworld_id": "world/a", "query": "What is Sanity?",
                        "answer": "Evidence only.", "citations": [],
                        "source_coverage": {
                            "requested_source_count": 0, "searched_source_count": 0,
                            "sources_without_ready_index": 0, "sources_requiring_reindex": 0,
                        },
                        "retrieval_strategy": "planned",
                        "query_plan": {
                            "plan_version": "query-v3.1", "intent": "simple",
                            "requires_exhaustive_coverage": False, "needs": [{
                                "id": "need-1", "question": "What is Sanity?", "kind": "definition",
                                "required": True, "coverage_requirement": "Definition",
                                "priority": 1,
                            }],
                        },
                        "evidence_coverage": {
                            "status": "partial", "needs": [{
                                "need_id": "need-1", "status": "missing", "evidence_ids": [],
                                "supported_requirements": [], "missing_requirements": ["Definition"],
                                "assessment_method": "unverified", "explanation": "No evidence.",
                            }], "unresolved_questions": [],
                        },
                        "evidence_groups": [],
                    },
                })
            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(
                base_url="http://world.test", transport=httpx.MockTransport(handler),
            )
            queued = await sdk.shreckworld.start_agent_query("world/a", "What is Sanity?")
            assert queued.status == "queued"
            completed = await sdk.shreckworld.agent_job("world/a", "job/1")
            assert completed.result is not None
            assert completed.result.retrieval_strategy == "planned"
            async def query_handler(request):
                import json
                assert request.url.path.endswith("/query")
                assert json.loads(request.content)["retrieval_strategy"] == "planned"
                return httpx.Response(200, json=completed.result.model_dump(mode="json"))
            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(
                base_url="http://world.test", transport=httpx.MockTransport(query_handler),
            )
            answer = await sdk.shreckworld.query("world/a", "What is Sanity?", retrieval_strategy="planned")
            assert answer.retrieval_strategy == "planned"
            with pytest.raises(ValueError, match="mode='evidence'"):
                await sdk.shreckworld.query(
                    "world/a", "What is Sanity?", mode="answer", retrieval_strategy="planned",
                )
    asyncio.run(scenario())


def test_shreckworld_model_settings_read_and_update():
    async def scenario():
        async with Shrecknet(token="admin-token") as sdk:
            async def handler(request):
                assert request.url.path == "/admin/settings/model-targets/shreckworld_synthesizer"
                if request.method == "PUT":
                    import json
                    target = json.loads(request.content)["model_shreckworld_synthesizer"]
                    assert target["provider"] == "anthropic"
                    assert target["name"] == "claude-sonnet-4"
                else:
                    target = {
                        "provider": "openai", "name": "gpt-5-nano",
                        "reasoning": False, "reasoning_effort": None,
                    }
                return httpx.Response(200, json={"model_shreckworld_synthesizer": target})

            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(
                base_url="http://world.test", transport=httpx.MockTransport(handler),
            )
            before = await sdk.shreckworld.model_settings()
            assert before.model_shreckworld_synthesizer.name == "gpt-5-nano"
            after = await sdk.shreckworld.update_model_settings(
                KnowledgeModelTarget(provider="anthropic", name="claude-sonnet-4"),
            )
            assert after.model_shreckworld_synthesizer.provider == "anthropic"

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
