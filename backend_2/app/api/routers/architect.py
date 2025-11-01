"""API router for Architect job orchestration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_architect_service,
    get_current_user,
    get_db_session,
)
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.architect import (
    ArchitectAnalysisRequest,
    ArchitectAnalysisRunRead,
    ArchitectAnalysisRunSummary,
    ArchitectProposalStatusUpdate,
)
from app.services.architect_service import ArchitectService
from app.tasks.architect_analysis import analyze_instance as architect_task

router = APIRouter(prefix="/jobs/architect", tags=["architect"])


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

    runs = await service.list_runs_for_agent(agent_id, limit=limit, offset=offset)
    summaries = []
    for run in runs:
        summaries.append(
            ArchitectAnalysisRunSummary(
                id=run.id,
                agent_id=run.agent_id,
                background_job_id=run.background_job_id,
                ontology_id=run.ontology_id,
                ontology_instance_id=run.ontology_instance_id,
                status=run.status,
                input_chunk_count=run.input_chunk_count,
                created_at=run.created_at,
                updated_at=run.updated_at,
            )
        )
    return summaries


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
