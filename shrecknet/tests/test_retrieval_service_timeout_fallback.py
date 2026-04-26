from __future__ import annotations

import asyncio
import time

import pytest

from app.graphrag.embedding_runtime import (
    EmbeddingRuntime,
    _EmbeddingJob,
    EmbeddingRuntimeQueueFull,
    EmbeddingRuntimeRequestTimeout,
)
from app.graphrag.retrieval_service import RetrievalService


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    async def data(self):
        return self._rows


class _FakeNode(dict):
    def __init__(self, labels, **kwargs):
        super().__init__(**kwargs)
        self.labels = labels


class _FakeGraphSession:
    async def run(self, query, **params):
        del params
        if "CALL db.index.vector.queryNodes" in query:
            chunk = _FakeNode(
                ["EntityChunk"],
                chunk_id="c-1",
                chunk_type="text",
                chunk_index=0,
                text_chunk="Tamura crushes the giant's balance with feints.",
            )
            parent = _FakeNode(
                ["EntityInstance"],
                entity_instance_id="ent-1",
                name="Tamura",
                alias="Tamura",
                ontology_id=2,
                text="Tamura fights with speed and control.",
            )
            return _FakeResult([{"chunk": chunk, "parent": parent, "score": 0.91}])
        if "MATCH (n)" in query and "MATCH (n)-[r]->(m)" in query:
            return _FakeResult([])
        return _FakeResult([])


@pytest.mark.asyncio
async def test_embedding_runtime_cache_hit_bypasses_queue():
    manager = EmbeddingRuntime(
        queue_max_size=10,
        batch_max_size=32,
        batch_wait_ms=5,
        cache_size=100,
        request_timeout_s=2.0,
        startup_timeout_s=2.0,
    )

    calls: list[list[str]] = []
    manager._prewarm = lambda: asyncio.sleep(0)  # type: ignore[method-assign]

    def _encode(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    manager._encode_batch = _encode  # type: ignore[method-assign]
    await manager.start()
    try:
        first = await manager.embed_query("How Tamura fights", request_id="r1")
        second = await manager.embed_query("How   Tamura   fights", request_id="r2")
        assert first == second
        assert len(calls) == 1
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_embedding_runtime_micro_batches_concurrent_jobs():
    manager = EmbeddingRuntime(
        queue_max_size=50,
        batch_max_size=32,
        batch_wait_ms=20,
        cache_size=10,
        request_timeout_s=2.0,
        startup_timeout_s=2.0,
    )
    manager._prewarm = lambda: asyncio.sleep(0)  # type: ignore[method-assign]

    calls: list[list[str]] = []

    def _encode(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    manager._encode_batch = _encode  # type: ignore[method-assign]
    await manager.start()
    try:
        results = await asyncio.gather(
            *[
                manager.embed_query(f"question {idx}", request_id=f"r{idx}")
                for idx in range(8)
            ]
        )
        assert len(results) == 8
        assert len(calls) == 1
        assert len(calls[0]) == 8
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_embedding_runtime_queue_full_fails_fast():
    manager = EmbeddingRuntime(
        queue_max_size=1,
        batch_max_size=1,
        batch_wait_ms=1,
        cache_size=10,
        request_timeout_s=1.0,
        startup_timeout_s=1.0,
    )
    manager.status = "ready"
    loop = asyncio.get_running_loop()
    manager._queue.put_nowait(
        _EmbeddingJob(
            text="existing",
            request_id="x",
            submitted_monotonic=time.monotonic(),
            future=loop.create_future(),
            cache_key="k",
        )
    )

    with pytest.raises(EmbeddingRuntimeQueueFull):
        await manager.embed_query("new query", request_id="b")


@pytest.mark.asyncio
async def test_embedding_runtime_request_timeout():
    manager = EmbeddingRuntime(
        queue_max_size=10,
        batch_max_size=1,
        batch_wait_ms=1,
        cache_size=10,
        request_timeout_s=0.05,
        startup_timeout_s=1.0,
    )
    manager._prewarm = lambda: asyncio.sleep(0)  # type: ignore[method-assign]

    def _encode(texts: list[str]) -> list[list[float]]:
        time.sleep(0.2)
        return [[0.1, 0.2, 0.3] for _ in texts]

    manager._encode_batch = _encode  # type: ignore[method-assign]
    await manager.start()
    try:
        with pytest.raises(EmbeddingRuntimeRequestTimeout):
            await manager.embed_query("slow query", request_id="slow")
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_semantic_search_uses_embedding_runtime(monkeypatch):
    service = RetrievalService(_FakeGraphSession())

    async def _skip_index():
        return True

    class _Mgr:
        async def embed_query(self, query: str, *, request_id: str):
            assert query == "Tamura vs giant"
            assert request_id
            return [0.1, 0.2, 0.3]

    async def _mgr_start():
        return _Mgr()

    monkeypatch.setattr(service.embedding_service, "ensure_chunk_vector_index", _skip_index)
    monkeypatch.setattr(
        "app.graphrag.retrieval_service.get_ready_embedding_runtime", _mgr_start
    )
    monkeypatch.setattr(
        "app.graphrag.retrieval_service.get_settings",
        lambda: type(
            "S",
            (),
            {
                "elder_query_embedding_timeout_s": 10.0,
                "embedding_runtime_enabled": True,
            },
        )(),
    )

    result = await service.semantic_search(
        query="Tamura vs giant",
        ontology_id=2,
        k=3,
    )

    assert result["total"] == 1
    assert result["results"][0]["name"] == "Tamura"
