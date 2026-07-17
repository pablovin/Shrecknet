"""Shared text encoder used by Semantic V2 and document ingestion.

This module intentionally contains no graph persistence, derived-document
generation, index management, or reset/backfill behavior.
"""

from __future__ import annotations

import gc
import logging
import threading
import time

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config_store import get_settings

logger = logging.getLogger(__name__)

DOCUMENT_EMBEDDING_PREFIX = "passage: "
QUERY_EMBEDDING_PREFIX = "query: "


def document_embedding_text(text: str) -> str:
    text = (text or "").strip()
    return text if text.startswith(DOCUMENT_EMBEDDING_PREFIX) else f"{DOCUMENT_EMBEDDING_PREFIX}{text}"


def query_embedding_text(text: str) -> str:
    text = (text or "").strip()
    return text if text.startswith(QUERY_EMBEDDING_PREFIX) else f"{QUERY_EMBEDDING_PREFIX}{text}"


_model_lock = threading.Lock()
_cached_model: SentenceTransformer | None = None
_cached_model_key: tuple[str, str] | None = None
_inference_lock = threading.Lock()
_inference_semaphore: threading.BoundedSemaphore | None = None
_inference_semaphore_size: int | None = None


def _resolve_embedding_device(configured_device: str) -> str:
    requested = (configured_device or "cpu").strip().lower()
    if not requested.startswith("cuda"):
        return requested or "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return requested
    except Exception as exc:  # pragma: no cover
        logger.warning("CUDA availability check failed; using CPU: %s", exc)
    return "cpu"


def _current_model_key() -> tuple[str, str]:
    settings = get_settings()
    return settings.embedding_model_id, _resolve_embedding_device(settings.embedding_device)


def get_embedding_model_id() -> str:
    return get_settings().embedding_model_id


def get_embedding_dimension() -> int:
    return get_settings().embedding_dimension


def get_embedding_model(diagnostic_request_id: str | None = None) -> SentenceTransformer:
    global _cached_model, _cached_model_key
    key = _current_model_key()
    if _cached_model is not None and _cached_model_key == key:
        return _cached_model
    with _model_lock:
        if _cached_model is not None and _cached_model_key == key:
            return _cached_model
        model_id, device = key
        started = time.monotonic()
        logger.info("embedding_model_load_start request_id=%s model=%s device=%s", diagnostic_request_id, model_id, device)
        _cached_model = SentenceTransformer(model_id, device=device)
        _cached_model_key = key
        logger.info("embedding_model_load_done request_id=%s elapsed_ms=%.2f", diagnostic_request_id, (time.monotonic() - started) * 1000)
        return _cached_model


def get_embedding_inference_semaphore() -> threading.BoundedSemaphore:
    global _inference_semaphore, _inference_semaphore_size
    size = max(1, int(get_settings().elder_embedding_inference_concurrency))
    if _inference_semaphore is not None and _inference_semaphore_size == size:
        return _inference_semaphore
    with _inference_lock:
        if _inference_semaphore is None or _inference_semaphore_size != size:
            _inference_semaphore = threading.BoundedSemaphore(size)
            _inference_semaphore_size = size
        return _inference_semaphore


class EmbeddingService:
    """Provider-neutral encoder retained as the stable shared service name."""

    def __init__(self, graph_session=None) -> None:
        self.graph_session = graph_session
        settings = get_settings()
        self.model_id = settings.embedding_model_id
        self.embed_dim = settings.embedding_dimension

    def embed_texts(
        self, texts: list[str], diagnostic_request_id: str | None = None
    ) -> list[list[float]]:
        global _cached_model, _cached_model_key
        prepared = [document_embedding_text(text) for text in texts]
        model = get_embedding_model(diagnostic_request_id)
        gate = get_embedding_inference_semaphore()
        for attempt in range(3):
            try:
                with gate:
                    encoded = model.encode(prepared, normalize_embeddings=True)
                array = np.asarray(encoded, dtype=np.float32, order="C")
                return [row.copy().tolist() for row in array]
            except (RuntimeError, ValueError, BufferError) as exc:
                retryable = any(term in str(exc).lower() for term in ("meta tensor", "re-sized", "export", "buffer"))
                if not retryable or attempt == 2:
                    raise
                logger.warning("Embedding encode failed; reloading model: %s", exc)
                gc.collect()
                with _model_lock:
                    _cached_model = None
                    _cached_model_key = None
                model = get_embedding_model(diagnostic_request_id)
        raise RuntimeError("embedding retries exhausted")

    def embed_text(self, text: str, diagnostic_request_id: str | None = None) -> list[float]:
        return self.embed_texts([text], diagnostic_request_id)[0]
