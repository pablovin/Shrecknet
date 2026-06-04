from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import get_admin_or_world_builder, get_admin_or_world_builder_or_internal
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
    AnthropicTokenUpdateRequest,
    AnthropicValidationResponse,
    ChatJobCreateResponse,
    ChatJobStatusResponse,
    ChatRequest,
    ChatResponse,
    OpenAITokenUpdateRequest,
    OpenAIValidationResponse,
    ServiceStatusResponse,
)
from app.service import ChatService

router = APIRouter()

CONFIG_FIELD_META: dict[str, dict[str, object]] = {
    "provider_defaults": {"type": "provider_map", "help": "Provider definitions including models, URLs, auth settings, and API keys.", "category": "Providers"},
    "provider_limits": {"type": "object", "help": "Per-provider concurrency and queue limit overrides.", "category": "Providers"},
    "memory_ttl_seconds": {"type": "integer", "help": "Conversation memory TTL in seconds.", "category": "Memory"},
    "memory_max_messages": {"type": "integer", "help": "Maximum messages stored per conversation.", "category": "Memory"},
    "max_concurrent_requests": {"type": "integer", "help": "Global concurrent request limit.", "category": "Concurrency"},
    "request_timeout_seconds": {"type": "number", "help": "Per-request timeout in seconds.", "category": "Concurrency"},
    "max_queue_wait_seconds": {"type": "number", "help": "Maximum queue wait before rejection.", "category": "Concurrency"},
    "chat_job_queue_max_size": {"type": "integer", "help": "Maximum queued chat jobs.", "category": "Concurrency"},
    "chat_job_result_ttl_seconds": {"type": "integer", "help": "How long completed chat job results are retained.", "category": "Concurrency"},
    "chat_job_poll_default_interval_ms": {"type": "integer", "help": "Suggested polling interval for chat job status.", "category": "Concurrency"},
    "chat_job_max_retries": {"type": "integer", "help": "Retry attempts for chat jobs on retryable provider failures.", "category": "Concurrency"},
}

CONFIG_GROUPS: list[dict[str, object]] = [
    {
        "id": "provider_assignment",
        "label": "Provider Assignment",
        "property": "runtime",
        "fields": ["provider_defaults"],
    },
    {
        "id": "expert_overrides",
        "label": "Expert Overrides",
        "property": "runtime",
        "fields": [
            "provider_limits",
            "memory_ttl_seconds",
            "memory_max_messages",
            "max_concurrent_requests",
            "request_timeout_seconds",
            "max_queue_wait_seconds",
            "chat_job_queue_max_size",
            "chat_job_result_ttl_seconds",
            "chat_job_poll_default_interval_ms",
            "chat_job_max_retries",
        ],
    },
]

class ProviderModelMutationRequest(BaseModel):
    model: str = Field(min_length=1)


class ProviderMetadataUpdateRequest(BaseModel):
    kind: str | None = None
    auth_strategy: str | None = None
    healthcheck_path: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    models: list[str] | None = None


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
        detail = str(exc)
        retry_after = None
        if "retry_after=" in detail:
            try:
                retry_after = float(detail.split("retry_after=", 1)[1].split()[0])
            except Exception:
                retry_after = None
        raise HTTPException(
            status_code=429,
            detail={"error": detail, "retry_after_seconds": retry_after, "provider_overloaded": True},
        ) from exc
    except ProviderTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except DependencyUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/chat/jobs", response_model=ChatJobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_chat_job(payload: ChatRequest, service: ChatService = Depends(get_service)) -> ChatJobCreateResponse:
    try:
        return await service.submit_chat_job(payload)
    except ProviderOverloadedError as exc:
        raise HTTPException(status_code=429, detail={"error": str(exc), "provider_overloaded": True}) from exc


@router.get("/chat/jobs/{job_id}", response_model=ChatJobStatusResponse, status_code=status.HTTP_200_OK)
async def get_chat_job(job_id: str, service: ChatService = Depends(get_service)) -> ChatJobStatusResponse:
    status_row = service.get_chat_job_status(job_id)
    if status_row is None:
        raise HTTPException(status_code=404, detail="chat job not found")
    return status_row


@router.get("/chat/jobs/{job_id}/result", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def get_chat_job_result(job_id: str, service: ChatService = Depends(get_service)) -> ChatResponse:
    status_row = service.get_chat_job_status(job_id)
    if status_row is None:
        raise HTTPException(status_code=404, detail="chat job not found")
    if status_row.status == "succeeded":
        result = await service.get_chat_job_result(job_id)
        if result is None:
            raise HTTPException(status_code=410, detail="chat job result expired")
        return result
    if status_row.status == "failed":
        raise HTTPException(status_code=409, detail={"status": "failed", "error": status_row.error, "job_id": job_id})
    raise HTTPException(status_code=409, detail={"status": status_row.status, "job_id": job_id})


@router.get("/config", status_code=status.HTTP_200_OK)
async def get_config(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder_or_internal),
) -> dict[str, object]:
    return service.config_public_view()


