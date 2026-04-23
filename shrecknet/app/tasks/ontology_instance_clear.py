from __future__ import annotations

from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.models.background_job import AuthorType, JobType
from app.services.ontology_instance_service import OntologyInstanceService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)


@celery_app.task(name="ontology.clear_instance_content_by_entity_types")
def clear_instance_content_by_entity_types(
    *,
    ontology_id: int,
    entity_definition_ids: list[int] | None = None,
    entity_type_names: list[str] | None = None,
    author_type: str = "user",
    author_id: str = "system",
    job_id: int | None = None,
) -> dict[str, Any]:
    definition_ids = [int(value) for value in (entity_definition_ids or [])]
    type_names = [str(value) for value in (entity_type_names or [])]

    effective_job_id = job_id
    if effective_job_id is None:
        effective_job_id = run_async(
            create_background_job(
                author_type=AuthorType(author_type),
                author_id=author_id,
                job_type=JobType.ONTOLOGY_INSTANCE_ENTITY_TYPE_CLEAR,
                description=(
                    f"Clearing ontology {ontology_id} content for selected entity types"
                ),
                celery_task_id=clear_instance_content_by_entity_types.request.id,
                details={
                    "ontology_id": ontology_id,
                    "entity_definition_ids": definition_ids,
                    "entity_type_names": type_names,
                },
                ontology_id=ontology_id,
            )
        )

    try:
        run_async(mark_job_running(effective_job_id))
        run_async(
            update_job_progress(
                effective_job_id,
                0.1,
                {
                    "status": "starting",
                    "ontology_id": ontology_id,
                    "entity_definition_ids": definition_ids,
                    "entity_type_names": type_names,
                },
            )
        )
        result = run_async(
            _clear_impl(
                ontology_id=ontology_id,
                entity_definition_ids=definition_ids,
                entity_type_names=type_names,
                job_id=effective_job_id,
            )
        )
        run_async(mark_job_done(effective_job_id, {"status": "done", "result": result}))
        return {
            "job_id": effective_job_id,
            "status": "success",
            "result": result,
        }
    except Exception as exc:
        run_async(mark_job_failed(effective_job_id, str(exc)))
        raise


async def _clear_impl(
    *,
    ontology_id: int,
    entity_definition_ids: list[int],
    entity_type_names: list[str],
    job_id: int,
) -> dict[str, Any]:
    from app.db.session import AsyncSessionMaker
    from app.graph.neo4j import get_driver

    settings = get_settings()
    driver = get_driver()

    await update_job_progress(job_id, 0.25, {"status": "preparing graph transaction"})

    async with AsyncSessionMaker() as sql_session:
        async with driver.session(database=settings.neo4j_database) as graph_session:
            service = OntologyInstanceService(sql_session, graph_session)
            await update_job_progress(
                job_id,
                0.6,
                {
                    "status": "deleting selected entity-type content",
                    "ontology_id": ontology_id,
                },
            )
            clear_result = await service.clear_instance_content_by_entity_types(
                ontology_id=ontology_id,
                entity_definition_ids=entity_definition_ids,
                entity_type_names=entity_type_names,
            )

    await update_job_progress(
        job_id,
        0.95,
        {
            "status": "finalizing",
            "entities_deleted": clear_result.get("entities_deleted", 0),
            "chunks_deleted": clear_result.get("chunks_deleted", 0),
        },
    )
    return clear_result


@celery_app.task(name="ontology.clear_timeline_events_and_orphans")
def clear_timeline_events_and_orphans(
    *,
    ontology_id: int,
    author_type: str = "user",
    author_id: str = "system",
    job_id: int | None = None,
) -> dict[str, Any]:
    effective_job_id = job_id
    if effective_job_id is None:
        effective_job_id = run_async(
            create_background_job(
                author_type=AuthorType(author_type),
                author_id=author_id,
                job_type=JobType.ONTOLOGY_INSTANCE_TIMELINE_CLEAR,
                description=(
                    f"Clearing timeline events/orphans for ontology {ontology_id}"
                ),
                celery_task_id=clear_timeline_events_and_orphans.request.id,
                details={
                    "ontology_id": ontology_id,
                },
                ontology_id=ontology_id,
            )
        )

    try:
        run_async(mark_job_running(effective_job_id))
        run_async(
            update_job_progress(
                effective_job_id,
                0.1,
                {
                    "status": "starting",
                    "ontology_id": ontology_id,
                },
            )
        )
        result = run_async(
            _clear_timeline_events_impl(
                ontology_id=ontology_id,
                job_id=effective_job_id,
            )
        )
        run_async(mark_job_done(effective_job_id, {"status": "done", "result": result}))
        return {
            "job_id": effective_job_id,
            "status": "success",
            "result": result,
        }
    except Exception as exc:
        run_async(mark_job_failed(effective_job_id, str(exc)))
        raise


async def _clear_timeline_events_impl(
    *,
    ontology_id: int,
    job_id: int,
) -> dict[str, Any]:
    from app.db.session import AsyncSessionMaker
    from app.graph.neo4j import get_driver

    settings = get_settings()
    driver = get_driver()

    await update_job_progress(job_id, 0.25, {"status": "preparing graph transaction"})

    async with AsyncSessionMaker() as sql_session:
        async with driver.session(database=settings.neo4j_database) as graph_session:
            service = OntologyInstanceService(sql_session, graph_session)
            await update_job_progress(
                job_id,
                0.6,
                {
                    "status": "clearing timeline events, scenes, and milestones",
                    "ontology_id": ontology_id,
                },
            )
            clear_result = await service.clear_timeline_events_and_orphans(
                ontology_id=ontology_id,
            )

    await update_job_progress(
        job_id,
        0.95,
        {
            "status": "finalizing",
            "legacy_events_deleted": clear_result.get("legacy_events_deleted", 0),
            "scenes_deleted": clear_result.get("scenes_deleted", 0),
            "milestones_deleted": clear_result.get("milestones_deleted", 0),
        },
    )
    return clear_result
