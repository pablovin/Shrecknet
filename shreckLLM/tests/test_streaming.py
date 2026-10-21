import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from app.streaming import StreamChatRequest, stream_chat, provider_messages
from app.ollama_client import OllamaClient
from app.concurrency import RequestLimiter


def request(**extra):
    return StreamChatRequest(provider_id='ollama',model='test',messages=[{'role':'user','content':'Hello'}],**extra)


@pytest.mark.asyncio
async def test_ollama_stream_exposes_provider_deltas_and_completion():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for text in ['Hi',' Fiona']:
                yield (json.dumps({'message':{'content':text},'done':False})+'\n').encode()
                await asyncio.sleep(0)
            yield b'{"done":true,"eval_count":2}\n'
    client=OllamaClient(base_url='http://test',timeout_s=1)
    await client._client.aclose()
    client._client=httpx.AsyncClient(base_url='http://test',transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=Body())))
    try:
        events=[event async for event in client.stream(request())]
    finally:
        await client.aclose()
    assert [e['text'] for e in events if e['type']=='delta']==['Hi',' Fiona']
    assert events[-1]=={'type':'done'}


@pytest.mark.asyncio
async def test_gateway_releases_provider_slot_on_stream_close_and_does_not_retry():
    class Adapter:
        calls=0
        closed=False
        async def stream(self,req):
            self.calls+=1
            try:
                yield {'type':'delta','text':'first'}
                await asyncio.sleep(100)
            finally:
                self.closed=True
    adapter=Adapter()
    async def cooldown(_): pass
    service=SimpleNamespace(registry=SimpleNamespace(get=lambda _:adapter),_runtime=SimpleNamespace(provider_defaults={'ollama':{}},max_queue_wait_seconds=1,request_timeout_seconds=1),limiter=RequestLimiter(max_concurrent=1),_provider_semaphores={'ollama':asyncio.Semaphore(1)},_wait_for_provider_cooldown=cooldown)
    stream=stream_chat(service,request())
    assert (await stream.__anext__())['text']=='first'
    await stream.aclose()
    assert adapter.closed and adapter.calls==1 and service._provider_semaphores['ollama']._value==1


def test_inline_images_reject_remote_urls_and_convert_per_provider():
    with pytest.raises(ValueError):
        StreamChatRequest(provider_id='ollama',model='m',messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'http://localhost/private'}}]}])
    req=StreamChatRequest(provider_id='ollama',model='m',messages=[{'role':'user','content':[{'type':'text','text':'Image'},{'type':'image_url','image_url':{'url':'data:image/png;base64,aGVsbG8='}}]}])
    rows,_=provider_messages(req,'ollama')
    assert rows[0]['images']==['aGVsbG8='] and rows[0]['content']=='Image'
