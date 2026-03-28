"""API router for Architect job orchestration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_architect_service,
    get_current_user,
    get_db_session,
)
from app.core.config_store import get_settings, is_openai_configured
from app.models.agent import Agent
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.architect import (
    ArchitectAnalysisRequest,
    ArchitectAnalysisRunRead,
    ArchitectAnalysisRunSummary,
    ArchitectProposalStatusUpdate,
    ArchitectValidationRequest,
)
from app.services.architect_service import ArchitectService
from app.tasks.architect_analysis import analyze_instance as architect_task

router = APIRouter(prefix="/jobs/architect", tags=["architect"])


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
    settings = get_settings()
    if not is_openai_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key not configured",
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

    architect_task.delay(
        run_id=run.id,
        agent_id=agent.id,
        request_payload=payload.model_dump(),
        author_type="user",
        author_id=str(current_user.id),
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
    "/runs/{run_id}/generate",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_entities_from_validated_proposals(
    run_id: str,
    payload: ArchitectValidationRequest,
    current_user: User = Depends(get_current_user),
    service: ArchitectService = Depends(get_architect_service),
) -> dict[str, Any]:
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
    
    # Import here to avoid circular imports
    from app.tasks.architect_generation_v2 import (
        generate_entities as generation_task,
    )
    
    # Trigger the background task
    agent_author_id = run.agent_id or payload.author_id
    result = generation_task.delay(
        run_id=run_id,
        revised_suggestions=(
            [s.model_dump() for s in payload.revised_suggestions]
            if payload.revised_suggestions
            else None
        ),
        validated_proposals=(
            [p.model_dump() for p in payload.validated_proposals]
            if payload.validated_proposals
            else None
        ),
        author_type="agent",
        author_id=agent_author_id,
    )
    
    return {
        "status": "accepted",
        "task_id": result.id,
        "run_id": run_id,
        "message": "Entity generation task started",
    }

