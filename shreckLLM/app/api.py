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
from app.service import PROVIDER_MODEL_FALLBACKS

router = APIRouter()

CONFIG_FIELD_META: dict[str, dict[str, object]] = {
    "provider_limits": {"type": "object", "help": "Per-provider limits. max_concurrent is required, positive, and always enforced.", "category": "Providers"},
    "memory_ttl_seconds": {"type": "integer", "help": "Conversation memory TTL in seconds.", "category": "Memory"},
    "memory_max_messages": {"type": "integer", "help": "Maximum messages stored per conversation.", "category": "Memory"},
    "max_concurrent_requests": {"type": "integer", "help": "Global concurrent request limit.", "category": "Concurrency"},
    "request_timeout_seconds": {
        "type": "number",
        "help": "Authoritative provider-attempt timeout in seconds for every LLM provider.",
        "category": "Concurrency",
        "frontend_editable": True,
        "derived_from_profile": False,
    },
    "max_queue_wait_seconds": {"type": "number", "help": "Maximum queue wait before rejection.", "category": "Concurrency"},
    "chat_job_queue_max_size": {"type": "integer", "help": "Maximum queued chat jobs.", "category": "Concurrency"},
    "chat_job_result_ttl_seconds": {"type": "integer", "help": "How long completed chat job results are retained.", "category": "Concurrency"},
    "chat_job_poll_default_interval_ms": {"type": "integer", "help": "Suggested polling interval for chat job status.", "category": "Concurrency"},
    "chat_job_max_retries": {
        "type": "integer",
        "help": "Authoritative retry count for retryable chat failures, applied equally to every model.",
        "category": "Concurrency",
        "frontend_editable": True,
        "derived_from_profile": False,
    },
}

