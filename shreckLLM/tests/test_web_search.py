"""Shared search capability, budgets and citation contracts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.errors import ProviderBadRequestError
from app.openai_client import OpenAIClient
from app.schemas import ChatMessage, ChatRequest
from app.streaming import StreamChatRequest, stream_chat
from app.web_search import capability, validate_web_search, annotations
from app.service import ChatService


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,parameters,expected', [
    ('openrouter', ['tools'], True), ('openrouter', [], False),
    ('openrouter', None, None), ('ollama', ['tools'], False),
    ('openai', ['tools'], False), ('anthropic', ['tools'], False),
])
async def test_capability_and_admission(provider, parameters, expected):
    adapter = SimpleNamespace(provider_id=provider, model_parameters={'m': parameters}, list_models=AsyncMock())
    assert capability(adapter, 'm') is expected
    await validate_web_search(adapter, 'm', False)
    if expected is True:
        await validate_web_search(adapter, 'm', True)
    else:
        with pytest.raises(ProviderBadRequestError, match='web_search_unsupported'):
            await validate_web_search(adapter, 'm', True)


def test_bounded_tools_preserve_reasoning_and_routing():
    client = OpenAIClient(api_key='test', timeout_s=5, provider_id='openrouter')
    messages = [ChatMessage(role='user', content='current news')]
    args = client._chat_kwargs('m', messages, .7, reasoning=True, reasoning_effort='low', web_search_enabled=True)
    assert args['tools'][0]['type'] == 'openrouter:web_search'
    params = args['tools'][0]['parameters']
    assert params['engine'] == 'exa' and params['max_uses'] == 1 and params['max_total_results'] == 6
    assert args['extra_body']['max_tool_calls'] == 1
    assert args['extra_body']['reasoning'] == {'enabled': True, 'effort': 'low'}
    assert args['extra_body']['provider']['sort'] == 'latency'
    assert 'tools' not in client._chat_kwargs('m', messages, .7)


@pytest.mark.asyncio
async def test_discovery_and_catalog_expose_model_support():
    adapter = SimpleNamespace(provider_id='openrouter', model_parameters={})
    async def discover():
        adapter.model_parameters = {'yes': ['tools'], 'no': []}
        return ['yes', 'no']
    adapter.list_models = discover
    await validate_web_search(adapter, 'yes', True)
    service = object.__new__(ChatService)
    service.registry = SimpleNamespace(get=lambda _: adapter)
    service._runtime = SimpleNamespace(provider_defaults={'openrouter': SimpleNamespace(models=['yes'])})
    catalog = await service.provider_model_catalog('openrouter')
    assert catalog['model_capabilities'] == {'yes': {'web_search': True}, 'no': {'web_search': False}}


@pytest.mark.asyncio
async def test_stream_rejects_unsupported_before_execution():
    adapter = SimpleNamespace(provider_id='ollama', stream=AsyncMock())
    service = SimpleNamespace(registry=SimpleNamespace(get=lambda _: adapter),
                              _runtime=SimpleNamespace(provider_defaults={'ollama': {}}))
    request = StreamChatRequest(provider_id='ollama', model='m', web_search_enabled=True,
                                messages=[{'role': 'user', 'content': 'news'}])
    with pytest.raises(ProviderBadRequestError):
        await stream_chat(service, request).__anext__()
    adapter.stream.assert_not_called()


def test_only_bounded_provider_citations_survive():
    rows = annotations(SimpleNamespace(annotations=[{'type': 'other'}, {'type': 'url_citation', 'url_citation': {
        'url': 'https://example.org', 'title': 'Title', 'content': 'x' * 6000, 'secret': 'drop'}}]))
    assert len(rows) == 1 and len(rows[0]['url_citation']['content']) == 4000
    assert 'secret' not in rows[0]['url_citation']
    assert ChatRequest(provider_id='openrouter', model='m', messages=[{'role':'user','content':'hello'}]).web_search_enabled is False


@pytest.mark.asyncio
async def test_normal_request_forwards_flag_and_preserves_provider_annotations():
    service = object.__new__(ChatService)
    adapter = SimpleNamespace(provider_id='openrouter', model_parameters={'m': ['tools']},
        chat=AsyncMock(return_value={'text': 'answer', 'annotations': [], 'usage': {}}))
    service.registry = SimpleNamespace(get=lambda _: adapter)
    service._runtime = SimpleNamespace(provider_defaults={'openrouter': {}})
    service._provider_semaphores = {}
    service._log_backend_usage = lambda **_: None
    request = ChatRequest(provider_id='openrouter', model='m', web_search_enabled=True,
                          messages=[{'role':'user','content':'news'}])
    await service._run_chat(request, history=[])
    assert adapter.chat.call_args.kwargs['web_search_enabled'] is True
    adapter.provider_id = 'ollama'
    with pytest.raises(ProviderBadRequestError):
        await service._run_chat(request, history=[])
    assert adapter.chat.call_count == 1


@pytest.mark.asyncio
async def test_openai_catalog_retains_extra_supported_parameters():
    from openai.types import Model
    client = OpenAIClient(api_key='test', timeout_s=5, provider_id='openrouter')
    await client._client.close()
    client._client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[
        Model(id='yes', created=0, object='model', owned_by='owner', supported_parameters=['tools']),
        Model(id='unknown', created=0, object='model', owned_by='owner'),
    ]))))
    assert await client.list_models() == ['unknown', 'yes']
    assert capability(client, 'yes') is True
    assert capability(client, 'unknown') is None


@pytest.mark.asyncio
async def test_stream_http_admission_rejects_unsupported():
    from app.api import incremental_chat
    from fastapi import HTTPException
    service = SimpleNamespace(registry=SimpleNamespace(get=lambda _: SimpleNamespace(provider_id='ollama')))
    request = StreamChatRequest(provider_id='ollama', model='m', web_search_enabled=True,
                                messages=[{'role':'user','content':'news'}])
    with pytest.raises(HTTPException) as exc:
        await incremental_chat(request, service)
    assert exc.value.status_code == 400 and 'web_search_unsupported' in exc.value.detail
