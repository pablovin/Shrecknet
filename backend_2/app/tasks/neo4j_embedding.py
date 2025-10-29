"""Neo4j embedding background task."""

from __future__ import annotations

import asyncio
from typing import Any

from app.celery_app import celery_app
from app.models.background_job import AuthorType, JobType
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)


@celery_app.task(name="ontology.embed_instance")
def embed_instance(
    instance_id: str, author_type: str = "agent", author_id: str = "system"
) -> dict[str, Any]:
    """
    Embed an ontology instance in Neo4j.

    This is a placeholder task for future Neo4j embedding functionality.

    Args:
        instance_id: The ontology instance ID
        author_type: Type of author triggering the job (user or agent)
        author_id: ID of the author

    Returns:
        Dictionary with job results
    """
    job_id = asyncio.run(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.NEO4J_EMBEDDING,
            description=f"Embedding ontology instance {instance_id}",
            celery_task_id=embed_instance.request.id,
            details={"instance_id": instance_id},
        )
    )

    try:
        asyncio.run(mark_job_running(job_id))

        # Placeholder for actual embedding logic
        # This would integrate with app/graphrag/embedding_service.py
        asyncio.run(
            update_job_progress(job_id, 0.5, {"status": "embedding in progress"})
        )

        # TODO: Implement actual Neo4j embedding logic here
        # For now, just simulate completion
        asyncio.run(
            update_job_progress(
                job_id, 0.9, {"status": "embedding completed (placeholder)"}
            )
        )

        asyncio.run(
            mark_job_done(
                job_id, {"instance_id": instance_id, "status": "success (placeholder)"}
            )
        )
        return {"job_id": job_id, "instance_id": instance_id, "status": "success"}
    except Exception as e:
        asyncio.run(mark_job_failed(job_id, str(e)))
        raise
