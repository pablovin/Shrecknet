"""API router for user-owned personal companion agents."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent_feature_gate import require_ai_agents_enabled
from app.api.deps import get_current_user, get_db_session, get_media_service
from app.db.jobs_session import get_jobs_session
from app.core.config_store import get_settings
from app.models.background_job import AuthorType
from app.models.user import User
from app.schemas.background_job import BackgroundJobResponse
from app.schemas.personal_companion_agent import (
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentRead,
    PersonalCompanionAgentUpdate,
)
from app.schemas.personal_companion_orchestrator import (
    CompanionOrchestratorTurnQueuedResponse,
    CompanionOrchestratorTurnRequest,
    CompanionOrchestratorTurnResultResponse,
    CompanionWorldBootstrapRequest,
    CompanionWorldBootstrapResponse,
)
from app.services.background_job_service import BackgroundJobService
from app.services.herald_orchestrator_service import HeraldOrchestratorService
from app.services.media_service import ImageValidationError, MediaService
from app.services.personal_companion_agent_service import PersonalCompanionAgentService
from app.utils.companion_orchestrator_store import (
    append_chat_message,
    create_or_update_session,
    get_session,
    read_chat_file,
    update_session_allocated_tools,
)
from app.utils.job_tracking import create_background_job
from app.models.background_job import JobType

router = APIRouter(prefix="/users/me/companion", tags=["companions"])


async def get_companion_service(
    session: AsyncSession = Depends(get_db_session),
) -> PersonalCompanionAgentService:
    return PersonalCompanionAgentService(session)


async def get_herald_orchestrator_service(
    session: AsyncSession = Depends(get_db_session),
) -> HeraldOrchestratorService:
    return HeraldOrchestratorService(session)


@router.post("", response_model=PersonalCompanionAgentRead, status_code=status.HTTP_201_CREATED)
async def create_personal_companion(
    payload: PersonalCompanionAgentCreate,
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> PersonalCompanionAgentRead:
    return await service.create_for_user(current_user.id, payload)


@router.get("", response_model=PersonalCompanionAgentRead)
async def get_personal_companion(
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> PersonalCompanionAgentRead:
    return await service.get_for_user(current_user.id)


@router.patch("", response_model=PersonalCompanionAgentRead)
async def update_personal_companion(
    payload: PersonalCompanionAgentUpdate,
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> PersonalCompanionAgentRead:
    return await service.update_for_user(current_user.id, payload)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personal_companion(
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> Response:
    await service.delete_for_user(current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/avatar", response_model=PersonalCompanionAgentRead)
async def upload_personal_companion_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    service: PersonalCompanionAgentService = Depends(get_companion_service),
    media_service: MediaService = Depends(get_media_service),
) -> PersonalCompanionAgentRead:
    companion = await service.get_for_user(current_user.id)

    try:
        settings = get_settings()
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", companion.name.lower()).strip("-")
        if not safe_name:
            safe_name = f"user-{current_user.id}"
        target_filename = f"companion_{safe_name}.png"
        avatar_url = await media_service.save_image(
            file,
            category="avatars",
            identifier=f"personal_companion_{current_user.id}",
            resize=(settings.image_max_width, settings.image_max_height),
            filename=target_filename,
        )
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return await service.update_for_user(
        current_user.id,
        PersonalCompanionAgentUpdate(avatar_url=avatar_url),
    )


@router.post(
    "/orchestrator/bootstrap",
    response_model=CompanionWorldBootstrapResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap_orchestrator_world_session(
    payload: CompanionWorldBootstrapRequest,
    current_user: User = Depends(get_current_user),
    companion_service: PersonalCompanionAgentService = Depends(get_companion_service),
    orchestrator_service: HeraldOrchestratorService = Depends(
        get_herald_orchestrator_service
    ),
) -> CompanionWorldBootstrapResponse:
    companion = await companion_service.get_for_user(current_user.id)
    await orchestrator_service.resolve_ontology_or_404(payload.ontology_id)
    allocated = await orchestrator_service.allocate_tools(payload.ontology_id)
    session_payload = create_or_update_session(
        user_id=current_user.id,
        companion_id=companion.id,
        ontology_id=payload.ontology_id,
        allocated_tools=allocated.model_dump(),
    )
    created_at_raw = str(session_payload.get("created_at") or "")
    created_at = datetime.fromisoformat(created_at_raw)
    return CompanionWorldBootstrapResponse(
        session_id=str(session_payload["session_id"]),
        companion_id=companion.id,
        ontology_id=payload.ontology_id,
        allocated_tools=allocated,
        created_at=created_at,
    )


@router.post(
    "/orchestrator/chats/{session_id}/turns",
    response_model=CompanionOrchestratorTurnQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_orchestrator_turn(
    session_id: str,
    payload: CompanionOrchestratorTurnRequest,
    current_user: User = Depends(get_current_user),
    companion_service: PersonalCompanionAgentService = Depends(get_companion_service),
    orchestrator_service: HeraldOrchestratorService = Depends(
        get_herald_orchestrator_service
    ),
) -> CompanionOrchestratorTurnQueuedResponse:
    require_ai_agents_enabled()
    companion = await companion_service.get_for_user(current_user.id)
    session_payload = get_session(current_user.id, session_id)
    if session_payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion orchestrator session not found",
        )
    if str(session_payload.get("companion_id")) != str(companion.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not own this orchestrator session",
        )

    ontology_id = int(session_payload.get("ontology_id") or 0)
    refreshed_allocation = await orchestrator_service.allocate_tools(ontology_id)
    refreshed_payload = update_session_allocated_tools(
        current_user.id,
        session_id,
        refreshed_allocation.model_dump(),
    )
    if refreshed_payload is not None:
        session_payload = refreshed_payload
    job_details = {
        "status": "queued",
        "query": payload.query,
        "session_id": session_id,
        "ontology_id": ontology_id,
        "companion_id": companion.id,
        "allocated_tools": session_payload.get("allocated_tools") or {},
    }
    job_id = await create_background_job(
        author_type=AuthorType.USER,
        author_id=str(current_user.id),
        job_type=JobType.COMPANION_ORCHESTRATOR,
        description="Companion orchestrator turn",
        details=job_details,
        ontology_id=ontology_id,
    )

    from app.tasks.companion_orchestrator import run_companion_orchestrator_turn

    run_companion_orchestrator_turn.delay(
        job_id=job_id,
        user_id=current_user.id,
        session_id=session_id,
        query=payload.query,
    )

    try:
        append_chat_message(
            user_id=current_user.id,
            companion_id=companion.id,
            session_id=session_id,
            role="user",
            content=payload.query,
            metadata={"job_id": job_id, "status": "queued"},
        )
    except Exception:
        # Chat logging should not block turn queueing.
        pass

    return CompanionOrchestratorTurnQueuedResponse(
        job_id=job_id,
        status="queued",
        session_id=session_id,
        ontology_id=ontology_id,
    )


@router.get(
    "/orchestrator/chats/{session_id}/file",
    response_model=dict,
)
async def get_orchestrator_chat_file(
    session_id: str,
    current_user: User = Depends(get_current_user),
    companion_service: PersonalCompanionAgentService = Depends(get_companion_service),
) -> dict:
    companion = await companion_service.get_for_user(current_user.id)
    session_payload = get_session(current_user.id, session_id)
    if session_payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion orchestrator session not found",
        )
    if str(session_payload.get("companion_id")) != str(companion.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not own this orchestrator session",
        )

    payload = read_chat_file(current_user.id, companion.id, session_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Companion orchestrator chat file not found",
        )
    return payload


@router.get(
    "/orchestrator/turns/{job_id}",
    response_model=CompanionOrchestratorTurnResultResponse,
)
async def get_orchestrator_turn_result(
    job_id: int,
    current_user: User = Depends(get_current_user),
    jobs_session: AsyncSession = Depends(get_jobs_session),
) -> CompanionOrchestratorTurnResultResponse:
    service = BackgroundJobService(jobs_session)
    job = await service.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Background job not found",
        )
    if job.author_type != AuthorType.USER or str(job.author_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to read this orchestrator job",
        )
    if job.job_type != JobType.COMPANION_ORCHESTRATOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not a companion orchestrator turn",
        )

    payload_data: dict = {}
    raw_details = job.details
    if raw_details:
        import json

        try:
            parsed = json.loads(raw_details)
            if isinstance(parsed, dict):
                payload_data = parsed
        except Exception:
            payload_data = {"raw": raw_details}

    return CompanionOrchestratorTurnResultResponse(
        job_id=job.id,
        status=job.status.value if hasattr(job.status, "value") else str(job.status),
        payload=payload_data,
    )
