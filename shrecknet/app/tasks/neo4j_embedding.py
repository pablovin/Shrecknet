"""Semantic V2 embedding background tasks."""

from __future__ import annotations

from typing import Any

from app.celery_app import celery_app
from app.graphrag.semantic_v2 import SemanticEmbeddingService
from app.models.background_job import AuthorType, JobType
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)


def _run_tracked(task, *, description: str, details: dict[str, Any], ontology_id: int | None, author_type: str, author_id: str, runner):
    job_id = run_async(create_background_job(
        author_type=AuthorType(author_type), author_id=author_id,
        job_type=JobType.NEO4J_EMBEDDING, description=description,
        celery_task_id=task.request.id, details=details, ontology_id=ontology_id,
    ))
    try:
        run_async(mark_job_running(job_id))
        result = run_async(runner(job_id))
        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, **result}
    except Exception as exc:
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _with_service(callback):
    from app.core.config_store import get_settings
    from app.db.session import get_sessionmaker
    from app.graph.neo4j import get_driver

    sql_session = get_sessionmaker()()
    try:
        async with get_driver().session(database=get_settings().neo4j_database) as graph_session:
            return await callback(SemanticEmbeddingService(graph_session, sql_session))
    finally:
        sql_session.close()


@celery_app.task(name="ontology.embed_ontology")
def embed_ontology(ontology_id: int, author_type: str = "user", author_id: str = "system") -> dict[str, Any]:
    return _run_tracked(
        embed_ontology,
        description=f"Embedding nodes for ontology {ontology_id}",
        details={"ontology_id": ontology_id}, ontology_id=ontology_id,
        author_type=author_type, author_id=author_id,
        runner=lambda job_id: _embed_ontology_impl(job_id, ontology_id),
    )


async def _embed_ontology_impl(job_id: int, ontology_id: int) -> dict[str, Any]:
    async def run(service):
        await update_job_progress(job_id, 0.1, {"status": "rendering semantic documents"})
        result = await service.embed_ontology(ontology_id, batch_size=50)
        await update_job_progress(job_id, 1.0, {"status": "semantic embedding complete", **result})
        return result
    return await _with_service(run)


@celery_app.task(name="ontology.embed_instance")
def embed_instance(instance_id: str, author_type: str = "agent", author_id: str = "system") -> dict[str, Any]:
    return _run_tracked(
        embed_instance,
        description=f"Embedding ontology instance {instance_id}",
        details={"instance_id": instance_id}, ontology_id=None,
        author_type=author_type, author_id=author_id,
        runner=lambda job_id: _embed_instance_impl(job_id, instance_id),
    )


async def _embed_instance_impl(job_id: int, instance_id: str) -> dict[str, Any]:
    async def run(service):
        await update_job_progress(job_id, 0.1, {"status": "reconciling instance documents"})
        result = await service.embed_instance(instance_id)
        await update_job_progress(job_id, 1.0, {"status": "complete", **result})
        return result
    return await _with_service(run)


@celery_app.task(name="ontology.embed_nodes")
def embed_nodes(ontology_id: int, node_ids: list[str], author_type: str = "user", author_id: str = "system") -> dict[str, Any]:
    return _run_tracked(
        embed_nodes,
        description=f"Embedding {len(node_ids)} nodes for ontology {ontology_id}",
        details={"ontology_id": ontology_id, "node_ids": node_ids}, ontology_id=ontology_id,
        author_type=author_type, author_id=author_id,
        runner=lambda job_id: _embed_nodes_impl(job_id, ontology_id, node_ids),
    )


async def _embed_nodes_impl(job_id: int, ontology_id: int, node_ids: list[str]) -> dict[str, Any]:
    normalized = list(dict.fromkeys(value.strip() for value in (node_ids or []) if value and value.strip()))
    if not normalized:
        result = {"ontology_id": ontology_id, "nodes_requested": 0, "nodes_embedded": 0,
                  "nodes_failed": 0, "nodes_skipped": 0, "missing_nodes": []}
        await update_job_progress(job_id, 1.0, {"status": "no nodes to embed", **result})
        return result

    async def run(service):
        await update_job_progress(job_id, 0.1, {"status": "reconciling targeted documents", "nodes_requested": len(normalized)})
        result = await service.embed_nodes(ontology_id, normalized)
        await update_job_progress(job_id, 1.0, {"status": "complete", **result})
        return result
    return await _with_service(run)


@celery_app.task(name="ontology.embed_definitions")
def embed_definitions(ontology_id: int, definition_ids: list[int], author_type: str = "agent", author_id: str = "ontology-definition-write") -> dict[str, Any]:
    return run_async(_embed_definitions_impl(ontology_id, definition_ids))


async def _embed_definitions_impl(ontology_id: int, definition_ids: list[int]) -> dict[str, Any]:
    async def run(service):
        await service.ensure_indexes()
        return await service.embed_definitions(ontology_id, definition_ids)
    return await _with_service(run)


@celery_app.task(name="ontology.embed_reconciliation")
def embed_reconciliation(
    ontology_id: int, instance_id: str | None = None, node_ids: list[str] | None = None,
    author_type: str = "agent", author_id: str = "system",
) -> dict[str, Any]:
    node_ids = node_ids or []
    job_id = run_async(create_background_job(
        author_type=AuthorType(author_type), author_id=author_id,
        job_type=JobType.NEO4J_EMBEDDING, description="Embedding reconciliation",
        celery_task_id=embed_reconciliation.request.id,
        details={"ontology_id": ontology_id, "instance_id": instance_id, "node_ids_count": len(node_ids)},
        ontology_id=ontology_id,
    ))
    try:
        run_async(mark_job_running(job_id))
        nodes_result = run_async(_embed_nodes_impl(job_id, ontology_id, node_ids)) if node_ids else {"nodes_requested": 0}
        instance_result = run_async(_embed_instance_impl(job_id, instance_id)) if instance_id else {"status": "skipped"}
        result = {
            "ontology_id": ontology_id, "instance_id": instance_id,
            "node_ids_count": len(node_ids), "embed_nodes": nodes_result,
            "embed_instance": instance_result,
            "queue_metrics": {"jobs_enqueued": 1, "jobs_coalesced": 1,
                              "avg_nodes_per_job": float(len(node_ids)), "fanout_per_request": 1.0},
        }
        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, **result}
    except Exception as exc:
        run_async(mark_job_failed(job_id, str(exc)))
        raise
