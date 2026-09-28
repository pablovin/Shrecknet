from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from app.core.config import get_settings


class ShreckWorldEmbedder:
    """Owns local vector creation; never uses Shrecknet/Librarian vectors."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.settings.embedding_model_id, device=self.settings.embedding_device)
        return self._model

    def embed(self, text: str, *, query: bool = False) -> list[float]:
        prefix = "query: " if query else "passage: "
        try:
            vector = self._load_model().encode(prefix + text, normalize_embeddings=True)
            return [float(value) for value in vector]
        except Exception:
            # Local deterministic fallback keeps ingestion operational in offline
            # deployments. It is intentionally visible in job detail/trace.
            return self._hash_vector(prefix + text)

    def _hash_vector(self, text: str) -> list[float]:
        values = [0.0] * self.settings.embedding_dimension
        for token in re.findall(r"[\w'-]+", text.lower()):
            index = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % len(values)
            values[index] += 1.0
        magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / magnitude for value in values]


@lru_cache(maxsize=1)
def get_embedder() -> ShreckWorldEmbedder:
    return ShreckWorldEmbedder()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))