@router.get("/config/schema", status_code=status.HTTP_200_OK)
async def get_config_schema(
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    all_fields = set(RuntimeConfigUpdate.model_fields.keys())
    # Keep default provider internal for now; frontend should not render or edit it.
    all_fields.discard("default_provider_id")
    grouped_fields: set[str] = set()
    for group in CONFIG_GROUPS:
        grouped_fields.update([f for f in group["fields"] if isinstance(f, str)])
    ungrouped_fields = sorted(all_fields - grouped_fields)

    field_meta = dict(CONFIG_FIELD_META)
    for field in sorted(all_fields):
        row = dict(field_meta.get(field, {}))
        row.setdefault("change_impact", "hot")
        row.setdefault("frontend_editable", True)
        if field in {"provider_limits", "memory_ttl_seconds", "memory_max_messages", "max_concurrent_requests", "request_timeout_seconds", "max_queue_wait_seconds", "chat_job_queue_max_size", "chat_job_result_ttl_seconds", "chat_job_poll_default_interval_ms", "chat_job_max_retries"}:
            row.setdefault("frontend_editable", False)
            row.setdefault("derived_from_profile", True)
        field_meta[field] = row

    return {
        "version": 2,
        "groups": CONFIG_GROUPS,
        "field_meta": {k: v for k, v in field_meta.items() if k in all_fields},
        "frontend_locked_fields": [],
        "ungrouped_fields": ungrouped_fields,
    }


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
        openai_defaults = ProviderDefaults(
            kind="cloud",
            auth_strategy="api_key",
            default_model="gpt-5-nano",
            models=["gpt-5-nano"],
            base_url=None,
            api_key=payload.api_key,
        )
    else:
        openai_defaults = ProviderDefaults(
            kind=openai_defaults.kind,
            auth_strategy=openai_defaults.auth_strategy,
            healthcheck_path=openai_defaults.healthcheck_path,
            default_model=openai_defaults.default_model,
            models=openai_defaults.models,
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
        openai_defaults = ProviderDefaults(
            kind="cloud",
            auth_strategy="api_key",
            default_model="gpt-5-nano",
            models=["gpt-5-nano"],
            base_url=None,
            api_key="",
        )
    else:
        openai_defaults = ProviderDefaults(
            kind=openai_defaults.kind,
            auth_strategy=openai_defaults.auth_strategy,
            healthcheck_path=openai_defaults.healthcheck_path,
            default_model=openai_defaults.default_model,
            models=openai_defaults.models,
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


@router.put("/config/anthropic-token", status_code=status.HTTP_200_OK)
async def put_anthropic_token(
    payload: AnthropicTokenUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    config = service._runtime
    providers = config.provider_defaults.copy()
    current = providers.get("anthropic")
    if current is None:
        current = ProviderDefaults(
            kind="cloud",
            auth_strategy="api_key",
            default_model="claude-3-haiku-20240307",
            models=["claude-3-haiku-20240307", "claude-opus-4-1-20250805"],
            base_url="https://api.anthropic.com",
            api_key=payload.api_key,
        )
    else:
        current = ProviderDefaults(
            kind=current.kind,
            auth_strategy=current.auth_strategy,
            healthcheck_path=current.healthcheck_path,
            default_model=current.default_model,
            models=current.models,
            base_url=current.base_url,
            api_key=payload.api_key,
        )
    providers["anthropic"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.anthropic_validation_status()
    return {"stored": True, "anthropic": validation}


@router.delete("/config/anthropic-token", status_code=status.HTTP_200_OK)
async def delete_anthropic_token(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    config = service._runtime
    providers = config.provider_defaults.copy()
    current = providers.get("anthropic")
    if current is None:
        current = ProviderDefaults(
            kind="cloud",
            auth_strategy="api_key",
            default_model="claude-3-haiku-20240307",
            models=["claude-3-haiku-20240307", "claude-opus-4-1-20250805"],
            base_url="https://api.anthropic.com",
            api_key="",
        )
    else:
        current = ProviderDefaults(
            kind=current.kind,
            auth_strategy=current.auth_strategy,
            healthcheck_path=current.healthcheck_path,
            default_model=current.default_model,
            models=current.models,
            base_url=current.base_url,
            api_key="",
        )
    providers["anthropic"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.anthropic_validation_status()
    return {"stored": False, "anthropic": validation}


@router.put("/config/ollama-cloud-token", status_code=status.HTTP_200_OK)
async def put_ollama_cloud_token(
    payload: OpenAITokenUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    config = service._runtime
    providers = config.provider_defaults.copy()
    current = providers.get("ollama_cloud")
    if current is None:
        current = ProviderDefaults(
            kind="cloud",
            auth_strategy="api_key",
            default_model="gemma4:31b-cloud",
            models=["gemma4:31b-cloud"],
            base_url="https://ollama.com",
            api_key=payload.api_key,
        )
    else:
        current = ProviderDefaults(
            kind=current.kind,
            auth_strategy=current.auth_strategy,
            healthcheck_path=current.healthcheck_path,
            default_model=current.default_model,
            models=current.models,
            base_url=current.base_url,
            api_key=payload.api_key,
        )
    providers["ollama_cloud"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.provider_validation_status("ollama_cloud")
    return {"stored": True, "ollama_cloud": validation}


@router.delete("/config/ollama-cloud-token", status_code=status.HTTP_200_OK)
async def delete_ollama_cloud_token(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    config = service._runtime
    providers = config.provider_defaults.copy()
    current = providers.get("ollama_cloud")
    if current is None:
        current = ProviderDefaults(
            kind="cloud",
            auth_strategy="api_key",
            default_model="gemma4:31b-cloud",
            models=["gemma4:31b-cloud"],
            base_url="https://ollama.com",
            api_key="",
        )
    else:
        current = ProviderDefaults(
            kind=current.kind,
            auth_strategy=current.auth_strategy,
            healthcheck_path=current.healthcheck_path,
            default_model=current.default_model,
            models=current.models,
            base_url=current.base_url,
            api_key="",
        )
    providers["ollama_cloud"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.provider_validation_status("ollama_cloud")
    return {"stored": False, "ollama_cloud": validation}


@router.get(
    "/providers/anthropic/validate",
    response_model=AnthropicValidationResponse,
    status_code=status.HTTP_200_OK,
)
async def get_anthropic_validate(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> AnthropicValidationResponse:
    payload = await service.anthropic_validation_status()
    return AnthropicValidationResponse.model_validate(payload)


@router.get("/providers/auth-status", status_code=status.HTTP_200_OK)
async def get_providers_auth_status(
    service: ChatService = Depends(get_service),
) -> dict[str, object]:
    """Return whether each provider has auth configured. No auth required; no network calls made."""
    return service.providers_auth_status()


@router.get("/providers/validate", status_code=status.HTTP_200_OK)
async def get_providers_validate(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await service.all_provider_validation_statuses()


@router.get("/providers/{provider_id}/validate", status_code=status.HTTP_200_OK)
async def get_provider_validate(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await service.provider_validation_status(provider_id)


@router.put("/config/providers/{provider_id}", status_code=status.HTTP_200_OK)
async def update_provider_metadata(
    provider_id: str,
    payload: ProviderMetadataUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    current = service._runtime.provider_defaults.get(provider_key)
    if current is None:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    updates = payload.model_dump(exclude_none=True)
    providers = service._runtime.provider_defaults.copy()
    providers[provider_key] = ProviderDefaults(
        kind=str(updates.get("kind", current.kind)).strip() or current.kind,
        auth_strategy=str(updates.get("auth_strategy", current.auth_strategy)).strip() or current.auth_strategy,
        healthcheck_path=updates.get("healthcheck_path", current.healthcheck_path),
        default_model=str(updates.get("default_model", current.default_model)).strip() or current.default_model,
        models=updates.get("models", current.models),
        base_url=updates.get("base_url", current.base_url),
        api_key=current.api_key,
    )
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    return service.config_public_view()


@router.post("/config/providers/{provider_id}/models", status_code=status.HTTP_200_OK)
async def add_provider_model(
    provider_id: str,
    payload: ProviderModelMutationRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    model_id = payload.model.strip()
    if not provider_key:
        raise HTTPException(status_code=400, detail="provider_id is required")
    if not model_id:
        raise HTTPException(status_code=400, detail="model is required")

    cfg = service._runtime
    providers = cfg.provider_defaults.copy()
    current = providers.get(provider_key)
    if current is None:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    updated_models = list(current.models)
    if model_id not in updated_models:
        updated_models.append(model_id)
    current = ProviderDefaults(
        kind=current.kind,
        auth_strategy=current.auth_strategy,
        healthcheck_path=current.healthcheck_path,
        default_model=current.default_model or model_id,
        models=updated_models,
        base_url=current.base_url,
        api_key=current.api_key,
    )
    providers[provider_key] = current

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    return service.config_public_view()


@router.delete("/config/providers/{provider_id}/models", status_code=status.HTTP_200_OK)
async def remove_provider_model(
    provider_id: str,
    payload: ProviderModelMutationRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    model_id = payload.model.strip()
    if not provider_key:
        raise HTTPException(status_code=400, detail="provider_id is required")
    if not model_id:
        raise HTTPException(status_code=400, detail="model is required")

    cfg = service._runtime
    providers = cfg.provider_defaults.copy()
    current = providers.get(provider_key)
    if current is None:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")

    remaining_models = [m for m in current.models if m != model_id]
    if not remaining_models:
        raise HTTPException(status_code=400, detail="cannot remove last configured model for provider")
    default_model = current.default_model
    if default_model == model_id:
        default_model = remaining_models[0]
    providers[provider_key] = ProviderDefaults(
        kind=current.kind,
        auth_strategy=current.auth_strategy,
        healthcheck_path=current.healthcheck_path,
        default_model=default_model,
        models=remaining_models,
        base_url=current.base_url,
        api_key=current.api_key,
    )

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    return service.config_public_view()
