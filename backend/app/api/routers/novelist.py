"""API router for Novelist job (step 1 draft)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config_store import get_settings, is_openai_configured
from app.models.agent import Agent
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.novelist import NovelistRunCreate, NovelistRunRead
from app.services.novelist_service import NovelistService
from app.tasks.novelist import generate_draft

router = APIRouter(prefix="/jobs/novelist", tags=["novelist"])


async def _get_novelist_agent_or_404(
    agent_id: str, session: AsyncSession
) -> Agent:
    repo = AgentRepository(session)
    agent = await repo.get_by_id(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    if not agent.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Agent is not active"
        )
    if agent.job != "novelist":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent job type '{agent.job}' is not 'novelist'",
        )
    return agent


async def get_novelist_service(
    session: AsyncSession = Depends(get_db_session),
) -> NovelistService:
    return NovelistService(session)


@router.post(
    "/{agent_id}/runs",
    response_model=NovelistRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_novelist_run(
    agent_id: str,
    payload: NovelistRunCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: NovelistService = Depends(get_novelist_service),
) -> NovelistRunRead:
    settings = get_settings()
    if not is_openai_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI API key not configured",
        )
    await _get_novelist_agent_or_404(agent_id, session)

    run = await service.create_run(
        agent_id=agent_id,
        ontology_id=None,
        ontology_instance_id=None,
        settings={
            "requested_by": current_user.id,
            "language": payload.language,
            "chunk_size": payload.chunk_size,
            "max_chunks": payload.max_chunks,
            "questions_per_chunk": payload.questions_per_chunk,
        },
        request_payload=payload.model_dump(),
    )

    # Fire background task
    generate_draft.delay(
        run_id=run.id,
        request_payload=payload.model_dump(),
        author_type="user",
        author_id=str(current_user.id),
    )

    refreshed = await service.get_run(run.id)
    return NovelistRunRead.model_validate(refreshed)


@router.get(
    "/runs/{run_id}",
    response_model=NovelistRunRead,
)
async def get_novelist_run(
    run_id: str,
    _current_user: User = Depends(get_current_user),
    service: NovelistService = Depends(get_novelist_service),
) -> NovelistRunRead:
    run = await service.get_run(run_id)
    return NovelistRunRead.model_validate(run)


@router.get(
    "/{agent_id}/runs",
    response_model=list[NovelistRunRead],
)
async def list_novelist_runs(
    agent_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: NovelistService = Depends(get_novelist_service),
) -> list[NovelistRunRead]:
    await _get_novelist_agent_or_404(agent_id, session)
    runs = await service.list_runs(agent_id=agent_id, limit=limit, offset=offset)
    return [NovelistRunRead.model_validate(r) for r in runs]


@router.delete(
    "/{agent_id}/runs/{run_id}",
    response_model=dict,
)
async def delete_novelist_run(
    agent_id: str,
    run_id: str,
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: NovelistService = Depends(get_novelist_service),
) -> dict[str, int]:
    await _get_novelist_agent_or_404(agent_id, session)
    deleted = await service.delete_run(run_id, agent_id=agent_id)
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Novelist run not found"
        )
    return {"deleted": deleted}
