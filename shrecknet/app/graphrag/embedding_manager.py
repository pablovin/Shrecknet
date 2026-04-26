"""Process-local embedding manager for Elder query embedding stability."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.core.config_store import get_settings
from app.graphrag.embedding_service import get_embedding_model, get_embedding_model_id

logger = logging.getLogger(__name__)


class EmbeddingManagerError(RuntimeError):
    """Base embedding manager failure."""


class EmbeddingQueueFullError(EmbeddingManagerError):
    """Raised when manager queue is full."""


class EmbeddingRequestTimeoutError(EmbeddingManagerError):
    """Raised when a queued job misses its response deadline."""


@dataclass(slots=True)
class _EmbeddingJob:
    query: str
    request_id: str
    submitted_monotonic: float
    future: asyncio.Future[list[float]]
    cache_key: str


class EmbeddingManager:
    """Single-worker micro-batching manager for Elder query embeddings."""

    def __init__(
        self,
        *,
        queue_max_size: int,
        batch_max_size: int,
        batch_wait_ms: int,
        cache_size: int,
        request_timeout_s: float,
    ) -> None:
        self.queue_max_size = max(1, int(queue_max_size))
        self.batch_max_size = max(1, int(batch_max_size))
        self.batch_wait_ms = max(1, int(batch_wait_ms))
        self.cache_size = max(1, int(cache_size))
        self.request_timeout_s = max(0.1, float(request_timeout_s))

        self._queue: asyncio.Queue[_EmbeddingJob] = asyncio.Queue(
            maxsize=self.queue_max_size
        )
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed-mgr")
        self._worker_task: asyncio.Task[None] | None = None
        self._shutdown = False
        self._inflight_batches = 0
        self.embeddings_ready = False

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join((query or "").strip().lower().split())

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        await self._prewarm()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="embedding-manager")

    async def stop(self) -> None:
        self._shutdown = True
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _prewarm(self) -> None:
        started = time.monotonic()
        loop = asyncio.get_running_loop()

        def _warm() -> None:
            model = get_embedding_model()
            model.encode(["startup prewarm"], normalize_embeddings=True)

        await loop.run_in_executor(self._executor, _warm)
        self.embeddings_ready = True
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        logger.info("embedding_manager_prewarm_done elapsed_ms=%.2f", elapsed_ms)
        print(f"[EMBED_DIAG] step=manager_prewarm_done elapsed_ms={elapsed_ms:.2f}")

    async def _cache_get(self, key: str) -> list[float] | None:
        async with self._cache_lock:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return value

    async def _cache_put(self, key: str, value: list[float]) -> None:
        async with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    async def embed_query(self, query: str, *, request_id: str) -> list[float]:
        normalized = self._normalize_query(query)
        cache_key = f"{get_embedding_model_id()}::{normalized}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            print(
                f"[EMBED_DIAG] step=cache_hit request_id={request_id} cache_key_len={len(cache_key)} queue_depth={self._queue.qsize()}"
            )
            return cached

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[float]] = loop.create_future()
        submitted = time.monotonic()
        job = _EmbeddingJob(
            query=query,
            request_id=request_id,
            submitted_monotonic=submitted,
            future=fut,
            cache_key=cache_key,
        )

        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            print(
                f"[EMBED_DIAG] step=queue_full request_id={request_id} queue_depth={self._queue.qsize()} max_queue={self.queue_max_size}"
            )
            raise EmbeddingQueueFullError("embedding queue is full") from exc

        print(
            f"[EMBED_DIAG] step=enqueue request_id={request_id} queue_depth={self._queue.qsize()} inflight_batches={self._inflight_batches}"
        )
        try:
            vector = await asyncio.wait_for(fut, timeout=self.request_timeout_s)
        except asyncio.TimeoutError as exc:
            print(
                f"[EMBED_DIAG] step=request_timeout request_id={request_id} timeout_s={self.request_timeout_s:.2f} queue_depth={self._queue.qsize()}"
            )
            if not fut.done():
                fut.cancel()
            raise EmbeddingRequestTimeoutError(
                f"embedding request timed out after {self.request_timeout_s:.2f}s"
            ) from exc

        total_ms = round((time.monotonic() - submitted) * 1000, 2)
        print(
            f"[EMBED_DIAG] step=job_done request_id={request_id} job_total_ms={total_ms:.2f} queue_depth={self._queue.qsize()}"
        )
        return vector

    async def _worker_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._shutdown:
            try:
                first = await self._queue.get()
            except asyncio.CancelledError:
                break

            batch = [first]
            wait_s = self.batch_wait_ms / 1000.0
            deadline = time.monotonic() + wait_s
            while len(batch) < self.batch_max_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    nxt = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                batch.append(nxt)

            texts = [job.query for job in batch]
            submitted_min = min(job.submitted_monotonic for job in batch)
            self._inflight_batches += 1
            print(
                f"[EMBED_DIAG] step=batch_start batch_size={len(batch)} queue_depth={self._queue.qsize()} "
                f"inflight_batches={self._inflight_batches}"
            )
            encode_started = time.monotonic()
            try:
                vectors = await loop.run_in_executor(
                    self._executor,
                    self._encode_batch,
                    texts,
                )
            except Exception as exc:  # pragma: no cover - defensive path
                for job in batch:
                    if not job.future.done():
                        job.future.set_exception(EmbeddingManagerError(str(exc)))
                vectors = []
            encode_ms = round((time.monotonic() - encode_started) * 1000, 2)
            wait_ms = round((encode_started - submitted_min) * 1000, 2)
            print(
                f"[EMBED_DIAG] step=batch_done batch_size={len(batch)} encode_ms={encode_ms:.2f} "
                f"job_wait_ms={wait_ms:.2f} queue_depth={self._queue.qsize()} inflight_batches={self._inflight_batches}"
            )

            if vectors:
                for job, vector in zip(batch, vectors):
                    await self._cache_put(job.cache_key, vector)
                    if not job.future.done():
                        job.future.set_result(vector)

            self._inflight_batches = max(0, self._inflight_batches - 1)
            for _ in batch:
                self._queue.task_done()

    @staticmethod
    def _encode_batch(texts: list[str]) -> list[list[float]]:
        model = get_embedding_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [list(map(float, row)) for row in vectors]


_manager: EmbeddingManager | None = None
_manager_lock = asyncio.Lock()


def get_embedding_manager() -> EmbeddingManager | None:
    return _manager


async def ensure_embedding_manager_started() -> EmbeddingManager:
    global _manager
    settings = get_settings()
    started = time.monotonic()
    logger.info("embedding_manager_init_start enabled=%s", settings.elder_embedding_manager_enabled)
    print(
        f"[EMBED_DIAG] step=manager_init_start enabled={settings.elder_embedding_manager_enabled}"
    )
    async with _manager_lock:
        if _manager is not None:
            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
            logger.info("embedding_manager_init_done reused=true elapsed_ms=%.2f", elapsed_ms)
            print(f"[EMBED_DIAG] step=manager_init_done reused=true elapsed_ms={elapsed_ms:.2f}")
            return _manager
        _manager = EmbeddingManager(
            queue_max_size=settings.elder_embedding_queue_max_size,
            batch_max_size=settings.elder_embedding_batch_max_size,
            batch_wait_ms=settings.elder_embedding_batch_wait_ms,
            cache_size=settings.elder_embedding_cache_size,
            request_timeout_s=max(10.0, float(settings.elder_embedding_request_timeout_s)),
        )
        await _manager.start()
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        logger.info("embedding_manager_init_done reused=false elapsed_ms=%.2f", elapsed_ms)
        print(f"[EMBED_DIAG] step=manager_init_done reused=false elapsed_ms={elapsed_ms:.2f}")
        return _manager


async def stop_embedding_manager() -> None:
    global _manager
    async with _manager_lock:
        manager = _manager
        _manager = None
    if manager is not None:
        await manager.stop()
