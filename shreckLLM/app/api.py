from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth import get_admin_or_world_builder
from app.config_store import RuntimeConfigUpdate, ProviderDefaults, reload_runtime_config, update_runtime_config
from app.errors import (
    DependencyUnavailableError,
    InvalidModelError,
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderOverloadedError,
    ProviderPermissionError,
    ProviderTimeoutError,
)
from app.schemas import (
    ChatRequest,
    ChatResponse,
    OpenAITokenUpdateRequest,
    OpenAIValidationResponse,
    ServiceStatusResponse,
)
from app.service import ChatService

router = APIRouter()


def get_service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.get("/health", status_code=status.HTTP_200_OK)
async def health(service: ChatService = Depends(get_service)) -> dict[str, object]:
    return await service.health()


@router.get("/ready", status_code=status.HTTP_200_OK)
async def ready(service: ChatService = Depends(get_service)) -> dict[str, object]:
    return await service.ready()


@router.get("/models", status_code=status.HTTP_200_OK)
async def models(service: ChatService = Depends(get_service)) -> dict[str, object]:
    try:
        return await service.models()
    except DependencyUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status", response_model=ServiceStatusResponse, status_code=status.HTTP_200_OK)
async def status_payload(service: ChatService = Depends(get_service)) -> ServiceStatusResponse:
    return await service.status()


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat(payload: ChatRequest, service: ChatService = Depends(get_service)) -> ChatResponse:
    try:
        return await service.chat(payload)
    except InvalidModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderBadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ProviderPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ProviderOverloadedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ProviderTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except DependencyUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/config", status_code=status.HTTP_200_OK)
async def get_config(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return service.config_public_view()


@router.put("/config", status_code=status.HTTP_200_OK)
async def put_config(
    payload: RuntimeConfigUpdate,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    patch = payload.model_dump(exclude_none=True)
    if "provider_defaults" in patch:
        incoming = patch["provider_defaults"] or {}
        merged = dict(service._runtime.provider_defaults)
        for provider_id, defaults in incoming.items():
            if isinstance(defaults, dict):
                merged[provider_id] = ProviderDefaults.model_validate(defaults)
            elif isinstance(defaults, ProviderDefaults):
                merged[provider_id] = defaults
        patch["provider_defaults"] = merged
    update_runtime_config(patch)
    reload_runtime_config()
    await service.refresh_runtime()
    return service.config_public_view()


@router.post("/config/reload", status_code=status.HTTP_200_OK)
async def post_config_reload(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    reload_runtime_config()
    await service.refresh_runtime()
    return {
        "reloaded": True,
        "config": service.config_public_view(),
    }


@router.put("/config/openai-token", status_code=status.HTTP_200_OK)
async def put_openai_token(
    payload: OpenAITokenUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    config = service._runtime
    providers = config.provider_defaults.copy()
    openai_defaults = providers.get("openai")
    if openai_defaults is None:
        openai_defaults = ProviderDefaults(default_model="gpt-5-nano", base_url=None, api_key=payload.api_key)
    else:
        openai_defaults = ProviderDefaults(
            default_model=openai_defaults.default_model,
            base_url=openai_defaults.base_url,
            api_key=payload.api_key,
        )
    providers["openai"] = openai_defaults

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.openai_validation_status()
    return {
        "stored": True,
        "openai": validation,
    }


@router.delete("/config/openai-token", status_code=status.HTTP_200_OK)
async def delete_openai_token(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    config = service._runtime
    providers = config.provider_defaults.copy()
    openai_defaults = providers.get("openai")
    if openai_defaults is None:
        openai_defaults = ProviderDefaults(default_model="gpt-5-nano", base_url=None, api_key="")
    else:
        openai_defaults = ProviderDefaults(
            default_model=openai_defaults.default_model,
            base_url=openai_defaults.base_url,
            api_key="",
        )
    providers["openai"] = openai_defaults

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.openai_validation_status()
    return {
        "stored": False,
        "openai": validation,
    }


@router.get(
    "/providers/openai/validate",
    response_model=OpenAIValidationResponse,
    status_code=status.HTTP_200_OK,
)
async def get_openai_validate(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> OpenAIValidationResponse:
    payload = await service.openai_validation_status()
    return OpenAIValidationResponse.model_validate(payload)
