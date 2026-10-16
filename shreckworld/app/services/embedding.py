"""Strict local E5 inference: model identity, token budgets, and vector validation.

There is no hash fallback. A failed load, self-test, or encode fails the request
or indexing job rather than mixing incompatible representations.
"""
from __future__ import annotations

import hashlib
import math
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from shreckdoc.pipeline import _model_token_limit, _token_count

from app.core.config import get_settings


class EmbeddingUnavailable(RuntimeError):
    """The configured semantic encoder could not pass readiness/inference checks."""


class EmbeddingInputError(ValueError):
    """A contextual E5 input exceeds the model's actual token budget."""


def model_snapshot(model_id: str, *, revision: str | None = None, local_files_only: bool = True) -> Path:
    path = Path(model_id)
    if path.is_dir():
        return path.resolve()
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(repo_id=model_id, revision=revision, local_files_only=local_files_only))


def snapshot_fingerprint(path: Path) -> str:
    """Identify actual weights and tokenizer/config files, including local models."""
    digest = hashlib.sha256()
    files = sorted(
        file for file in path.rglob("*")
        if file.is_file() and file.suffix in {".bin", ".safetensors", ".json", ".txt", ".model"}
        and not {"onnx", "openvino"}.intersection(file.relative_to(path).parts)
    )
    if not any(file.suffix in {".bin", ".safetensors"} for file in files):
        raise EmbeddingUnavailable("The model snapshot has no encoder weights")
    for file in files:
        digest.update(str(file.relative_to(path)).encode())
        with file.open("rb") as handle:
            for part in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(part)
    return digest.hexdigest()


class ShreckWorldEmbedder:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._model: Any = None
        self._ready = False
        self._lock = threading.RLock()
        self.backend = "sentence_transformers"
        self.fingerprint: str | None = None
        self.revision: str | None = None

    def _load_model(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            if self.settings.embedding_device.startswith("cuda") and not torch.cuda.is_available():
                raise EmbeddingUnavailable("CUDA was requested but is unavailable")
            torch.set_num_threads(self.settings.embedding_threads)
            path = model_snapshot(
                self.settings.embedding_model_id, revision=self.settings.embedding_revision,
                local_files_only=self.settings.embedding_local_files_only,
            )
            self._model = SentenceTransformer(str(path), device=self.settings.embedding_device, trust_remote_code=False)
            self.fingerprint = snapshot_fingerprint(path)
            self.revision = path.name
        return self._model

    def _validate_vector(self, row: Any) -> list[float]:
        values = [float(value) for value in row]
        if len(values) != self.settings.embedding_dimension or not all(math.isfinite(value) for value in values):
            raise EmbeddingUnavailable("Semantic encoder returned invalid vector dimensions or non-finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if abs(norm - 1) > 0.01:
            raise EmbeddingUnavailable("Semantic encoder returned a zero or non-normalized vector")
        return values

    def _encode_prepared(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        for text in texts:
            if _token_count(model.tokenizer, text) > _model_token_limit(model):
                raise EmbeddingInputError("Embedding input exceeds the tokenizer budget; silent truncation is disabled")
        try:
            encoded = model.encode(texts, batch_size=self.settings.embedding_batch_size,
                                   normalize_embeddings=True, show_progress_bar=False)
            if len(encoded) != len(texts):
                raise EmbeddingUnavailable("Semantic encoder returned the wrong number of vectors")
            return [self._validate_vector(row) for row in encoded]
        except EmbeddingInputError:
            raise
        except EmbeddingUnavailable:
            self._ready = False
            raise
        except Exception as exc:
            self._ready = False
            raise EmbeddingUnavailable(f"Semantic inference failed ({type(exc).__name__})") from exc

    def ensure_ready(self) -> dict[str, str | int | bool | None]:
        with self._lock:
            if not self._ready:
                try:
                    model = self._load_model()
                    if model.get_sentence_embedding_dimension() != self.settings.embedding_dimension:
                        raise EmbeddingUnavailable("Configured embedding dimension does not match the loaded model")
                    vectors = self._encode_prepared([
                        "query: What is the capital of France?",
                        "passage: Paris is the capital city of France.",
                        "passage: Bread is baked from flour, water, salt and yeast.",
                    ])
                    if cosine_similarity(vectors[0], vectors[1]) <= cosine_similarity(vectors[0], vectors[2]):
                        raise EmbeddingUnavailable("Semantic encoder failed the query/passage relevance self-test")
                    if not self.fingerprint:
                        raise EmbeddingUnavailable("Semantic model identity could not be verified")
                    self._ready = True
                except EmbeddingUnavailable:
                    raise
                except Exception as exc:
                    raise EmbeddingUnavailable(f"Semantic encoder is unavailable ({type(exc).__name__}); check model artifacts") from exc
            return {"ready": True, "backend": self.backend, "model": self.settings.embedding_model_id,
                    "fingerprint": self.fingerprint, "revision": self.revision,
                    "dimension": self.settings.embedding_dimension, "device": self.settings.embedding_device}

    @property
    def tokenizer(self):
        self.ensure_ready()
        return self._model.tokenizer

    @property
    def token_limit(self) -> int:
        self.ensure_ready()
        return _model_token_limit(self._model)

    def embed_batch(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        with self._lock:
            self.ensure_ready()
            prefix = "query: " if query else "passage: "
            prepared = [text.strip() if text.strip().startswith(prefix) else prefix + text.strip() for text in texts]
            return self._encode_prepared(prepared)

    def embed(self, text: str, *, query: bool = False) -> list[float]:
        return self.embed_batch([text], query=query)[0]


@lru_cache(maxsize=1)
def get_embedder() -> ShreckWorldEmbedder:
    return ShreckWorldEmbedder()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Cannot compare embedding vectors with different dimensions")
    return sum(a * b for a, b in zip(left, right))
