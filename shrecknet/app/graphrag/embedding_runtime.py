"""Process-local embedding runtime for request-time query embeddings."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.core.config_store import get_settings
from app.graphrag.embedding_service import get_embedding_model, get_embedding_model_id

logger = logging.getLogger(__name__)


class EmbeddingRuntimeError(RuntimeError):
    """Base runtime error."""


class EmbeddingRuntimeQueueFull(EmbeddingRuntimeError):
    """Queue reached configured max size."""


class EmbeddingRuntimeRequestTimeout(EmbeddingRuntimeError):
    """Request timed out waiting for runtime response."""


class EmbeddingRuntimeNotReady(EmbeddingRuntimeError):
    """Runtime not ready yet."""


class EmbeddingRuntimeFailed(EmbeddingRuntimeError):
    """Runtime startup failed and cannot serve requests."""


@dataclass(slots=True)
class _EmbeddingJob:
    text: str
    request_id: str
    submitted_monotonic: float
    cache_key: str
    future: asyncio.Future[list[float]]


class EmbeddingRuntime:
    """Single-process, single-worker micro-batched embedding runtime."""

    def __init__(
        self,
        *,
        queue_max_size: int,
        batch_max_size: int,
        batch_wait_ms: int,
        cache_size: int,
        request_timeout_s: float,
        startup_timeout_s: float,
    ) -> None:
        self.queue_max_size = max(1, int(queue_max_size))
        self.batch_max_size = max(1, int(batch_max_size))
        self.batch_wait_ms = max(1, int(batch_wait_ms))
        self.cache_size = max(1, int(cache_size))
        self.request_timeout_s = max(0.1, float(request_timeout_s))
        self.startup_timeout_s = max(1.0, float(startup_timeout_s))

        self._queue: asyncio.Queue[_EmbeddingJob] = asyncio.Queue(maxsize=self.queue_max_size)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed-runtime")
        self._worker_task: asyncio.Task[None] | None = None
        self._prewarm_task: asyncio.Task[None] | None = None
        self._shutdown = False
        self._inflight_batches = 0

        self.status: str = "starting"
        self.failure_reason: str | None = None

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join((text or "").strip().lower().split())

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        if self._prewarm_task is not None and not self._prewarm_task.done():
            return
        self.status = "starting"
        self.failure_reason = None
        print("[EMBED_DIAG] step=runtime_init_start")
        try:
            self._prewarm_task = asyncio.create_task(self._prewarm(), name="embedding-runtime-prewarm")
            await asyncio.wait_for(asyncio.shield(self._prewarm_task), timeout=self.startup_timeout_s)
            self._mark_ready()
        except asyncio.TimeoutError:
            # Keep warming in background; do not hard-fail runtime on startup timeout.
            print(
                f"[EMBED_DIAG] step=runtime_init_deferred timeout_s={self.startup_timeout_s:.2f}"
            )
            logger.warning(
                "Embedding runtime prewarm exceeded startup timeout (%.2fs); continuing in background",
                self.startup_timeout_s,
            )
            self._prewarm_task.add_done_callback(self._on_prewarm_done)
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_failed(exc)

    def _mark_ready(self) -> None:
        self.status = "ready"
        print("[EMBED_DIAG] step=runtime_init_done")
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._worker_loop(), name="embedding-runtime-worker"
            )

    def _mark_failed(self, exc: Exception) -> None:
        self.status = "failed"
        self.failure_reason = str(exc)
        print(f"[EMBED_DIAG] step=runtime_init_failed error={exc}")
        logger.exception("Embedding runtime initialization failed")

    def _on_prewarm_done(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception as exc:  # pragma: no cover
            self._mark_failed(exc)
            return
        self._mark_ready()

    async def stop(self) -> None:
        self._shutdown = True
        if self._prewarm_task is not None and not self._prewarm_task.done():
            self._prewarm_task.cancel()
            try:
                await self._prewarm_task
            except asyncio.CancelledError:
                pass
        self._prewarm_task = None
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _prewarm(self) -> None:
        loop = asyncio.get_running_loop()

        def _warm() -> None:
            model = get_embedding_model()
            model.encode(["startup prewarm"], normalize_embeddings=True)

        await loop.run_in_executor(self._executor, _warm)

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

    async def embed_query(self, text: str, *, request_id: str) -> list[float]:
        if self.status == "failed":
            raise EmbeddingRuntimeFailed(self.failure_reason or "embedding runtime failed")
        if self.status != "ready":
            raise EmbeddingRuntimeNotReady("embedding runtime not ready")

        normalized = self._normalize_text(text)
        cache_key = f"{get_embedding_model_id()}::{normalized}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            print(
                f"[EMBED_DIAG] step=cache_hit request_id={request_id} queue_depth={self._queue.qsize()}"
            )
            return cached

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[float]] = loop.create_future()
        submitted = time.monotonic()
        job = _EmbeddingJob(
            text=text,
            request_id=request_id,
            submitted_monotonic=submitted,
            cache_key=cache_key,
            future=fut,
        )
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull as exc:
            print(
                f"[EMBED_DIAG] step=queue_full request_id={request_id} queue_depth={self._queue.qsize()} max_queue={self.queue_max_size}"
            )
            raise EmbeddingRuntimeQueueFull("embedding runtime queue is full") from exc

        print(
            f"[EMBED_DIAG] step=enqueue request_id={request_id} queue_depth={self._queue.qsize()} inflight_batches={self._inflight_batches}"
        )
        try:
            vector = await asyncio.wait_for(fut, timeout=self.request_timeout_s)
        except asyncio.TimeoutError as exc:
            print(
                f"[EMBED_DIAG] step=request_timeout request_id={request_id} timeout_s={self.request_timeout_s:.2f}"
            )
            if not fut.done():
                fut.cancel()
            raise EmbeddingRuntimeRequestTimeout(
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
            deadline = time.monotonic() + (self.batch_wait_ms / 1000.0)
            while len(batch) < self.batch_max_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    nxt = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                batch.append(nxt)

            self._inflight_batches += 1
            texts = [job.text for job in batch]
            submitted_min = min(job.submitted_monotonic for job in batch)
            print(
                f"[EMBED_DIAG] step=batch_start batch_size={len(batch)} queue_depth={self._queue.qsize()} inflight_batches={self._inflight_batches}"
            )
            encode_started = time.monotonic()
            try:
                vectors = await loop.run_in_executor(self._executor, self._encode_batch, texts)
            except Exception as exc:  # pragma: no cover
                vectors = []
                for job in batch:
                    if not job.future.done():
                        job.future.set_exception(EmbeddingRuntimeError(str(exc)))
            encode_ms = round((time.monotonic() - encode_started) * 1000, 2)
            wait_ms = round((encode_started - submitted_min) * 1000, 2)
            print(
                f"[EMBED_DIAG] step=batch_done batch_size={len(batch)} encode_ms={encode_ms:.2f} job_wait_ms={wait_ms:.2f} queue_depth={self._queue.qsize()}"
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


_runtime: EmbeddingRuntime | None = None
_runtime_lock = asyncio.Lock()
_runtime_start_task: asyncio.Task[None] | None = None


def get_embedding_runtime_status() -> dict[str, str | None]:
    if _runtime is None:
        return {"status": "starting", "reason": "runtime_not_initialized"}
    return {"status": _runtime.status, "reason": _runtime.failure_reason}


async def ensure_embedding_runtime_started() -> EmbeddingRuntime:
    global _runtime
    settings = get_settings()
    async with _runtime_lock:
        if _runtime is None:
            _runtime = EmbeddingRuntime(
                queue_max_size=settings.embedding_runtime_queue_max_size,
                batch_max_size=settings.embedding_runtime_batch_max_size,
                batch_wait_ms=settings.embedding_runtime_batch_wait_ms,
                cache_size=settings.embedding_runtime_cache_size,
                request_timeout_s=max(10.0, float(settings.embedding_runtime_request_timeout_s)),
                startup_timeout_s=float(settings.embedding_runtime_startup_timeout_s),
            )
            await _runtime.start()
        return _runtime


def start_embedding_runtime_background() -> asyncio.Task[None] | None:
    global _runtime_start_task
    if _runtime_start_task is not None and not _runtime_start_task.done():
        return _runtime_start_task
    try:
        _runtime_start_task = asyncio.create_task(_background_start(), name="embedding-runtime-start")
    except RuntimeError:
        _runtime_start_task = None
    return _runtime_start_task


async def _background_start() -> None:
    await ensure_embedding_runtime_started()


async def get_ready_embedding_runtime() -> EmbeddingRuntime:
    runtime = await ensure_embedding_runtime_started()
    if runtime.status == "failed":
        raise EmbeddingRuntimeFailed(runtime.failure_reason or "embedding runtime failed")
    if runtime.status != "ready":
        raise EmbeddingRuntimeNotReady("embedding runtime not ready")
    return runtime


async def stop_embedding_runtime() -> None:
    global _runtime
    async with _runtime_lock:
        rt = _runtime
        _runtime = None
    if rt is not None:
        await rt.stop()
