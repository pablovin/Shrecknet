"""API router for Novelist job."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.api.agent_feature_gate import require_ai_agents_enabled
from app.core.config_store import get_settings, is_shreckllm_configured
from app.graph.neo4j import get_driver
from app.integrations.novelist_source import extract_source, normalize_pdf_text
from app.models.agent import Agent
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.novelist import (
    NovelistRunCreate,
    NovelistRunRead,
    SourcePage,
)
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


def _extract_source_from_upload(file: UploadFile) -> tuple[str, list[SourcePage]]:
    try:
        return extract_source(file.filename or "", file.file.read())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _extract_text_from_upload(file: UploadFile) -> str:
    """Compatibility wrapper for existing extraction callers."""
    return _extract_source_from_upload(file)[0]


_normalize_pdf_extracted_text = normalize_pdf_text


async def _create_and_queue_run(
    *,
    agent_id: str,
    payload: NovelistRunCreate,
    current_user: User,
    session: AsyncSession,
    service: NovelistService,
    source_pages: list[SourcePage] | None = None,
) -> NovelistRunRead:
    language, instructions = await service.resolve_and_save_preferences(
        agent_id=agent_id,
        language=payload.language,
        instructions=payload.instructions,
    )
    payload = payload.model_copy(
        update={"language": language, "instructions": instructions}
    )
    run = await service.create_run(
        agent_id=agent_id,
        ontology_id=None,
        ontology_instance_id=None,
        settings={
            "requested_by": current_user.id,
            "language": payload.language,
            "source_pages": [page.model_dump() for page in source_pages or []],
        },
        request_payload=payload.model_dump(),
    )

    settings = get_settings()
    generate_draft.apply_async(
        kwargs={
            "run_id": run.id,
            "request_payload": payload.model_dump(),
            "author_type": "user",
            "author_id": str(current_user.id),
        },
        expires=max(60, int(settings.celery_expires_novelist_seconds)),
    )

    refreshed = await service.get_run(run.id)
    return NovelistRunRead.model_validate(refreshed)


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
    require_ai_agents_enabled()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="shreckLLM is not configured",
        )
    await _get_novelist_agent_or_404(agent_id, session)
    return await _create_and_queue_run(
        agent_id=agent_id,
        payload=payload,
        current_user=current_user,
        session=session,
        service=service,
    )


@router.post(
    "/{agent_id}/runs/upload",
    response_model=NovelistRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_novelist_run_from_upload(
    agent_id: str,
    file: UploadFile = File(...),
    language: str | None = Form(None, max_length=100),
    instructions: str | None = Form(None, max_length=20000),
    previous_session_id: str | None = Form(None),
    previous_novelist_run_id: str | None = Form(None),
    source_type: str = Form("auto"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: NovelistService = Depends(get_novelist_service),
) -> NovelistRunRead:
    require_ai_agents_enabled()
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="shreckLLM is not configured",
        )
    await _get_novelist_agent_or_404(agent_id, session)

    extracted_text, source_pages = _extract_source_from_upload(file)
    if not extracted_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract text from uploaded file",
        )
    payload = NovelistRunCreate(
        unstructured_text=extracted_text,
        language=language,
        instructions=instructions,
        previous_session_id=previous_session_id,
        previous_novelist_run_id=previous_novelist_run_id,
        source_type=source_type,
        source_label=file.filename,
    )
    return await _create_and_queue_run(
        agent_id=agent_id,
        payload=payload,
        source_pages=source_pages,
        current_user=current_user,
        session=session,
        service=service,
    )


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
