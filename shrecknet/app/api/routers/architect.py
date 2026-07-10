"""API router for Architect job orchestration."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_architect_service,
    get_current_user,
    get_db_session,
)
from app.api.agent_feature_gate import require_ai_agents_enabled
from app.core.config_store import get_settings, is_shreckllm_configured
from app.models.agent import Agent
from app.db.jobs_session import JobsSessionMaker
from app.repositories.background_job_repository import BackgroundJobRepository
from app.models.architect import ArchitectProposal, ArchitectProposalStatus, ArchitectProposalType
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.architect import (
    ArchitectAnalysisRequest,
    ArchitectGenerationRequest,
    ArchitectAnalysisRunRead,
    ArchitectAnalysisRunSummary,
    ArchitectProposalRead,
    ArchitectProposalStatusUpdate,
)
from app.services.architect_service import ArchitectService
from app.tasks.architect_analysis import analyze_instance as architect_task

router = APIRouter(prefix="/jobs/architect", tags=["architect"])


class ArchitectProposalCreateRequest(ArchitectProposalRead):
    id: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None


class ArchitectProposalUpdateRequest(BaseModel):
    proposal_type: str | None = None
    status: str | None = None
    entity_definition_id: int | None = None
    entity_instance_id: str | None = None
    alias: str | None = None
    confidence: float | None = None
    justification: str | None = None
    evidence: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = Field(default=None, alias="proposal_metadata")
    chunks: list[str] | None = None
    merged_into_proposal_id: str | None = None
    corrected_alias: str | None = None
    corrected_entity_definition_id: int | None = None
    corrected_proposal_type: str | None = None
    corrected_entity_instance_id: str | None = None
    generated_entity_instance_id: str | None = None


def _serialize_proposal(proposal: ArchitectProposal) -> ArchitectProposalRead:
    return ArchitectProposalRead.model_validate(proposal)


async def _get_architect_agent_or_404(
    agent_id: str,
    session: AsyncSession,
) -> Agent:
    agent_repo = AgentRepository(session)
    agent = await agent_repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    if agent.job != "architect":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent job type '{agent.job}' is not 'architect'",
        )
    return agent


@router.post(
    "/{agent_id}/analyze",
    response_model=ArchitectAnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_architect_analysis(
    agent_id: str,
    payload: ArchitectAnalysisRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: ArchitectService = Depends(get_architect_service),
) -> ArchitectAnalysisRunRead:
    require_ai_agents_enabled()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="shreckLLM is not configured",
        )
    agent_repo = AgentRepository(session)
    agent = await agent_repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    if not agent.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Agent is not active"
        )
    if agent.job != "architect":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent job type '{agent.job}' is not 'architect'",
        )

    run = await service.create_run(
        agent_id=agent.id,
        ontology_id=payload.ontology_id,
        ontology_instance_id=payload.ontology_instance_id,
        settings={
            "requested_by": current_user.id,
            "max_chunks": payload.max_chunks,
            "chunk_size": payload.chunk_size,
        },
    )

    architect_task.apply_async(
        kwargs={
            "run_id": run.id,
            "agent_id": agent.id,
            "request_payload": payload.model_dump(),
            "author_type": "user",
            "author_id": str(current_user.id),
        },
        expires=max(60, int(settings.celery_expires_architect_seconds)),
    )

    refreshed = await service.get_run(run.id, include_proposals=True)
    if not refreshed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Architect run not found after creation",
        )
    return ArchitectAnalysisRunRead.model_validate(refreshed)


@router.get(
    "/runs/{run_id}",
    response_model=ArchitectAnalysisRunRead,
)
async def get_architect_run(
    run_id: str,
    _current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> ArchitectAnalysisRunRead:
    run = await service.get_run(run_id, include_proposals=True)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Architect run not found"
        )
    return ArchitectAnalysisRunRead.model_validate(run)


@router.get(
    "/{agent_id}/runs",
    response_model=list[ArchitectAnalysisRunSummary],
)
async def list_architect_runs(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: ArchitectService = Depends(get_architect_service),
) -> list[ArchitectAnalysisRunSummary]:
    await _get_architect_agent_or_404(agent_id, session)

    runs = await service.list_runs_for_agent(agent_id, limit=limit, offset=offset)
    summaries = []
    for run in runs:
        summaries.append(
            ArchitectAnalysisRunSummary(
                id=run.id,
                agent_id=run.agent_id,
                background_job_id=run.background_job_id,
                generation_job_id=run.generation_job_id,
                ontology_id=run.ontology_id,
                ontology_instance_id=run.ontology_instance_id,
                status=run.status,
                input_chunk_count=run.input_chunk_count,
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
        )
    return summaries


@router.delete(
    "/{agent_id}/runs/{run_id}",
    response_model=dict,
)
async def delete_architect_run(
    agent_id: str,
    run_id: str,
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: ArchitectService = Depends(get_architect_service),
) -> dict[str, int]:
    agent = await _get_architect_agent_or_404(agent_id, session)
    deleted = await service.delete_run(run_id, agent_id=agent.id)
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Architect run not found",
        )
    return {"deleted": deleted}


@router.delete(
    "/{agent_id}/runs",
    response_model=dict,
)
async def delete_architect_runs_for_agent(
    agent_id: str,
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: ArchitectService = Depends(get_architect_service),
) -> dict[str, int]:
    agent = await _get_architect_agent_or_404(agent_id, session)
    deleted = await service.delete_runs_for_agent(agent.id)
    return {"deleted": deleted}


@router.patch(
    "/runs/{run_id}/proposals/status",
    response_model=dict,
)
async def update_proposal_statuses(
    run_id: str,
    payload: ArchitectProposalStatusUpdate,
    _current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> dict[str, int]:
    run = await service.get_run(run_id, include_proposals=False)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Architect run not found"
        )
    updated = await service.update_proposal_states(
        payload.proposal_ids, status=payload.status
    )
    return {"updated": updated}


@router.post(
    "/runs/{run_id}/proposals",
    response_model=ArchitectProposalRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_architect_proposal(
    run_id: str,
    payload: ArchitectProposalCreateRequest,
    _current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> ArchitectProposalRead:
    run = await service.get_run(run_id, include_proposals=False)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Architect run not found")

    proposal_data = payload.model_dump(
        exclude={"id", "created_at", "updated_at"},
        by_alias=True,
    )
    proposal_data["id"] = payload.id or str(uuid4())
    proposal_data["proposal_metadata"] = proposal_data.pop("metadata", None)
    proposal_data["proposal_type"] = ArchitectProposalType(proposal_data["proposal_type"])
    proposal_data["status"] = ArchitectProposalStatus(proposal_data["status"])
    created = (await service.repository.add_proposals(run_id, [proposal_data]))[0]
    await service.session.commit()
    await service.session.refresh(created)
    return _serialize_proposal(created)


async def _update_architect_proposal(
    run_id: str,
    proposal_id: str,
    payload: ArchitectProposalUpdateRequest,
    _current_user: User,
    service: ArchitectService,
) -> ArchitectProposalRead:
    run = await service.get_run(run_id, include_proposals=False)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Architect run not found")

    proposal = await service.session.get(ArchitectProposal, proposal_id)
    if proposal is None or proposal.run_id != run_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Architect proposal not found")

    data = payload.model_dump(exclude_unset=True, by_alias=True)
    if "metadata" in data:
        data["proposal_metadata"] = data.pop("metadata")
    if "proposal_type" in data and data["proposal_type"] is not None:
        data["proposal_type"] = ArchitectProposalType(data["proposal_type"])
    if "status" in data and data["status"] is not None:
        data["status"] = ArchitectProposalStatus(data["status"])
    if "corrected_proposal_type" in data and data["corrected_proposal_type"] is not None:
        data["corrected_proposal_type"] = ArchitectProposalType(data["corrected_proposal_type"])

    for key, value in data.items():
        setattr(proposal, key, value)
    service.session.add(proposal)
    await service.session.commit()
    await service.session.refresh(proposal)
    return _serialize_proposal(proposal)


@router.patch(
    "/runs/{run_id}/proposals/{proposal_id}",
    response_model=ArchitectProposalRead,
)
async def patch_architect_proposal(
    run_id: str,
    proposal_id: str,
    payload: ArchitectProposalUpdateRequest,
    current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> ArchitectProposalRead:
    return await _update_architect_proposal(run_id, proposal_id, payload, current_user, service)


@router.put(
    "/runs/{run_id}/proposals/{proposal_id}",
    response_model=ArchitectProposalRead,
)
async def put_architect_proposal(
    run_id: str,
    proposal_id: str,
    payload: ArchitectProposalUpdateRequest,
    current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> ArchitectProposalRead:
    return await _update_architect_proposal(run_id, proposal_id, payload, current_user, service)


@router.post(
    "/runs/{run_id}/generate",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_entities_from_validated_proposals(
    run_id: str,
    payload: ArchitectGenerationRequest,
    current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> dict[str, Any]:
    require_ai_agents_enabled()
    """
    Step 2: Generate/update entities from validated proposals.
    
    This endpoint accepts validated proposals from the client and triggers
    entity generation/update based on the approved proposals.
    """
    run = await service.get_run(run_id, include_proposals=False)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Architect run not found"
        )
    if payload.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path run_id must match payload.run_id",
        )
    
    # Import here to avoid circular imports
    from app.tasks.architect_generation import (
        generate_entities as generation_task,
    )
    
    # Trigger the background task
    agent_author_id = run.agent_id or payload.author_id
    settings = get_settings()
    result = generation_task.apply_async(
        kwargs={
            "run_id": run_id,
            "reviewed_pipeline_output": payload.reviewed_pipeline_output.model_dump(),
            "author_type": "agent",
            "author_id": agent_author_id,
        },
        expires=max(60, int(settings.celery_expires_architect_seconds)),
    )
    
    return {
        "status": "accepted",
        "task_id": result.id,
        "run_id": run_id,
        "message": "Entity generation task started",
    }


@router.post(
    "/runs/{run_id}/retry-enrichment",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_enrichment_for_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> dict[str, Any]:
    require_ai_agents_enabled()
    run = await service.get_run(run_id, include_proposals=False)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Architect run not found")
    reviewed: dict[str, Any] | None = None
    if run.generation_job_id is not None:
        async with JobsSessionMaker() as jobs_session:
            jobs_repo = BackgroundJobRepository(jobs_session)
            job = await jobs_repo.get_by_id(int(run.generation_job_id))
            if job and isinstance(job.details, str):
                try:
                    payload = json.loads(job.details)
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    meta = payload.get("generation_metadata")
                    if isinstance(meta, dict):
                        candidate = meta.get("reviewed_pipeline_output")
                        if isinstance(candidate, dict):
                            reviewed = candidate
    if not isinstance(reviewed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No stored reviewed pipeline output found in generation job details for this run",
        )
    from app.tasks.architect_generation import generate_entities as generation_task
    settings = get_settings()
    result = generation_task.apply_async(
        kwargs={
            "run_id": run_id,
            "reviewed_pipeline_output": reviewed,
            "author_type": "user",
            "author_id": str(current_user.id),
            "retry_enrichment_only": True,
        },
        expires=max(60, int(settings.celery_expires_architect_seconds)),
    )
    return {
        "status": "accepted",
        "task_id": result.id,
        "run_id": run_id,
        "message": "Enrichment retry task started",
    }
