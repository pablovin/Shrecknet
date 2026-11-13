"""Prepare verified local E5, reranker, and Docling caches before serving work."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from huggingface_hub.errors import LocalEntryNotFoundError
from huggingface_hub import snapshot_download

from app.core.config import get_settings
from app.services.embedding import get_embedder, prepare_embedding_backend


def _ensure_snapshot(model_id: str, revision: str | None, cache: Path) -> str:
    options = {
        "repo_id": model_id,
        "revision": revision,
        "cache_dir": str(cache),
        "allow_patterns": ["*.json", "*.txt", "*.model", "*.safetensors", "*.bin"],
        "ignore_patterns": ["onnx/*", "openvino/*"],
    }
    try:
        return snapshot_download(**options, local_files_only=True)
    except LocalEntryNotFoundError:
        return snapshot_download(**options)


def main() -> None:
    settings = get_settings()
    cache = Path(os.environ.get("HF_HUB_CACHE", os.environ.get("HF_HOME", "./models/hub")))
    cache.mkdir(parents=True, exist_ok=True)
    for model_id, revision in [(settings.embedding_model_id, settings.embedding_revision),
                               (settings.reranker_model_id, None)] if settings.reranker_enabled else [(settings.embedding_model_id, settings.embedding_revision)]:
        _ensure_snapshot(model_id, revision, cache)
    # The runtime deliberately resolves local snapshots only after prefetch.
    prepare_embedding_backend(settings)
    print(get_embedder().ensure_ready(), flush=True)
    subprocess.run(["docling-tools", "models", "download", "layout", "tableformer", "-o", str(settings.docling_artifacts_path)], check=True)
if __name__ == "__main__":
    main()
