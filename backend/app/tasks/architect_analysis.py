from __future__ import annotations

import logging
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.graph.neo4j import get_driver
from app.integrations.llm.model_policy import ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.jobs.architect.architect_v2 import ArchitectOrchestratorV2
from app.models.architect import ArchitectRunStatus
from app.models.background_job import AuthorType, JobType
from app.repositories.agent_repository import AgentRepository
from app.repositories.architect_repository import ArchitectRepository
from app.repositories.ontology_repository import OntologyRepository
from app.services.ontology_instance_service import OntologyInstanceService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)
from app.db.session import AsyncSessionMaker

logger = logging.getLogger(__name__)


@celery_app.task(name="architect.analyze_instance")
def analyze_instance(
    run_id: str,
    agent_id: str,
    request_payload: dict[str, Any],
    *,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Background job entry point for the Architect step-one workflow."""

    description = (
        "Architect analysis for agent "
        f"{agent_id} on instance {request_payload.get('ontology_instance_id')}"
    )
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.ARCHITECT_ANALYSIS,
            description=description,
            celery_task_id=analyze_instance.request.id,
            details={
                "run_id": run_id,
                "agent_id": agent_id,
                "ontology_instance_id": request_payload.get("ontology_instance_id"),
            },
        )
    )

    try:
        run_async(mark_job_running(job_id))
        run_async(_attach_job_to_run(run_id, job_id))
        run_async(
            update_job_progress(
                job_id, 0.05, {"status": "Preparing architect analysis"}
            )
        )

        result = run_async(
            _execute_architect_pipeline(
                run_id=run_id,
                agent_id=agent_id,
                request_payload=request_payload,
                job_id=job_id,
            )
        )

        run_async(
            mark_job_done(
                job_id,
                {
                    "run_id": run_id,
                    "proposal_count": len(result.get("proposals", [])),
                    "chunk_count": result.get("chunk_count", 0),
                    "status": "completed",
                },
            )
        )
        return {"job_id": job_id, "status": "success", "run_id": run_id}

    except Exception as exc:
        logger.error(
            "Architect analysis failed for run %s: %s", run_id, exc, exc_info=True
        )
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _attach_job_to_run(run_id: str, job_id: int) -> None:
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        await repo.attach_background_job(run_id, job_id)
        await session.commit()


async def _execute_architect_pipeline(
    *,
    run_id: str,
    agent_id: str,
    request_payload: dict[str, Any],
    job_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    async with AsyncSessionMaker() as session:
        repo = ArchitectRepository(session)
        agent_repo = AgentRepository(session)
        agent = await agent_repo.get_by_id(agent_id)
        if not agent:
            raise ValueError("Agent not found")

        run = await repo.get_run(run_id, with_proposals=False)
        if not run:
            raise ValueError("Architect analysis run not found")

        await repo.update_run_status(run_id, status=ArchitectRunStatus.RUNNING)
        await session.commit()

        ontology_instance_id = request_payload["ontology_instance_id"]
        chunk_size = request_payload.get("chunk_size")
        max_chunks = request_payload.get("max_chunks")

        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as graph_session:
            instance_service = OntologyInstanceService(session, graph_session)
            await update_job_progress(job_id, 0.15, {"status": "Loading instance"})

            ontology_instance = await instance_service.get_instance(
                ontology_instance_id
            )
            ontology_id = ontology_instance.ontology_id
            run.ontology_id = ontology_id
            await session.flush()

            onto_repo = OntologyRepository(session)
            entity_defs = await onto_repo.list_entities(ontology_id)
            entity_catalog = [
                {
                    "id": entity.id,
                    "name": entity.name,
                    "description": entity.description,
                }
                for entity in entity_defs
                if entity.auto_generatable
            ]

            model_policy = ModelPolicy(
                decompose_model=settings.model_decompose,
                subanswer_model=settings.model_subanswer,
                synthesis_model=settings.model_synthesis,
                validation_model=settings.model_validation,
                style_model=settings.model_style,
                architect_extract_model=getattr(
                    settings, "model_architect_extract", settings.model_decompose
                ),
            )
            llm_client = OpenAIClient(
                api_key=settings.openai_api_key,
                timeout=60,
                max_retries=3,
            )
            retriever = Neo4jGraphRetriever(graph_session)
            try:
                # Use the new V2 orchestrator for improved efficiency
                orchestrator = ArchitectOrchestratorV2(
                    llm_client=llm_client,
                    model_policy=model_policy,
                    graph_retriever=retriever,
                )

                await update_job_progress(
                    job_id, 0.25, {"status": "Chunking story text"}
                )

                result = await orchestrator.analyse(
                    agent_ontology_ids=[ont.id for ont in agent.ontologies],
                    ontology_instance=ontology_instance,
                    entity_definitions=entity_catalog,
                    override_chunk_size=chunk_size,
                    override_max_chunks=max_chunks,
                )
            finally:
                await llm_client.aclose()

        await repo.insert_proposals(run_id, result["proposals"])
        await repo.update_run_status(
            run_id,
            status=ArchitectRunStatus.COMPLETED,
            input_chunk_count=result.get("chunk_count"),
        )
        await session.commit()

        await update_job_progress(
            job_id,
            0.95,
            {
                "status": "Completed",
                "proposal_count": len(result.get("proposals", [])),
            },
        )

        return result
