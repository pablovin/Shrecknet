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
                ["SemanticDocument"],
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
async def test_embedding_runtime_restarts_missing_worker():
    manager = EmbeddingRuntime(
        queue_max_size=10,
        batch_max_size=1,
        batch_wait_ms=1,
        cache_size=10,
        request_timeout_s=1.0,
        startup_timeout_s=1.0,
    )
    manager.status = "ready"

    def _encode(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    manager._encode_batch = _encode  # type: ignore[method-assign]
    try:
        result = await manager.embed_query("restart worker", request_id="restart")
        assert result == [0.1, 0.2, 0.3]
        assert manager._worker_task is not None
        assert not manager._worker_task.done()
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_semantic_search_uses_embedding_runtime(monkeypatch):
    service = RetrievalService(_FakeGraphSession())

    class _Mgr:
        async def embed_query(self, query: str, *, request_id: str, timeout_s: float | None = None):
            assert query == "Tamura vs giant"
            assert request_id
            assert timeout_s == 10.0
            return [0.1, 0.2, 0.3]

    async def _mgr_start():
        return _Mgr()

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


@pytest.mark.parametrize('cancel_request', [False, True])
def test_embedding_runtime_survives_successive_celery_task_loops(monkeypatch, cancel_request):
    from app.graphrag import embedding_runtime as module
    from app.utils.async_helpers import run_async

    manager = EmbeddingRuntime(
        queue_max_size=10, batch_max_size=32, batch_wait_ms=5,
        cache_size=10, request_timeout_s=0.5, startup_timeout_s=1,
    )
    manager._prewarm = lambda: asyncio.sleep(0)
    calls = []

    def encode(texts):
        calls.append(list(texts))
        return [[0.1, 0.2] for _ in texts]

    manager._encode_batch = encode
    monkeypatch.setattr(module, '_runtime', manager)
    monkeypatch.setattr(module, '_runtime_loop', None)
    monkeypatch.setattr(module, '_runtime_lock', asyncio.Lock())

    async def first_turn():
        runtime = await module.get_ready_embedding_runtime()
        assert await runtime.embed_query('cached', request_id='first') == [0.1, 0.2]
        if cancel_request:
            pending = asyncio.create_task(runtime.embed_query('cancelled', request_id='cancelled'))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        # Let the idle worker bind its queue to this loop before run_async closes it.
        await asyncio.sleep(0.01)

    async def next_turn(index):
        runtime = await module.get_ready_embedding_runtime()
        assert runtime is manager
        before = len(calls)
        assert await runtime.embed_query('cached', request_id='cache') == [0.1, 0.2]
        assert len(calls) == before
        results = await asyncio.gather(*[
            runtime.embed_query(f'turn {index} question {i}', request_id=f'{index}-{i}')
            for i in range(2)
        ])
        assert results == [[0.1, 0.2], [0.1, 0.2]]
        assert runtime._worker_task is not None and not runtime._worker_task.done()
        await asyncio.sleep(0.01)

    try:
        run_async(first_turn())
        run_async(next_turn(2))
        run_async(next_turn(3))
    finally:
        run_async(module.stop_embedding_runtime())


def test_embedding_runtime_recovers_interrupted_prewarm(monkeypatch):
    from app.graphrag import embedding_runtime as module
    from app.utils.async_helpers import run_async

    manager = EmbeddingRuntime(
        queue_max_size=10, batch_max_size=32, batch_wait_ms=5,
        cache_size=10, request_timeout_s=0.5, startup_timeout_s=1,
    )
    manager.startup_timeout_s = 0.01
    manager._prewarm = lambda: asyncio.sleep(10)
    manager._encode_batch = lambda texts: [[0.1] for _ in texts]
    monkeypatch.setattr(module, '_runtime', manager)
    monkeypatch.setattr(module, '_runtime_loop', None)
    monkeypatch.setattr(module, '_runtime_lock', asyncio.Lock())

    async def interrupted_turn():
        assert (await module.ensure_embedding_runtime_started()).status == 'starting'

    async def resumed_turn():
        manager._prewarm = lambda: asyncio.sleep(0)
        runtime = await module.get_ready_embedding_runtime()
        assert await runtime.embed_query('resumed', request_id='resumed') == [0.1]

    try:
        run_async(interrupted_turn())
        run_async(resumed_turn())
    finally:
        run_async(module.stop_embedding_runtime())
