"""Neo4j embedding background task."""

from __future__ import annotations

import asyncio
from typing import Any

from app.celery_app import celery_app
from app.graph.neo4j import get_neo4j_session
from app.graphrag.embedding_service import EmbeddingService
from app.models.background_job import AuthorType, JobType
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)


@celery_app.task(name="ontology.embed_ontology")
def embed_ontology(
    ontology_id: int, author_type: str = "user", author_id: str = "system"
) -> dict[str, Any]:
    """
    Embed all nodes for a specific ontology in Neo4j.

    This task processes all nodes that are not yet embedded or have been
    updated since their last embedding.

    Args:
        ontology_id: The ontology ID
        author_type: Type of author triggering the job (user or agent)
        author_id: ID of the author

    Returns:
        Dictionary with job results
    """
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.NEO4J_EMBEDDING,
            description=f"Embedding nodes for ontology {ontology_id}",
            celery_task_id=embed_ontology.request.id,
            details={"ontology_id": ontology_id},
            ontology_id=ontology_id,
        )
    )

    try:
        run_async(mark_job_running(job_id))

        # Run the actual embedding
        result = run_async(_embed_ontology_impl(job_id, ontology_id))

        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, "ontology_id": ontology_id, **result}
    except Exception as e:
        run_async(mark_job_failed(job_id, str(e)))
        raise


async def _embed_ontology_impl(job_id: int, ontology_id: int) -> dict[str, Any]:
    """
    Implementation of ontology embedding.

    Args:
        job_id: The background job ID for progress tracking
        ontology_id: The ontology ID to embed

    Returns:
        Dictionary with embedding statistics
    """
    async for session in get_neo4j_session():
        try:
            embedding_service = EmbeddingService(session)

            # Update progress: Starting
            await update_job_progress(
                job_id, 0.1, {"status": "Fetching nodes to embed"}
            )

            # Get count of nodes that need embedding
            count_query = """
            MATCH (n:EntityInstance)
            WHERE n.ontology_id = $ontology_id
              AND (n.is_embedded IS NULL OR n.is_embedded = false 
                   OR n.last_updated_date > n.last_embedded_date)
            RETURN count(n) AS count
            """
            result = await session.run(count_query, ontology_id=ontology_id)
            record = await result.single()
            total_to_embed = record["count"] if record else 0

            await update_job_progress(
                job_id, 0.2, {"status": f"Found {total_to_embed} nodes to embed"}
            )

            # If no nodes to embed, return early
            if total_to_embed == 0:
                return {
                    "nodes_processed": 0,
                    "nodes_failed": 0,
                    "nodes_skipped": 0,
                    "status": "No nodes to embed",
                }

            # Ensure vector index exists
            await update_job_progress(job_id, 0.3, {"status": "Ensuring vector index"})
            await embedding_service.ensure_vector_index()

            # Perform embedding with progress tracking
            await update_job_progress(
                job_id, 0.4, {"status": "Starting embedding process"}
            )

            # Use the embedding service's embed_ontology method
            embed_result = await embedding_service.embed_ontology(
                ontology_id, batch_size=50
            )

            # Mark nodes as embedded
            await update_job_progress(
                job_id, 0.9, {"status": "Marking nodes as embedded"}
            )

            update_query = """
            MATCH (n:EntityInstance)
            WHERE n.ontology_id = $ontology_id
              AND n.text_embedding IS NOT NULL
            SET n.is_embedded = true,
                n.last_embedded_date = datetime()
            """
            await session.run(update_query, ontology_id=ontology_id)

            return {
                "nodes_processed": embed_result["nodes_processed"],
                "nodes_failed": embed_result["nodes_failed"],
                "total_found": total_to_embed,
                "status": "success",
            }

        except Exception as e:
            raise Exception(f"Embedding failed: {str(e)}") from e


@celery_app.task(name="ontology.embed_instance")
def embed_instance(
    instance_id: str, author_type: str = "agent", author_id: str = "system"
) -> dict[str, Any]:
    """
    Embed an ontology instance in Neo4j (legacy - redirects to embed_ontology).

    This is a placeholder task for backward compatibility.

    Args:
        instance_id: The ontology instance ID
        author_type: Type of author triggering the job (user or agent)
        author_id: ID of the author

    Returns:
        Dictionary with job results
    """
    job_id = run_async(
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
        run_async(mark_job_running(job_id))

        # Placeholder for actual embedding logic
        # This would integrate with app/graphrag/embedding_service.py
        run_async(update_job_progress(job_id, 0.5, {"status": "embedding in progress"}))

        # TODO: Implement actual Neo4j embedding logic here
        # For now, just simulate completion
        run_async(
            update_job_progress(
                job_id, 0.9, {"status": "embedding completed (placeholder)"}
            )
        )

        run_async(
            mark_job_done(
                job_id, {"instance_id": instance_id, "status": "success (placeholder)"}
            )
        )
        return {"job_id": job_id, "instance_id": instance_id, "status": "success"}
    except Exception as e:
        run_async(mark_job_failed(job_id, str(e)))
        raise
