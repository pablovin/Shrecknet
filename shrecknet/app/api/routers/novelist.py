"""API router for Novelist job."""

from __future__ import annotations

from typing import Any
from io import BytesIO
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.core.config_store import get_settings, is_shreckllm_configured
from app.graph.neo4j import get_driver
from app.models.agent import Agent
from app.models.user import User
from app.repositories.agent_repository import AgentRepository
from app.schemas.novelist import (
    NovelistRunCreate,
    NovelistRunRead,
)
from app.services.novelist_service import NovelistService
from app.tasks.novelist import generate_draft

router = APIRouter(prefix="/jobs/novelist", tags=["novelist"])
_BULLET_OR_NUMBERED_START = re.compile(r"^\s*(?:[●•\-\*]\s+|\d+[.)]\s+)")
_TIMESTAMP_MARKER = re.compile(r"\(\d{1,2}:\d{2}:\d{2}\)")


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


def _extract_text_from_upload(file: UploadFile) -> str:
    filename = (file.filename or "").lower()
    suffix = filename.rsplit(".", 1)[-1] if "." in filename else ""
    raw = file.file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )
    if suffix == "txt":
        return raw.decode("utf-8", errors="ignore").strip()
    if suffix == "pdf":
        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="PyPDF2 is required to process PDF files",
            ) from exc
        reader = PdfReader(BytesIO(raw))
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(_normalize_pdf_extracted_text(page.extract_text() or ""))
            except Exception:
                continue
        return "\n\n".join(page for page in pages if page).strip()
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported file type. Use .txt or .pdf",
    )


def _normalize_pdf_extracted_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line and line.strip()]
    if not lines:
        return ""

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        starts_block = bool(_BULLET_OR_NUMBERED_START.match(line)) or bool(
            _TIMESTAMP_MARKER.search(line)
        )
        if starts_block and current:
            blocks.append(current)
            current = [line]
            continue
        current.append(line)

    if current:
        blocks.append(current)

    return "\n\n".join(" ".join(block) for block in blocks if block).strip()


async def _create_and_queue_run(
    *,
    agent_id: str,
    payload: NovelistRunCreate,
    current_user: User,
    session: AsyncSession,
    service: NovelistService,
) -> NovelistRunRead:
    run = await service.create_run(
        agent_id=agent_id,
        ontology_id=None,
        ontology_instance_id=None,
        settings={
            "requested_by": current_user.id,
            "language": payload.language,
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
    language: str | None = Form(None),
    instructions: str | None = Form(None),
    previous_session_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    service: NovelistService = Depends(get_novelist_service),
) -> NovelistRunRead:
    settings = get_settings()
    if not is_shreckllm_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="shreckLLM is not configured",
        )
    await _get_novelist_agent_or_404(agent_id, session)

    extracted_text = _extract_text_from_upload(file)
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
    )
    return await _create_and_queue_run(
        agent_id=agent_id,
        payload=payload,
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
