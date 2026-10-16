"""Prepare verified local E5, reranker, and Docling caches before serving work."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from huggingface_hub import snapshot_download

from app.core.config import get_settings
from app.services.embedding import get_embedder


def main() -> None:
    settings = get_settings()
    cache = Path(os.environ.get("HF_HUB_CACHE", os.environ.get("HF_HOME", "./models/hub")))
    cache.mkdir(parents=True, exist_ok=True)
    for model_id, revision in [(settings.embedding_model_id, settings.embedding_revision),
                               (settings.reranker_model_id, None)] if settings.reranker_enabled else [(settings.embedding_model_id, settings.embedding_revision)]:
        if not Path(model_id).is_dir():
            snapshot_download(repo_id=model_id, revision=revision, cache_dir=str(cache),
                allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors", "*.bin"],
                ignore_patterns=["onnx/*", "openvino/*"])
    # The runtime deliberately resolves local snapshots only after prefetch.
    print(get_embedder().ensure_ready(), flush=True)
    subprocess.run(["docling-tools", "models", "download", "layout", "tableformer", "-o", str(settings.docling_artifacts_path)], check=True)
    if os.geteuid() == 0:
        uid = int(os.environ.get("SHRECKNET_CONTAINER_UID", "1000"))
        gid = int(os.environ.get("SHRECKNET_CONTAINER_GID", "1000"))
        for root in {cache, settings.docling_artifacts_path}:
            for path in [root, *root.rglob("*")]:
                if path.exists():
                    os.chown(path, uid, gid)


if __name__ == "__main__":
    main()
