"""Character Companion client: durable actions, streaming and independent models."""
from __future__ import annotations
import json
from typing import Any, AsyncIterator, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from .models import LLMModelTarget
from .errors import raise_for_status
from .companion_rich_response import RichResponse

PREFIX = '/users/me/character-companion'


class CompanionModelConfig(BaseModel):
    model_companion_routing: LLMModelTarget
    model_companion_synthesis: LLMModelTarget
    model_companion_interim: LLMModelTarget
    model_companion_rapport_update: LLMModelTarget

    model_companion_web_search: LLMModelTarget | None = None
    model_companion_evidence: LLMModelTarget | None = None


class CompanionModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_companion_routing: LLMModelTarget | None = None
    model_companion_synthesis: LLMModelTarget | None = None
    model_companion_interim: LLMModelTarget | None = None
    model_companion_rapport_update: LLMModelTarget | None = None

    model_companion_web_search: LLMModelTarget | None = None
    model_companion_evidence: LLMModelTarget | None = None


class CompanionSelection(BaseModel):
    character_agent_id: str | None
    version: int
    availability: Literal['none','ready','identity_pending','archived','unavailable']
    character: dict[str, Any] | None


class CompanionRapport(BaseModel):
    character_agent_id: str
    scores: dict[str, int]
    version: int
    generation: int
    pending_count: int


class CompanionPresentationConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    companion_web_images_enabled: bool = False
    companion_rich_response_enabled: bool = False
    companion_rich_response_timeout_s: float = Field(2, ge=0.1, le=10)


class CompanionTurn(BaseModel):
    model_config = ConfigDict(extra='allow')
    turn_id: str | None = None
    id: str | None = None
    chat_id: str
    status: str
    result: dict[str, Any] | None = None

    @property
    def rich_response(self) -> RichResponse | None:
        """Decode supported presentation, retaining the raw result and text on failure."""
        try:
            return RichResponse.model_validate((self.result or {}).get('rich_response'))
        except ValueError:
            return None


class CompanionEvent(BaseModel):
    version: Literal[1]
    event_id: str
    occurred_at: str
    chat_id: str | None
    turn_id: str | None
    payload: dict[str, Any]


