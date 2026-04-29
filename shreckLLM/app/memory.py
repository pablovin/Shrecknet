from __future__ import annotations

import json

from redis.asyncio import Redis

from app.schemas import ChatMessage


class RedisConversationMemory:
    def __init__(self, redis: Redis, *, ttl_seconds: int, max_messages: int) -> None:
        self.redis = redis
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_messages = max(1, int(max_messages))

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"shreckllm:conv:{conversation_id}"

    async def load(self, conversation_id: str) -> list[ChatMessage]:
        key = self._key(conversation_id)
        raw_entries = await self.redis.lrange(key, 0, -1)
        out: list[ChatMessage] = []
        for entry in raw_entries:
            if isinstance(entry, bytes):
                payload = entry.decode("utf-8")
            else:
                payload = str(entry)
            out.append(ChatMessage.model_validate(json.loads(payload)))
        return out

    async def append(self, conversation_id: str, messages: list[ChatMessage]) -> None:
        if not messages:
            return
        key = self._key(conversation_id)
        serialized = [m.model_dump_json() for m in messages]
        pipe = self.redis.pipeline(transaction=True)
        pipe.rpush(key, *serialized)
        pipe.ltrim(key, -self.max_messages, -1)
        pipe.expire(key, self.ttl_seconds)
        await pipe.execute()

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False
