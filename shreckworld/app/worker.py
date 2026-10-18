"""Start a single inference worker only after its semantic model passes checks."""
from app.celery_app import celery_app
from app.core.config import get_settings
from app.services.embedding import get_embedder


def main() -> None:
    print({"worker_embedding_readiness": get_embedder().ensure_ready()}, flush=True)
    if not get_settings().docling_artifacts_path.is_dir():
        raise RuntimeError("Docling artifacts are missing; run the model-prefetch service first")
    # Threads avoid forking a prewarmed PyTorch model. One task at a time bounds
    # memory; local model inference and Docling use their configured CPU threads.
    celery_app.worker_main([
        "worker", "--pool=threads", "--concurrency=1", "--loglevel=info",
        "--queues=shreckworld_embedding,shreckworld_agent",
    ])


if __name__ == "__main__":
    main()