CONFIG_GROUPS: list[dict[str, object]] = [
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


class ProviderConcurrencyUpdateRequest(BaseModel):
    max_concurrent: int = Field(ge=1)


class ProviderMetadataUpdateRequest(BaseModel):
    kind: str | None = None
    auth_strategy: str | None = None
    healthcheck_path: str | None = None
    base_url: str | None = None
    models: list[str] | None = None
    api_key: str | None = None


def get_service(request: Request) -> ChatService:
    return request.app.state.chat_service


async def _refresh_and_validate_providers(
    service: ChatService,
    provider_ids: list[str] | None = None,
    *,
    ping: bool = True,
) -> dict[str, object]:
    validation = await service.refresh_runtime_and_validate(provider_ids, ping=ping)
    return {
        "config": service.runtime_config_public_view(),
        "validation": validation,
    }


def _runtime_config_patch(payload: RuntimeConfigUpdate) -> dict[str, object]:
    patch = payload.model_dump(exclude_none=True)
    patch.pop("provider_defaults", None)
    patch.pop("provider_states", None)
    return patch


async def _validate_provider_models_before_save(
    provider_id: str,
    candidate: ProviderDefaults,
    service: ChatService,
) -> ProviderDefaults:
    validation = await service.validate_provider_models(provider_id, candidate)
    configured_models = validation.get("configured_models")
    if not isinstance(configured_models, list):
        configured_models = []
    if validation.get("valid") is not True:
        raise HTTPException(
            status_code=400,
            detail={
                "error": validation.get("error") or "invalid_provider_models",
                "provider_id": provider_id,
                "invalid_models": validation.get("invalid_models") or [],
                "discovered_models": validation.get("discovered_models") or [],
            },
        )
    return ProviderDefaults(
        kind=candidate.kind,
        auth_strategy=candidate.auth_strategy,
        healthcheck_path=candidate.healthcheck_path,
        models=[str(model) for model in configured_models],
        base_url=candidate.base_url,
        api_key=candidate.api_key,
        provider_type=candidate.provider_type,
        website_url=candidate.website_url,
    )


async def _update_provider(
    provider_id: str,
    payload: ProviderMetadataUpdateRequest,
    service: ChatService,
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    current = service._runtime.provider_defaults.get(provider_key)
    if current is None:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    updates = payload.model_dump(exclude_none=True)
    if current.provider_type == "needs_api" and "base_url" in updates:
        raise HTTPException(status_code=400, detail="base_url is managed by the provider default and cannot be changed")
    if current.provider_type == "needs_baseurl" and "api_key" in updates:
        raise HTTPException(status_code=400, detail="api_key is not supported for this provider")
    providers = service._runtime.provider_defaults.copy()
    candidate = ProviderDefaults(
        kind=str(updates.get("kind", current.kind)).strip() or current.kind,
        auth_strategy=str(updates.get("auth_strategy", current.auth_strategy)).strip() or current.auth_strategy,
        healthcheck_path=updates.get("healthcheck_path", current.healthcheck_path),
        models=updates.get("models", current.models),
        base_url=updates.get("base_url", current.base_url),
        api_key=updates.get("api_key", current.api_key),
        provider_type=current.provider_type,
        website_url=current.website_url,
    )
    try:
        providers[provider_key] = await _validate_provider_models_before_save(provider_key, candidate, service)
    except DependencyUnavailableError:
        # Provider unreachable (bad base_url, invalid API key, etc.) —
        # save the update anyway so the user can fix connectivity later.
        providers[provider_key] = candidate
    except HTTPException as exc:
        if exc.status_code == 400:
            # Models don't match the new config — save the update anyway,
            # the user can adjust models later.
            providers[provider_key] = candidate
        else:
            raise
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    validation = await service.refresh_runtime_and_validate([provider_key], ping=True)
    providers_payload = validation.get("providers") if isinstance(validation, dict) else {}
    provider_payload = providers_payload.get(provider_key) if isinstance(providers_payload, dict) else None
    if not isinstance(provider_payload, dict):
        provider_payload = await service.provider_validation_status(provider_key)
    operational_provider_ids = validation.get("operational_provider_ids") if isinstance(validation, dict) else []
    if not isinstance(operational_provider_ids, list):
        operational_provider_ids = []
    return {
        "provider": provider_payload,
        "shreckllm_operational": validation.get("shreckllm_operational") is True if isinstance(validation, dict) else False,
        "operational_provider_ids": [str(provider_id) for provider_id in operational_provider_ids],
    }


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
    except DependencyUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    return service.runtime_config_public_view()


@router.get("/config/schema", status_code=status.HTTP_200_OK)
async def get_config_schema(
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    all_fields = set(RuntimeConfigUpdate.model_fields.keys()) - {"provider_defaults", "provider_states"}
    grouped_fields: set[str] = set()
    for group in CONFIG_GROUPS:
        grouped_fields.update([f for f in group["fields"] if isinstance(f, str)])
    ungrouped_fields = sorted(all_fields - grouped_fields)

    field_meta = dict(CONFIG_FIELD_META)
    for field in sorted(all_fields):
        row = dict(field_meta.get(field, {}))
        row.setdefault("change_impact", "hot")
        row.setdefault("frontend_editable", True)
        if field in {"memory_ttl_seconds", "memory_max_messages", "max_concurrent_requests", "max_queue_wait_seconds", "chat_job_queue_max_size", "chat_job_result_ttl_seconds", "chat_job_poll_default_interval_ms"}:
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
    patch = _runtime_config_patch(payload)
    update_runtime_config(patch)
    reload_runtime_config()
    await service.refresh_runtime()
    return service.runtime_config_public_view()


@router.post("/config/reload", status_code=status.HTTP_200_OK)
async def post_config_reload(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    reload_runtime_config()
    validation = await service.refresh_runtime_and_validate()
    return {
        "reloaded": True,
        "config": service.config_public_view(),
        "validation": validation,
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
            models=["gpt-5-nano"],
            base_url="https://api.openai.com/v1",
            api_key=payload.api_key,
            provider_type="needs_api",
            website_url="https://platform.openai.com/api-keys",
        )
    else:
        openai_defaults = openai_defaults.model_copy(update={"api_key": payload.api_key})
    providers["openai"] = openai_defaults

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.test_provider_functionality("openai", ping=True)
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
            models=["gpt-5-nano"],
            base_url="https://api.openai.com/v1",
            api_key="",
            provider_type="needs_api",
            website_url="https://platform.openai.com/api-keys",
        )
    else:
        openai_defaults = openai_defaults.model_copy(update={"api_key": ""})
    providers["openai"] = openai_defaults

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.test_provider_functionality("openai", ping=True)
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
            models=["claude-3-haiku-20240307", "claude-opus-4-1-20250805"],
            base_url="https://api.anthropic.com",
            api_key=payload.api_key,
            provider_type="needs_api",
            website_url="https://console.anthropic.com/settings/keys",
        )
    else:
        current = current.model_copy(update={"api_key": payload.api_key})
    providers["anthropic"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.test_provider_functionality("anthropic", ping=True)
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
            models=["claude-3-haiku-20240307", "claude-opus-4-1-20250805"],
            base_url="https://api.anthropic.com",
            api_key="",
            provider_type="needs_api",
            website_url="https://console.anthropic.com/settings/keys",
        )
    else:
        current = current.model_copy(update={"api_key": ""})
    providers["anthropic"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.test_provider_functionality("anthropic", ping=True)
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
            models=PROVIDER_MODEL_FALLBACKS["ollama_cloud"],
            base_url="https://ollama.com",
            api_key=payload.api_key,
            provider_type="needs_api",
            website_url="https://ollama.com/settings/keys",
        )
    else:
        current = current.model_copy(update={"api_key": payload.api_key})
    providers["ollama_cloud"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.test_provider_functionality("ollama_cloud", ping=True)
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
            models=PROVIDER_MODEL_FALLBACKS["ollama_cloud"],
            base_url="https://ollama.com",
            api_key="",
            provider_type="needs_api",
            website_url="https://ollama.com/settings/keys",
        )
    else:
        current = current.model_copy(update={"api_key": ""})
    providers["ollama_cloud"] = current
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    validation = await service.test_provider_functionality("ollama_cloud", ping=True)
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


@router.put("/config/deepinfra-token", status_code=status.HTTP_200_OK)
async def put_deepinfra_token(
    payload: OpenAITokenUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    providers = service._runtime.provider_defaults.copy()
    current = providers.get("deepinfra") or ProviderDefaults(
        kind="cloud",
        auth_strategy="api_key",
        models=[],
        base_url="https://api.deepinfra.com/v1/openai",
        api_key="",
        provider_type="needs_api",
        website_url="https://deepinfra.com/dash/api_keys",
    )
    providers["deepinfra"] = current.model_copy(update={"api_key": payload.api_key})
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    if current.models:
        validation = await service.test_provider_functionality("deepinfra", ping=True)
    else:
        validation = await service.deepinfra_validation_status()
        await service.deactivate_provider("deepinfra")
    return {"stored": True, "deepinfra": validation}


@router.delete("/config/deepinfra-token", status_code=status.HTTP_200_OK)
async def delete_deepinfra_token(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    providers = service._runtime.provider_defaults.copy()
    current = providers.get("deepinfra")
    if current is None:
        raise HTTPException(status_code=404, detail="provider not found: deepinfra")
    providers["deepinfra"] = current.model_copy(update={"api_key": ""})
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    await service.deactivate_provider("deepinfra")
    return {"stored": False, "deepinfra": await service.deepinfra_validation_status()}


@router.get("/providers/deepinfra/validate", status_code=status.HTTP_200_OK)
async def get_deepinfra_validate(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await service.deepinfra_validation_status()


@router.put("/config/openrouter-token", status_code=status.HTTP_200_OK)
async def put_openrouter_token(
    payload: OpenAITokenUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    providers = service._runtime.provider_defaults.copy()
    current = providers.get("openrouter") or ProviderDefaults(
        kind="cloud",
        auth_strategy="api_key",
        models=[],
        base_url="https://openrouter.ai/api/v1",
        api_key="",
        provider_type="needs_api",
        website_url="https://openrouter.ai/settings/keys",
    )
    providers["openrouter"] = current.model_copy(update={"api_key": payload.api_key})
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    if current.models:
        validation = await service.test_provider_functionality("openrouter", ping=True)
    else:
        validation = await service.openrouter_validation_status()
        await service.deactivate_provider("openrouter")
    return {"stored": True, "openrouter": validation}


@router.delete("/config/openrouter-token", status_code=status.HTTP_200_OK)
async def delete_openrouter_token(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    providers = service._runtime.provider_defaults.copy()
    current = providers.get("openrouter")
    if current is None:
        raise HTTPException(status_code=404, detail="provider not found: openrouter")
    providers["openrouter"] = current.model_copy(update={"api_key": ""})
    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    await service.refresh_runtime()
    await service.deactivate_provider("openrouter")
    return {"stored": False, "openrouter": await service.openrouter_validation_status()}


@router.get("/providers/openrouter/validate", status_code=status.HTTP_200_OK)
async def get_openrouter_validate(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await service.openrouter_validation_status()


@router.get("/providers/validate", status_code=status.HTTP_200_OK)
async def get_providers_validate(
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await get_providers(service=service)


@router.get("/providers/{provider_id}/validate", status_code=status.HTTP_200_OK)
async def get_provider_validate(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await get_provider(provider_id=provider_id, service=service, _user=_user)


@router.get("/providers", status_code=status.HTTP_200_OK)
async def get_providers(
    service: ChatService = Depends(get_service),
) -> dict[str, object]:
    return await service.all_provider_validation_statuses()


@router.get("/providers/{provider_id}/models", status_code=status.HTTP_200_OK)
async def get_provider_models(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    if provider_key not in service._runtime.provider_defaults:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    try:
        return await service.provider_model_catalog(provider_key)
    except DependencyUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "provider_model_catalog_unavailable", "provider_id": provider_key},
        ) from exc


@router.get("/providers/{provider_id}", status_code=status.HTTP_200_OK)
async def get_provider(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    if provider_key not in service._runtime.provider_defaults:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    return await service.provider_validation_status(provider_key)


@router.put("/providers/{provider_id}", status_code=status.HTTP_200_OK)
async def put_provider(
    provider_id: str,
    payload: ProviderMetadataUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await _update_provider(provider_id, payload, service)


@router.post("/providers/{provider_id}/test", status_code=status.HTTP_200_OK)
async def test_provider_functionality(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder_or_internal),
) -> dict[str, object]:
    try:
        return await service.test_provider_functionality(provider_id, ping=True)
    except InvalidModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/config/providers/{provider_id}/activate", status_code=status.HTTP_200_OK)
async def activate_provider(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    try:
        status_payload = await service.activate_provider(provider_id)
    except InvalidModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DependencyUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"activated": True, "provider": status_payload}


@router.delete("/config/providers/{provider_id}/activate", status_code=status.HTTP_200_OK)
async def deactivate_provider(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    try:
        status_payload = await service.deactivate_provider(provider_id)
    except InvalidModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"activated": False, "provider": status_payload}


def _provider_limit_payload(provider_id: str, service: ChatService) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    if provider_key not in service._runtime.provider_defaults:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    limits = service._runtime.provider_limits.get(provider_key, {})
    max_concurrent = int(limits["max_concurrent"])
    return {
        "provider_id": provider_key,
        "max_concurrent": max_concurrent,
        "global_max_concurrent": service._runtime.max_concurrent_requests,
        "effective_max_concurrent": min(service._runtime.max_concurrent_requests, max_concurrent),
    }


@router.get("/config/providers/{provider_id}/limits", status_code=status.HTTP_200_OK)
async def get_provider_limits(
    provider_id: str,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return _provider_limit_payload(provider_id, service)


@router.put("/config/providers/{provider_id}/limits", status_code=status.HTTP_200_OK)
async def put_provider_limits(
    provider_id: str,
    payload: ProviderConcurrencyUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    provider_key = provider_id.strip().lower()
    if provider_key not in service._runtime.provider_defaults:
        raise HTTPException(status_code=404, detail=f"provider not found: {provider_key}")
    provider_limits = {
        configured_provider: dict(limits)
        for configured_provider, limits in service._runtime.provider_limits.items()
    }
    provider_limits.setdefault(provider_key, {})["max_concurrent"] = payload.max_concurrent
    update_runtime_config({"provider_limits": provider_limits})
    reload_runtime_config()
    await service.refresh_runtime()
    return _provider_limit_payload(provider_key, service)


@router.put("/config/providers/{provider_id}", status_code=status.HTTP_200_OK)
async def update_provider_metadata(
    provider_id: str,
    payload: ProviderMetadataUpdateRequest,
    service: ChatService = Depends(get_service),
    _user=Depends(get_admin_or_world_builder),
) -> dict[str, object]:
    return await _update_provider(provider_id, payload, service)


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
    candidate = ProviderDefaults(
        kind=current.kind,
        auth_strategy=current.auth_strategy,
        healthcheck_path=current.healthcheck_path,
        models=updated_models,
        base_url=current.base_url,
        api_key=current.api_key,
        provider_type=current.provider_type,
        website_url=current.website_url,
    )
    try:
        providers[provider_key] = await _validate_provider_models_before_save(provider_key, candidate, service)
    except DependencyUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "provider_model_catalog_unavailable", "provider_id": provider_key},
        ) from exc

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    return await _refresh_and_validate_providers(service, [provider_key])


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
    providers[provider_key] = ProviderDefaults(
        kind=current.kind,
        auth_strategy=current.auth_strategy,
        healthcheck_path=current.healthcheck_path,
        models=remaining_models,
        base_url=current.base_url,
        api_key=current.api_key,
        provider_type=current.provider_type,
        website_url=current.website_url,
    )

    update_runtime_config({"provider_defaults": providers})
    reload_runtime_config()
    return await _refresh_and_validate_providers(service, [provider_key])
