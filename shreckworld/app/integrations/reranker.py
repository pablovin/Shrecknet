"""Optional local cross-encoder refinement; embeddings never fall back to hashes."""
import math
import threading
from functools import lru_cache

from app.core.config import get_settings
from app.services.embedding import model_snapshot


class LocalReranker:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def rerank(self, query: str, candidates: list[dict]) -> tuple[list[dict], bool]:
        settings = get_settings()
        if not settings.reranker_enabled or not candidates:
            return candidates, False
        window = candidates[:25]
        try:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import CrossEncoder
                    path = model_snapshot(settings.reranker_model_id, local_files_only=settings.embedding_local_files_only)
                    self._model = CrossEncoder(str(path), device=settings.embedding_device, trust_remote_code=False,
                                               automodel_args={"low_cpu_mem_usage": False})
                pairs = [(query, "\n".join(filter(None, [candidate["props"].get("book_title"),
                    candidate["props"].get("heading_path_text"), candidate["props"].get("display_text")]))) for candidate in window]
                scores = self._model.predict(pairs, show_progress_bar=False)
            if len(scores) != len(window) or not all(math.isfinite(float(score)) for score in scores):
                return candidates, True
            for candidate, score in zip(window, scores):
                candidate["rerank_score"] = float(score)
            window.sort(key=lambda value: (-value["rerank_score"], -value["rrf_score"], int(value["props"].get("authority_priority", 100)), value["chunk_id"]))
            return window + candidates[25:], False
        except Exception:
            # This fallback affects ranking only. It retains the verified E5
            # vectors and the shared deterministic RRF ordering.
            return candidates, True


@lru_cache(maxsize=1)
def get_reranker() -> LocalReranker:
    return LocalReranker()
