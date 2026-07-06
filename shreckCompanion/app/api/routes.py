from __future__ import annotations

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status

from app.core.config import get_settings
from app.schemas import (
    CompanionChatSessionCount,
    CompanionChatSessionCreateRequest,
    CompanionChatSessionRead,
    CompanionChatSessionUpdateRequest,
    CompanionOrchestratorTurnQueuedResponse,
    CompanionOrchestratorTurnRequest,
    CompanionOrchestratorTurnResultResponse,
    CompanionWorldBootstrapRequest,
    CompanionWorldBootstrapResponse,
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentRead,
    PersonalCompanionAgentUpdate,
    ServiceStatusResponse,
)
from app.service import CompanionService

router = APIRouter()


def get_service(request: Request) -> CompanionService:
    return request.app.state.companion_service


def get_current_user_id(x_shreck_user_id: int | None = Header(default=None)) -> int:
    return int(x_shreck_user_id or get_settings().default_user_id)


def get_current_username(x_shreck_username: str | None = Header(default=None)) -> str | None:
    return x_shreck_username


def get_authorization_header(authorization: str | None = Header(default=None)) -> str | None:
    return authorization


@router.get("/health", status_code=status.HTTP_200_OK)
async def health(service: CompanionService = Depends(get_service)) -> dict[str, object]:
    return await service.health()


@router.get("/ready", status_code=status.HTTP_200_OK)
async def ready(service: CompanionService = Depends(get_service)) -> dict[str, object]:
    return await service.ready()


@router.get("/status", response_model=ServiceStatusResponse, status_code=status.HTTP_200_OK)
async def status_payload(service: CompanionService = Depends(get_service)) -> ServiceStatusResponse:
    return await service.status()


@router.get("/config", status_code=status.HTTP_200_OK)
async def get_config(service: CompanionService = Depends(get_service)) -> dict[str, object]:
    return service.config_public_view()


@router.get("/config/frontend", status_code=status.HTTP_200_OK)
async def get_frontend_config(service: CompanionService = Depends(get_service)) -> dict[str, object]:
    return service.frontend_config_view()


@router.post("/users/me/companion", response_model=PersonalCompanionAgentRead, status_code=status.HTTP_201_CREATED)
async def create_personal_companion(
    payload: PersonalCompanionAgentCreate,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> PersonalCompanionAgentRead:
    try:
        return service.create_companion(user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/users/me/companion", response_model=PersonalCompanionAgentRead)
async def get_personal_companion(
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> PersonalCompanionAgentRead:
    try:
        return service.get_companion(user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/users/me/companion", response_model=PersonalCompanionAgentRead)
async def update_personal_companion(
    payload: PersonalCompanionAgentUpdate,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> PersonalCompanionAgentRead:
    try:
        return service.update_companion(user_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/me/companion/avatar", response_model=PersonalCompanionAgentRead)
async def upload_personal_companion_avatar(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    username: str | None = Depends(get_current_username),
    service: CompanionService = Depends(get_service),
) -> PersonalCompanionAgentRead:
    try:
        return await service.upload_companion_avatar(user_id=user_id, username=username, file=file)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/me/companion", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personal_companion(
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> Response:
    try:
        service.delete_companion(user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/me/companion/orchestrator/bootstrap",
    response_model=CompanionWorldBootstrapResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bootstrap_orchestrator_world_session(
    payload: CompanionWorldBootstrapRequest,
    user_id: int = Depends(get_current_user_id),
    auth_header: str | None = Depends(get_authorization_header),
    service: CompanionService = Depends(get_service),
) -> CompanionWorldBootstrapResponse:
    try:
        return await service.bootstrap_world(user_id=user_id, ontology_id=payload.ontology_id, auth_header=auth_header)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/users/me/companion/orchestrator/chats", response_model=list[CompanionChatSessionRead])
async def list_orchestrator_chats(
    ontology_id: int,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> list[CompanionChatSessionRead]:
    try:
        return service.list_chat_sessions(user_id=user_id, ontology_id=ontology_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/me/companion/orchestrator/chats", response_model=CompanionChatSessionRead, status_code=status.HTTP_201_CREATED)
async def create_orchestrator_chat(
    payload: CompanionChatSessionCreateRequest,
    user_id: int = Depends(get_current_user_id),
    auth_header: str | None = Depends(get_authorization_header),
    service: CompanionService = Depends(get_service),
) -> CompanionChatSessionRead:
    try:
        return await service.create_chat_session(user_id=user_id, payload=payload, auth_header=auth_header)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/users/me/companion/orchestrator/chats/{session_id}", response_model=CompanionChatSessionRead)
async def get_orchestrator_chat(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> CompanionChatSessionRead:
    try:
        return service.get_chat_session(user_id=user_id, session_id=session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.patch("/users/me/companion/orchestrator/chats/{session_id}", response_model=CompanionChatSessionRead)
async def update_orchestrator_chat(
    session_id: str,
    payload: CompanionChatSessionUpdateRequest,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> CompanionChatSessionRead:
    try:
        return service.update_chat_session(user_id=user_id, session_id=session_id, payload=payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/users/me/companion/orchestrator/chats/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_orchestrator_chat(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> Response:
    try:
        service.delete_chat_session(user_id=user_id, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/users/me/companion/orchestrator/chat-counts", response_model=list[CompanionChatSessionCount])
async def get_orchestrator_chat_counts(
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> list[CompanionChatSessionCount]:
    try:
        return service.chat_session_counts(user_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/users/me/companion/orchestrator/chats/{session_id}/turns",
    response_model=CompanionOrchestratorTurnQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_orchestrator_turn(
    session_id: str,
    payload: CompanionOrchestratorTurnRequest,
    user_id: int = Depends(get_current_user_id),
    auth_header: str | None = Depends(get_authorization_header),
    service: CompanionService = Depends(get_service),
) -> CompanionOrchestratorTurnQueuedResponse:
    try:
        return await service.queue_turn(user_id=user_id, session_id=session_id, query=payload.query, auth_header=auth_header)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/users/me/companion/orchestrator/chats/{session_id}/file", response_model=dict)
async def get_orchestrator_chat_file(
    session_id: str,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> dict:
    try:
        companion = service.get_companion(user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = service.store.get_session(user_id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Companion orchestrator session not found")
    if str(session.get("companion_id")) != str(companion.id):
        raise HTTPException(status_code=403, detail="Companion does not own this orchestrator session")
    payload = service.store.read_chat_file(user_id, companion.id, session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Companion orchestrator chat file not found")
    return payload


@router.get(
    "/users/me/companion/orchestrator/turns/{job_id}",
    response_model=CompanionOrchestratorTurnResultResponse,
)
async def get_orchestrator_turn_result(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
    service: CompanionService = Depends(get_service),
) -> CompanionOrchestratorTurnResultResponse:
    job = service.store.get_turn_job(user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Background job not found")
    return CompanionOrchestratorTurnResultResponse(
        job_id=job["job_id"],
        status=job["status"],
        payload=job["payload"],
    )
