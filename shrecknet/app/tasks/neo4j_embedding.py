"""Neo4j embedding background task."""

from __future__ import annotations

import asyncio
from typing import Any

from app.celery_app import celery_app
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
    from app.graph.neo4j import get_driver
    from app.core.config_store import get_settings

    driver = get_driver()
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        try:
            embedding_service = EmbeddingService(session)

            # Update progress: Starting
            await update_job_progress(
                job_id, 0.1, {"status": "Fetching nodes to embed"}
            )

            # Get count of nodes that need embedding
            count_query = """
            MATCH (n)
                        WHERE any(label IN labels(n) WHERE label IN ['EntityInstance', 'Scene', 'Milestone'])
              AND toInteger(n['ontology_id']) = toInteger($ontology_id)
              AND (
                  n['is_embedded'] IS NULL OR n['is_embedded'] = false
                  OR n['last_updated_date'] > n['last_embedded_date']
              )
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
                    "ontology_id": ontology_id,
                    "nodes_processed": 0,
                    "nodes_failed": 0,
                    "nodes_skipped": 0,
                    "total_found": 0,
                    "status": "no_nodes_to_embed",
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

            await update_job_progress(
                job_id, 0.9, {"status": "Finalizing embedding results"}
            )

            return {
                "ontology_id": ontology_id,
                "nodes_processed": embed_result["nodes_processed"],
                "nodes_failed": embed_result["nodes_failed"],
                "total_found": total_to_embed,
                "processed_by_type": embed_result.get("processed_by_type", {}),
                "status": "success",
            }

        except Exception as e:
            raise Exception(f"Embedding failed: {str(e)}") from e


@celery_app.task(name="ontology.embed_instance")
def embed_instance(
    instance_id: str, author_type: str = "agent", author_id: str = "system"
) -> dict[str, Any]:
    """Embed every graph node owned by a specific ontology instance."""
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
        result = run_async(_embed_instance_impl(job_id, instance_id))
        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, "instance_id": instance_id, **result}
    except Exception as e:
        run_async(mark_job_failed(job_id, str(e)))
        raise


async def _embed_instance_impl(job_id: int, instance_id: str) -> dict[str, Any]:
    from app.core.config_store import get_settings
    from app.graph.neo4j import get_driver

    driver = get_driver()
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        embedding_service = EmbeddingService(session)

        await update_job_progress(
            job_id, 0.1, {"status": "resolving instance", "instance_id": instance_id}
        )

        instance_query = """
        MATCH (instance:OntologyInstance {instance_id: $instance_id})
        OPTIONAL MATCH (instance)-[:HAS_ENTITY]->(entity:EntityInstance)
        RETURN
            instance.ontology_id AS ontology_id,
            collect(DISTINCT entity.entity_instance_id) AS entity_ids
        LIMIT 1
        """
        result = await session.run(instance_query, instance_id=instance_id)
        record = await result.single()
        if record is None:
            raise ValueError(f"Ontology instance {instance_id} not found in Neo4j")

        ontology_id = record.get("ontology_id")
        entity_ids = [value for value in (record.get("entity_ids") or []) if value]
        node_ids = list(dict.fromkeys(entity_ids))

        await update_job_progress(
            job_id,
            0.2,
            {
                "status": "resolved instance graph",
                "instance_id": instance_id,
                "ontology_id": ontology_id,
                "nodes_requested": len(node_ids),
            },
        )

        if ontology_id is None:
            raise ValueError(f"Ontology instance {instance_id} is missing ontology_id")

        if not node_ids:
            await update_job_progress(
                job_id,
                1.0,
                {
                    "status": "no nodes to embed",
                    "instance_id": instance_id,
                    "ontology_id": ontology_id,
                },
            )
            return {
                "instance_id": instance_id,
                "ontology_id": ontology_id,
                "nodes_requested": 0,
                "nodes_embedded": 0,
                "nodes_failed": 0,
                "nodes_skipped": 0,
                "missing_nodes": [],
                "status": "no_nodes_to_embed",
            }

        processed = 0
        failed = 0
        total = len(node_ids)
        missing_nodes: list[str] = []

        for idx, node_id in enumerate(node_ids, start=1):
            progress = 0.2 + 0.75 * (idx / total)
            try:
                await embedding_service.embed_node(node_id, ontology_id)
                processed += 1
            except ValueError:
                missing_nodes.append(node_id)
            except Exception as exc:  # pragma: no cover - defensive tracking
                failed += 1
                await update_job_progress(
                    job_id,
                    progress,
                    {
                        "status": "embedding instance nodes",
                        "instance_id": instance_id,
                        "current_node": node_id,
                        "nodes_completed": processed,
                        "nodes_failed": failed,
                        "error": str(exc),
                    },
                )
                continue

            await update_job_progress(
                job_id,
                progress,
                {
                    "status": "embedding instance nodes",
                    "instance_id": instance_id,
                    "current_node": node_id,
                    "nodes_completed": processed,
                    "nodes_failed": failed,
                },
            )

        await update_job_progress(
            job_id,
            1.0,
            {
                "status": "complete",
                "instance_id": instance_id,
                "ontology_id": ontology_id,
                "nodes_completed": processed,
                "nodes_failed": failed,
                "nodes_skipped": len(missing_nodes),
            },
        )

        return {
            "instance_id": instance_id,
            "ontology_id": ontology_id,
            "nodes_requested": len(node_ids),
            "nodes_embedded": processed,
            "nodes_failed": failed,
            "nodes_skipped": len(missing_nodes),
            "missing_nodes": sorted(missing_nodes),
            "status": "success" if processed > 0 and failed == 0 else "partial_success",
        }


@celery_app.task(name="ontology.embed_nodes")
def embed_nodes(
    ontology_id: int,
    node_ids: list[str],
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """
    Embed specific nodes within an ontology.

    Args:
        ontology_id: The ontology that owns the nodes
        node_ids: EntityInstance identifiers to embed
        author_type: Who initiated the embedding
        author_id: Identifier of the author

    Returns:
        Dictionary with embedding results
    """
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.NEO4J_EMBEDDING,
            description=f"Embedding {len(node_ids)} nodes for ontology {ontology_id}",
            celery_task_id=embed_nodes.request.id,
            details={"ontology_id": ontology_id, "node_ids": node_ids},
            ontology_id=ontology_id,
        )
    )

    try:
        run_async(mark_job_running(job_id))
        result = run_async(_embed_nodes_impl(job_id, ontology_id, node_ids))
        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, "ontology_id": ontology_id, **result}
    except Exception as e:
        run_async(mark_job_failed(job_id, str(e)))
        raise


async def _embed_nodes_impl(
    job_id: int, ontology_id: int, node_ids: list[str]
) -> dict[str, Any]:
    """
    Embed only the provided node identifiers.

    Args:
        job_id: Background job identifier
        ontology_id: Ontology identifier for filtering
        node_ids: Requested EntityInstance ids

    Returns:
        Summary of the embedding run
    """
    from app.graph.neo4j import get_driver
    from app.core.config_store import get_settings

    # Normalize identifiers and drop duplicates/empty values
    normalized: list[str] = []
    seen: set[str] = set()
    for node_id in node_ids or []:
        if not node_id:
            continue
        cleaned = node_id.strip()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)

    if not normalized:
        await update_job_progress(
            job_id, 1.0, {"status": "no nodes to embed", "nodes_embedded": 0}
        )
        return {
            "ontology_id": ontology_id,
            "nodes_requested": 0,
            "nodes_embedded": 0,
            "nodes_failed": 0,
            "nodes_skipped": 0,
            "missing_nodes": [],
        }

    driver = get_driver()
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        embedding_service = EmbeddingService(session)

        await update_job_progress(
            job_id,
            0.1,
            {
                "status": "validating nodes",
                "nodes_requested": len(normalized),
            },
        )

        validation_query = """
        UNWIND $ids AS node_id
        MATCH (node:EntityInstance {entity_instance_id: node_id})
        WHERE node.ontology_id = $ontology_id
        RETURN node.entity_instance_id AS entity_id
        """
        result = await session.run(
            validation_query, ids=normalized, ontology_id=ontology_id
        )
        rows = await result.data()
        valid_ids = [row["entity_id"] for row in rows if row.get("entity_id")]
        valid_set = set(valid_ids)
        missing = sorted(set(normalized) - valid_set)

        if not valid_ids:
            await update_job_progress(
                job_id,
                1.0,
                {
                    "status": "no matching nodes",
                    "nodes_requested": len(normalized),
                    "nodes_skipped": len(missing),
                },
            )
            return {
                "ontology_id": ontology_id,
                "nodes_requested": len(normalized),
                "nodes_embedded": 0,
                "nodes_failed": 0,
                "nodes_skipped": len(missing),
                "missing_nodes": missing,
            }

        processed = 0
        failed = 0
        total = len(valid_ids)

        for idx, node_id in enumerate(valid_ids, start=1):
            progress = 0.2 + 0.75 * (idx / total)
            try:
                await embedding_service.embed_node(node_id, ontology_id)
                processed += 1
            except Exception as exc:  # pragma: no cover - just tracking failures
                failed += 1
                await update_job_progress(
                    job_id,
                    progress,
                    {
                        "status": "embedding nodes",
                        "nodes_completed": processed,
                        "nodes_failed": failed,
                        "current_node": node_id,
                        "error": str(exc),
                    },
                )
                continue

            await update_job_progress(
                job_id,
                progress,
                {
                    "status": "embedding nodes",
                    "nodes_completed": processed,
                    "nodes_failed": failed,
                    "current_node": node_id,
                },
            )

        await update_job_progress(
            job_id,
            1.0,
            {
                "status": "complete",
                "nodes_completed": processed,
                "nodes_failed": failed,
                "nodes_skipped": len(missing),
            },
        )

        return {
            "ontology_id": ontology_id,
            "nodes_requested": len(normalized),
            "nodes_embedded": processed,
            "nodes_failed": failed,
            "nodes_skipped": len(missing),
            "missing_nodes": missing,
        }