class CharacterCompanionAPI:
    """Private canonical-character conversations; see the Character Companion workflow guide."""
    def __init__(self, client):
        self.client = client

    async def capabilities(self):
        return await self.client.raw_request('GET', PREFIX+'/capabilities')

    async def candidates(self, **filters):
        return await self.client.raw_request('GET', PREFIX+'/candidates', params=filters)

    async def selection(self) -> CompanionSelection:
        """Read the globally persisted selection and optimistic version."""
        return CompanionSelection.model_validate(await self.client.raw_request('GET', PREFIX+'/selection'))

    async def select(self, character_agent_id: str, expected_version: int) -> CompanionSelection:
        """Select a canonical character using the current selection version."""
        return CompanionSelection.model_validate(await self.client.raw_request('PUT', PREFIX+'/selection', json={'character_agent_id':character_agent_id,'expected_version':expected_version}))

    async def clear(self, expected_version: int) -> CompanionSelection:
        """Clear selection while preserving conversations and rapport."""
        return CompanionSelection.model_validate(await self.client.raw_request('DELETE', PREFIX+'/selection', params={'expected_version':expected_version}))

    async def relationships(self, **filters):
        return await self.client.raw_request('GET', PREFIX+'/relationships', params=filters)

    async def rapport(self, character_id: str) -> CompanionRapport:
        return CompanionRapport.model_validate(await self.client.raw_request('GET', PREFIX+f'/relationships/{character_id}/rapport'))

    async def rapport_history(self, character_id: str, **filters):
        return await self.client.raw_request('GET', PREFIX+f'/relationships/{character_id}/rapport/history', params=filters)

    async def reset_rapport(self, character_id: str, expected_version: int, *, idempotency_key: str):
        """Reset this pair using its current version and a stable UUID key."""
        return await self.client.raw_request('POST', PREFIX+f'/relationships/{character_id}/rapport/reset', json={'expected_version':expected_version}, headers={'Idempotency-Key':idempotency_key})

    async def chats(self, **filters):
        return await self.client.raw_request('GET', PREFIX+'/chats', params=filters)

    async def create_chat(self, character_agent_id: str, selection_version: int, *, idempotency_key: str, title=None, default_scope=None):
        """Create a character-bound chat; retain the UUID key for retries."""
        return await self.client.raw_request('POST', PREFIX+'/chats', json={'character_agent_id':character_agent_id,'selection_version':selection_version,'title':title,'default_scope':default_scope}, headers={'Idempotency-Key':idempotency_key})

    async def chat(self, chat_id):
        return await self.client.raw_request('GET', PREFIX+f'/chats/{chat_id}')

    async def rename_chat(self, chat_id, title, expected_revision):
        return await self.client.raw_request('PATCH', PREFIX+f'/chats/{chat_id}', json={'title':title,'expected_revision':expected_revision})

    async def delete_chat(self, chat_id):
        return await self.client.raw_request('DELETE', PREFIX+f'/chats/{chat_id}')

    async def messages(self, chat_id, **filters):
        return await self.client.raw_request('GET', PREFIX+f'/chats/{chat_id}/messages', params=filters)

    async def send(self, chat_id: str, text: str, selection_version: int, *, idempotency_key: str, attachment_ids=None, context=None, tools=None, retry_of_turn_id=None, foundry_context=None, general_knowledge_opt_in=False, use_page_scope=False, clarification_id=None) -> CompanionTurn:
        """Admit one turn with a stable UUID key; optional foundry_context is an immutable client snapshot."""
        body = {'text':text,'selection_version':selection_version,'attachment_ids':attachment_ids or [],'context':context,'retry_of_turn_id':retry_of_turn_id}
        if general_knowledge_opt_in:
            body['general_knowledge_opt_in'] = True
        if use_page_scope:
            body['use_page_scope'] = True
        if clarification_id is not None:
            body['clarification_id'] = clarification_id
        if foundry_context is not None:
            body['foundry_context'] = foundry_context
        if tools is not None:
            body['tools'] = tools
        return CompanionTurn.model_validate(await self.client.raw_request('POST', PREFIX+f'/chats/{chat_id}/turns', json=body, headers={'Idempotency-Key':idempotency_key}))

    async def turn(self, turn_id) -> CompanionTurn:
        """Read a durable snapshot, including provisional draft and terminal result."""
        return CompanionTurn.model_validate(await self.client.raw_request('GET', PREFIX+f'/turns/{turn_id}'))

    async def cancel(self, turn_id):
        """Cancel a nonterminal turn and fence late provider output."""
        return await self.client.raw_request('POST', PREFIX+f'/turns/{turn_id}/cancel')

    async def upload(self, filename: str, content: bytes, mime_type: str):
        """Upload a private image for one future turn."""
        return await self.client.raw_request('POST', PREFIX+'/attachments', files={'file':(filename,content,mime_type)})

    async def attachment(self, attachment_id: str) -> dict[str, Any]:
        """Read owner-only attachment metadata; no public storage path is returned."""
        return await self.client.raw_request('GET', PREFIX+f'/attachments/{attachment_id}')

    async def attachment_content(self, attachment_id: str) -> bytes:
        """Download authenticated normalized image bytes without JSON decoding."""
        response = await self.client._client.get(PREFIX+f'/attachments/{attachment_id}/content', headers=self.client._headers())
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = None
            raise_for_status(response.status_code, detail)
        return response.content

    async def rich_asset_content(self, turn_id: str, asset_id: str) -> bytes:
        """Download saved Atlas media only; other services retain their own auth clients."""
        from uuid import UUID
        import re
        turn_id = str(UUID(turn_id))
        if not re.fullmatch(r'a[a-f0-9]{20}', asset_id):
            raise ValueError('Invalid rich asset identifier')
        response = await self.client._client.get(PREFIX+f'/turns/{turn_id}/assets/{asset_id}', headers=self.client._headers())
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = None
            raise_for_status(response.status_code, detail)
        return response.content

    async def presentation_config(self) -> CompanionPresentationConfig:
        """Read administrator presentation controls; old servers use disabled defaults."""
        return CompanionPresentationConfig.model_validate(await self.client.raw_request('GET','/config/'))

    async def update_presentation_config(self, changes: CompanionPresentationConfig) -> CompanionPresentationConfig:
        """Set presentation controls using the existing administrator config boundary."""
        return CompanionPresentationConfig.model_validate(await self.client.raw_request('PUT','/config/',json=changes.model_dump(exclude_unset=True)))

    async def delete_attachment(self, attachment_id):
        return await self.client.raw_request('DELETE', PREFIX+f'/attachments/{attachment_id}')

    async def get_model_config(self) -> CompanionModelConfig:
        """Read administrator Companion model assignments (new roles may be absent on older servers)."""
        return CompanionModelConfig.model_validate(await self.client.raw_request('GET','/config/'))

    async def update_model_config(self, changes: CompanionModelConfigUpdate) -> CompanionModelConfig:
        """Update only supplied administrator model assignments."""
        return CompanionModelConfig.model_validate(await self.client.raw_request('PUT','/config/',json=changes.model_dump(exclude_none=True)))

    async def events(self, turn_id: str | None = None, *, after: str | None = None) -> AsyncIterator[tuple[str, CompanionEvent]]:
        """Stream versioned events; resume with after without submitting another turn."""
        path = PREFIX+(f'/turns/{turn_id}/events' if turn_id else '/events')
        headers = {**self.client._headers(),'Accept':'text/event-stream'}
        if after:
            headers['Last-Event-ID'] = after
        async with self.client._client.stream('GET',path,headers=headers,timeout=None) as response:
            if response.status_code >= 400:
                await response.aread()
                raise_for_status(response.status_code,response.json().get('detail'))
            name, data = '', []
            async for line in response.aiter_lines():
                if line.startswith('event:'):
                    name = line[6:].strip()
                elif line.startswith('data:'):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    yield name, CompanionEvent.model_validate_json('\n'.join(data))
                    name, data = '', []
