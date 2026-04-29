from __future__ import annotations

import json

import pytest

from app.memory import RedisConversationMemory
from app.schemas import ChatMessage


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple, dict]] = []

    def rpush(self, key: str, *values: str):
        self.ops.append(("rpush", (key, values), {}))
        return self

    def ltrim(self, key: str, start: int, end: int):
        self.ops.append(("ltrim", (key, start, end), {}))
        return self

    def expire(self, key: str, ttl: int):
        self.ops.append(("expire", (key, ttl), {}))
        return self

    async def execute(self):
        for op, args, _ in self.ops:
            if op == "rpush":
                key, values = args
                self.redis.store.setdefault(key, [])
                for value in values:
                    self.redis.store[key].append(value.encode("utf-8"))
            elif op == "ltrim":
                key, start, end = args
                values = self.redis.store.get(key, [])
                if end == -1:
                    end = len(values) - 1
                self.redis.store[key] = values[start : end + 1]
            elif op == "expire":
                key, ttl = args
                self.redis.ttls[key] = ttl


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, list[bytes]] = {}
        self.ttls: dict[str, int] = {}

    async def lrange(self, key: str, start: int, end: int):
        values = self.store.get(key, [])
        if end == -1:
            end = len(values) - 1
        return values[start : end + 1]

    def pipeline(self, transaction: bool = True):
        del transaction
        return _FakePipeline(self)

    async def ping(self):
        return True


@pytest.mark.asyncio
async def test_redis_memory_append_trim_and_load() -> None:
    redis = _FakeRedis()
    memory = RedisConversationMemory(redis, ttl_seconds=3600, max_messages=3)

    await memory.append(
        "abc",
        [
            ChatMessage(role="user", content="1"),
            ChatMessage(role="assistant", content="2"),
            ChatMessage(role="user", content="3"),
            ChatMessage(role="assistant", content="4"),
        ],
    )

    loaded = await memory.load("abc")
    assert [m.content for m in loaded] == ["2", "3", "4"]


@pytest.mark.asyncio
async def test_redis_memory_ttl_set() -> None:
    redis = _FakeRedis()
    memory = RedisConversationMemory(redis, ttl_seconds=120, max_messages=5)
    await memory.append("ttl", [ChatMessage(role="user", content="hello")])

    assert redis.ttls["shreckllm:conv:ttl"] == 120


@pytest.mark.asyncio
async def test_redis_memory_serialization_shape() -> None:
    redis = _FakeRedis()
    memory = RedisConversationMemory(redis, ttl_seconds=120, max_messages=10)
    message = ChatMessage(role="user", content="hello")
    await memory.append("shape", [message])
    payload = redis.store["shreckllm:conv:shape"][0].decode("utf-8")
    assert json.loads(payload) == {"role": "user", "content": "hello"}
