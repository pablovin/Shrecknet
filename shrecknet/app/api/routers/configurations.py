from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.api.agent_feature_gate import require_shreckllm_operational_for_agents_enable
from app.api.deps import get_current_active_admin_or_world_builder, get_current_admin_user
from app.celery_app import configure_celery_app
from app.core.config_store import (
    BOOTSTRAP_ENV_FIELDS,
    LLM_TARGET_FIELDS,
    LLMModelTarget,
    Settings,
    get_settings,
    reload_settings,
    update_settings,
)
from app.services.shreckllm_status_service import get_all_provider_validations
from app.services.email_service import EmailDeliveryError, EmailService, get_email_service_status
from app.schemas.user import PublicRegistrationConfig

router = APIRouter(prefix="/config", tags=["config"])
logger = logging.getLogger(__name__)

MAX_GOOGLE_SERVICE_ACCOUNT_BYTES = 1024 * 1024
GOOGLE_SERVICE_DIR = Path("secrets") / "google"
GOOGLE_SERVICE_FILENAME = "service_account.json"
SECRET_CONFIG_FIELDS = {"smtp_password", "smtp_service_token"}


def _first_available_llm_target(provider_validations: dict[str, Any]) -> LLMModelTarget | None:
    """Return the first active provider/model pair reported usable by shreckLLM."""
    providers = provider_validations.get("providers")
    if not isinstance(providers, dict):
        return None

    for provider_id in provider_validations.get("operational_provider_ids", []):
        provider_key = str(provider_id)
        provider = providers.get(provider_key)
        if not isinstance(provider, dict) or provider.get("active") is not True:
            continue
        models = provider.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            if isinstance(model, dict) and model.get("available") is True:
                name = str(model.get("model") or "").strip()
                if name:
                    return LLMModelTarget(provider=provider_key, name=name)
    return None


def _reconcile_llm_targets(
    current: Settings,
    updates: dict[str, Any],
    provider_validations: dict[str, Any],
) -> dict[str, Any]:
    """Replace only invalid configured targets with a known working target."""
    fallback = _first_available_llm_target(provider_validations)
    if fallback is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Enable Agents requires an active shreckLLM provider with an available model.",
        )

    available_pairs = {
        (str(provider_id), str(model.get("model")))
        for provider_id, provider in provider_validations.get("providers", {}).items()
        if isinstance(provider, dict) and provider.get("active") is True
        for model in provider.get("models", [])
        if isinstance(model, dict) and model.get("available") is True and str(model.get("model") or "").strip()
    }
    reconciled = dict(updates)
    for field_name in LLM_TARGET_FIELDS:
        raw_target = reconciled.get(field_name, getattr(current, field_name, None))
        if isinstance(raw_target, LLMModelTarget):
            provider, name = raw_target.provider, raw_target.name
        elif isinstance(raw_target, dict):
            provider = str(raw_target.get("provider") or "").strip()
            name = str(raw_target.get("name") or "").strip()
        else:
            provider, name = "", ""
        if (provider, name) not in available_pairs:
            reconciled[field_name] = fallback.model_dump()
    return reconciled

SETTINGS_GROUPS: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "label": "Runtime",
        "fields": [
            "app_name",
            "debug",
            "enable_ai_agents",
            "user_creation_mode",
            "email_verification_enabled",
            "email_service_configured",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_tls_mode",
            "smtp_sender_email",
            "smtp_sender_name",
            "email_verification_frontend_url",
            "email_verification_subject",
            "email_verification_text_template",
            "email_verification_html_template",
            "cors_allow_origins",
            "cors_allow_origin_regex",
            "cors_allow_credentials",
            "cors_allow_methods",
            "cors_allow_headers",
            "cors_max_age",
            "media_root",
            "media_base_url",
            "media_public_url",
            "event_publisher_mode",
            "event_webhook_url",
        ],
    },
    {
        "id": "database",
        "label": "Database",
        "fields": [
            "database_url",
            "jobs_database_url",
            "old_database_url",
            "neo4j_uri",
            "neo4j_user",
            "neo4j_password",
            "neo4j_database",
        ],
    },
    {
        "id": "celery_workers",
        "label": "Celery Workers",
        "fields": [
            "celery_broker_url",
            "celery_result_backend",
            "celery_task_always_eager",
            "celery_expires_architect_seconds",
            "celery_expires_novelist_seconds",
            "celery_expires_reconciliation_seconds",
            "celery_stale_reaper_enabled",
            "celery_stale_reaper_interval_seconds",
            "celery_stale_reaper_max_task_age_seconds",
        ],
    },
    {
        "id": "shreckllm_integration",
        "label": "ShreckLLM Integration",
        "fields": [
            "shreckllm_base_url",
            "shreckllm_request_timeout_s",
            "shreckllm_max_retries",
        ],
    },
    {
        "id": "agents_configurations",
        "label": "Agents Configurations",
        "fields": [
            "model_architect_scene_chunking",
            "model_architect_entity_proposal",
            "model_architect_milestone_proposal",
            "model_architect_entity_generation",
            "model_agents_repair_json",
            "model_elder",
            "default_top_k",
            "elder_embedding_inference_concurrency",
            "elder_query_embedding_timeout_s",
            "elder_embedding_warmup_on_worker_start",
            "elder_embedding_manager_enabled",
            "elder_embedding_queue_max_size",
            "elder_embedding_batch_max_size",
            "elder_embedding_batch_wait_ms",
            "elder_embedding_cache_size",
            "elder_embedding_request_timeout_s",
            "embedding_runtime_enabled",
            "embedding_runtime_queue_max_size",
            "embedding_runtime_batch_max_size",
            "embedding_runtime_batch_wait_ms",
            "embedding_runtime_cache_size",
            "embedding_runtime_request_timeout_s",
            "embedding_runtime_startup_timeout_s",
            "embedding_runtime_fail_open_health",
            "embedding_model_id",
            "embedding_dimension",
            "embedding_device",
            "embedding_chunk_size",
            "embedding_chunk_overlap",
            "model_novelist_planning",
            "model_novelist_prose",
            "model_novelist_critic",
            "model_librarian",
            "model_orchestrator_routing",
            "model_orchestrator_synthesis",
        ],
    },
    {
        "id": "upload_limits",
        "label": "Upload Limits",
        "fields": [
            "max_image_upload_bytes",
            "image_max_width",
            "image_max_height",
            "max_pdf_upload_bytes",
            "library_max_pdf_bytes",
        ],
    },
    {
        "id": "security_jwt",
        "label": "Security JWT",
        "fields": [
            "jwt_issuer",
            "jwt_audience",
            "jwt_kid",
            "jwt_access_token_expiry_minutes",
            "jwt_private_key_pem",
            "jwt_public_key_pem",
        ],
    },
]

FIELD_UI_META: dict[str, dict[str, Any]] = {
    "app_name": {"type": "string", "help": "Application name shown in logs and diagnostics."},
    "debug": {"type": "boolean", "help": "Enable debug behavior and verbose internals."},
    "enable_ai_agents": {"type": "boolean", "label": "Enable Agents", "help": "Global toggle for agent jobs. Can only be turned on when shreckLLM is operational."},
    "user_creation_mode": {"type": "enum", "label": "User creation mode", "options": ["stopped", "moderated", "allowed"], "help": "Controls whether new accounts are blocked, require approval, or are immediately approved."},
    "email_verification_enabled": {"type": "boolean", "label": "Require email verification", "help": "Require public registrants to confirm their email before sign-in."},
    "email_service_configured": {"type": "boolean", "frontend_editable": False, "change_impact": "locked", "label": "Email Service Configured", "help": "Whether Shrecknet can currently connect to and authenticate with the configured SMTP server."},
    "smtp_host": {"type": "string", "label": "SMTP host"},
    "smtp_port": {"type": "integer", "label": "SMTP port"},
    "smtp_username": {"type": "string", "label": "SMTP username"},
    "smtp_password": {"type": "string", "label": "SMTP password", "secret": True},
    "smtp_tls_mode": {"type": "enum", "label": "SMTP TLS", "options": ["starttls", "ssl", "none"]},
    "smtp_sender_email": {"type": "string", "label": "Sender email"},
    "smtp_sender_name": {"type": "string", "label": "Sender name"},
    "smtp_service_token": {"type": "string", "label": "SMTP service token", "secret": True, "frontend_editable": False, "change_impact": "locked", "help": "Credential used by trusted services to submit email through Shrecknet."},
    "email_verification_frontend_url": {"type": "string", "label": "Verification page URL"},
    "email_verification_subject": {"type": "string", "label": "Verification email subject"},
    "email_verification_text_template": {"type": "string", "label": "Verification text template", "multiline": True},
    "email_verification_html_template": {"type": "string", "label": "Verification HTML template", "multiline": True},
    "cors_allow_origins": {"type": "string_list", "multiline": True, "help": "Allowed CORS origins."},
    "cors_allow_origin_regex": {"type": "string", "help": "Regex for allowed dynamic origins."},
    "cors_allow_credentials": {"type": "boolean", "help": "Allow CORS credentials."},
    "cors_allow_methods": {"type": "string_list", "multiline": True, "help": "Allowed CORS methods."},
    "cors_allow_headers": {"type": "string_list", "multiline": True, "help": "Allowed CORS headers."},
    "cors_max_age": {"type": "integer", "help": "CORS preflight cache duration in seconds."},
    "media_root": {"type": "string", "help": "Filesystem path for stored media."},
    "media_base_url": {"type": "string", "help": "Base URL path used to serve media."},
    "media_public_url": {"type": "string", "nullable": True, "help": "Public absolute URL for media."},
    "event_publisher_mode": {"type": "string", "help": "Event publishing mode (e.g. logging/webhook)."},
    "event_webhook_url": {"type": "string", "nullable": True, "help": "Webhook URL when webhook mode is used."},
    "database_url": {"type": "string", "help": "Primary application database URL.", "requires_restart": True},
    "jobs_database_url": {"type": "string", "help": "Background jobs database URL.", "requires_restart": True},
    "old_database_url": {"type": "string", "help": "Legacy database URL for migration tooling."},
    "neo4j_uri": {"type": "string", "help": "Neo4j connection URI."},
    "neo4j_user": {"type": "string", "help": "Neo4j username."},
    "neo4j_password": {"type": "string", "secret": True, "help": "Neo4j password."},
    "neo4j_database": {"type": "string", "help": "Neo4j database name."},
    "celery_broker_url": {"type": "string", "help": "Celery broker URL."},
    "celery_result_backend": {"type": "string", "help": "Celery result backend URL."},
    "celery_task_always_eager": {"type": "boolean", "help": "Run Celery tasks synchronously in-process."},
    "celery_expires_architect_seconds": {"type": "integer", "help": "Architect task expiry in seconds."},
    "celery_expires_novelist_seconds": {"type": "integer", "help": "Novelist task expiry in seconds."},
    "celery_expires_reconciliation_seconds": {"type": "integer", "help": "Reconciliation task expiry in seconds."},
    "celery_stale_reaper_enabled": {"type": "boolean", "help": "Enable stale-task reaper."},
    "celery_stale_reaper_interval_seconds": {"type": "integer", "help": "Reaper interval in seconds."},
    "celery_stale_reaper_max_task_age_seconds": {"type": "integer", "help": "Task age threshold for reaper in seconds."},
    "shreckllm_base_url": {"type": "string", "help": "Base URL for shreckLLM service."},
    "shreckllm_request_timeout_s": {"type": "number", "help": "Request timeout when calling shreckLLM."},
    "shreckllm_max_retries": {"type": "integer", "help": "Retry attempts for shreckLLM calls."},
    "model_architect_scene_chunking": {"type": "llm_target", "help": "Provider/model target for scene chunking."},
    "model_architect_entity_proposal": {"type": "llm_target", "help": "Provider/model target for architect entity proposal."},
    "model_architect_milestone_proposal": {"type": "llm_target", "help": "Provider/model target for architect milestone proposal."},
    "model_architect_entity_generation": {"type": "llm_target", "help": "Provider/model target for architect entity generation."},
    "model_agents_repair_json": {"type": "llm_target", "help": "Provider/model target for shared JSON repair."},
    "model_elder": {"type": "llm_target", "help": "Provider/model target for elder responses."},
    "default_top_k": {"type": "integer", "help": "Default retrieval top-k."},
    "elder_embedding_inference_concurrency": {"type": "integer", "help": "Embedding inference concurrency."},
    "elder_query_embedding_timeout_s": {"type": "number", "help": "Elder query embedding timeout."},
    "elder_embedding_warmup_on_worker_start": {"type": "boolean", "help": "Warm embedding stack on worker startup."},
    "elder_embedding_manager_enabled": {"type": "boolean", "help": "Enable embedding manager."},
    "elder_embedding_queue_max_size": {"type": "integer", "help": "Embedding queue maximum size."},
    "elder_embedding_batch_max_size": {"type": "integer", "help": "Embedding batch maximum size."},
    "elder_embedding_batch_wait_ms": {"type": "integer", "help": "Embedding batch wait time in ms."},
    "elder_embedding_cache_size": {"type": "integer", "help": "Embedding cache size."},
    "elder_embedding_request_timeout_s": {"type": "number", "help": "Embedding request timeout."},
    "embedding_runtime_enabled": {"type": "boolean", "help": "Enable embedding runtime."},
    "embedding_runtime_queue_max_size": {"type": "integer", "help": "Embedding runtime queue max size."},
    "embedding_runtime_batch_max_size": {"type": "integer", "help": "Embedding runtime batch max size."},
    "embedding_runtime_batch_wait_ms": {"type": "integer", "help": "Embedding runtime batch wait ms."},
    "embedding_runtime_cache_size": {"type": "integer", "help": "Embedding runtime cache size."},
    "embedding_runtime_request_timeout_s": {"type": "number", "help": "Embedding runtime request timeout."},
    "embedding_runtime_startup_timeout_s": {"type": "number", "help": "Embedding runtime startup timeout."},
    "embedding_runtime_fail_open_health": {"type": "boolean", "help": "Health checks fail-open behavior."},
    "embedding_model_id": {"type": "string", "help": "Embedding model identifier."},
    "embedding_dimension": {"type": "integer", "help": "Embedding vector dimension."},
    "embedding_device": {"type": "string", "help": "Embedding execution device."},
    "embedding_chunk_size": {"type": "integer", "help": "Chunk size for embedding pipeline."},
    "embedding_chunk_overlap": {"type": "integer", "help": "Chunk overlap for embedding pipeline."},
    "model_novelist_planning": {"type": "llm_target", "help": "Provider/model target for novelist planning stages (step2+step4)."},
    "model_novelist_prose": {"type": "llm_target", "help": "Provider/model target for novelist prose stages (step5+step7)."},
    "model_novelist_critic": {"type": "llm_target", "help": "Provider/model target for novelist critic stage (step6)."},
    "model_librarian": {"type": "llm_target", "help": "Provider/model target for librarian."},
    "librarian_debug_artifacts_enabled": {"type": "boolean", "help": "Write Librarian local-test JSON artifacts and manifests."},
    "model_orchestrator_routing": {"type": "llm_target", "help": "Provider/model target for companion orchestrator routing classifier."},
    "model_orchestrator_synthesis": {"type": "llm_target", "help": "Provider/model target for companion orchestrator final synthesis."},
    "max_image_upload_bytes": {"type": "integer", "help": "Maximum image upload size in bytes."},
    "image_max_width": {"type": "integer", "help": "Maximum image width in pixels."},
    "image_max_height": {"type": "integer", "help": "Maximum image height in pixels."},
    "max_pdf_upload_bytes": {"type": "integer", "help": "Maximum PDF upload size in bytes."},
    "library_max_pdf_bytes": {"type": "integer", "help": "Maximum total PDF size per library in bytes."},
    "jwt_issuer": {"type": "string", "help": "JWT issuer claim."},
    "jwt_audience": {"type": "string", "help": "JWT audience claim."},
    "jwt_kid": {"type": "string", "help": "JWT key identifier."},
    "jwt_access_token_expiry_minutes": {"type": "integer", "help": "Access token lifetime in minutes."},
    "jwt_private_key_pem": {"type": "string", "secret": True, "multiline": True, "help": "Private key PEM."},
    "jwt_public_key_pem": {"type": "string", "multiline": True, "help": "Public key PEM."},
}

# Settings that should not be editable from the frontend runtime config UI because they
# are bootstrap/infrastructure concerns and typically require service re-provisioning.
FRONTEND_LOCKED_FIELDS: set[str] = {
    "email_service_configured",
    "smtp_service_token",
    "cors_allow_origins",
    "cors_allow_origin_regex",
    "cors_allow_credentials",
    "cors_allow_methods",
    "cors_allow_headers",
    "cors_max_age",
    "database_url",
    "jobs_database_url",
    "neo4j_uri",
    "neo4j_user",
    "neo4j_password",
    "neo4j_database",
    "celery_broker_url",
    "celery_result_backend",
    "jwt_private_key_pem",
    "jwt_public_key_pem",
}

RESTART_REQUIRED_FIELDS: set[str] = {
    field
    for field, meta in FIELD_UI_META.items()
    if bool(meta.get("requires_restart"))
}
RESTART_REQUIRED_FIELDS.update(
    {
        "shreckllm_base_url",
        "neo4j_uri",
        "neo4j_user",
        "neo4j_password",
        "neo4j_database",
        "celery_broker_url",
        "celery_result_backend",
        "jwt_issuer",
        "jwt_audience",
        "jwt_kid",
        "jwt_access_token_expiry_minutes",
        "jwt_private_key_pem",
        "jwt_public_key_pem",
    }
)


def _validate_updates(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(Settings.model_fields)
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown config keys: {', '.join(sorted(unknown))}",
        )
    merged = get_settings().model_dump()
    merged.update(payload)
    if merged["email_verification_enabled"]:
        required = ("smtp_host", "smtp_sender_email", "email_verification_frontend_url")
        missing = [field for field in required if not str(merged[field]).strip()]
        if missing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Email verification requires: {', '.join(missing)}")
        if merged["smtp_tls_mode"] not in {"starttls", "ssl", "none"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="smtp_tls_mode must be starttls, ssl, or none")
    return payload


def _google_service_account_path(settings: Settings) -> Path:
    base = Path(settings.media_root)
    target_dir = base / GOOGLE_SERVICE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / GOOGLE_SERVICE_FILENAME


async def _save_google_service_account(
    upload: UploadFile,
    settings: Settings,
) -> Path:
    if not upload.filename:
        logger.warning("Google Calendar service account upload missing filename")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing filename for uploaded service account JSON",
        )
    contents = await upload.read()
    if len(contents) > MAX_GOOGLE_SERVICE_ACCOUNT_BYTES:
        logger.warning(
            "Google Calendar service account upload too large: %d bytes",
            len(contents),
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Service account JSON exceeds size limit",
        )
    try:
        json.loads(contents)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Google Calendar service account upload invalid JSON: %s",
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload for service account",
        ) from exc

    target_path = _google_service_account_path(settings)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "wb") as handle:
        handle.write(contents)
    try:
        os.chmod(target_path, 0o600)
    except OSError:
        # Best-effort on platforms without chmod support
        pass
    return target_path


def _get_config_payload() -> dict[str, Any]:
    payload = get_settings().model_dump()
    for key in BOOTSTRAP_ENV_FIELDS:
        payload.pop(key, None)
    for key in SECRET_CONFIG_FIELDS:
        if key in payload:
            payload[key] = ""
    payload["email_service_configured"] = bool(get_email_service_status()["configured"])
    return payload


@router.get("/public", response_model=PublicRegistrationConfig)
def get_public_registration_config() -> PublicRegistrationConfig:
    """Expose the registration mode without disclosing protected configuration."""
    settings = get_settings()
    return PublicRegistrationConfig(user_creation_mode=settings.user_creation_mode, email_verification_required=getattr(settings, "email_verification_enabled", False))


def _google_service_account_metadata(settings: Settings) -> dict[str, Any]:
    path = _google_service_account_path(settings)
    exists = path.exists() and path.is_file()
    return {
        "service_account_configured": exists,
        "service_account_path": str(path) if exists else None,
    }


@router.get(
    "",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_config_no_slash() -> dict[str, Any]:
    return _get_config_payload()


@router.get(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def get_config() -> dict[str, Any]:
    return _get_config_payload()


@router.get("/email-service/status", dependencies=[Depends(get_current_admin_user)])
def get_email_service_config_status() -> dict[str, object]:
    return get_email_service_status()


@router.post("/email-service/test", dependencies=[Depends(get_current_admin_user)])
async def test_email_service() -> dict[str, object]:
    settings = get_settings()
    result = await EmailService(settings).validate_and_record_status()
    if settings.email_verification_enabled and not result["configured"]:
        update_settings({"email_verification_enabled": False})
    return result


@router.get("/email-service/token", dependencies=[Depends(get_current_admin_user)])
def get_email_service_token() -> dict[str, str | bool]:
    """Reveal the service credential only to an authenticated administrator."""
    token = str(get_settings().smtp_service_token or "")
    return {"configured": bool(token), "token": token}


@router.post("/email-service/token", dependencies=[Depends(get_current_admin_user)])
def generate_email_service_token() -> dict[str, str | bool]:
    """Replace the service credential and return it once for secure copying."""
    token = secrets.token_urlsafe(32)
    update_settings({"smtp_service_token": token})
    return {"configured": True, "token": token}


@router.get(
    "/schema",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def get_config_schema() -> dict[str, Any]:
    all_fields = set(Settings.model_fields) | {"email_service_configured"}
    visible_fields = all_fields - set(BOOTSTRAP_ENV_FIELDS)
    grouped_fields: set[str] = set()
    for group in SETTINGS_GROUPS:
        grouped_fields.update([f for f in group["fields"] if f in visible_fields])
    ungrouped = sorted(visible_fields - grouped_fields)
    editable_fields = visible_fields - FRONTEND_LOCKED_FIELDS
    runtime_changeable_fields = sorted(editable_fields - RESTART_REQUIRED_FIELDS)
    restart_required_fields = sorted(editable_fields & RESTART_REQUIRED_FIELDS)

    group_definitions: list[dict[str, Any]] = [
        {"id": "app_runtime", "label": "App Runtime", "fields": ["app_name", "debug", "event_publisher_mode", "event_webhook_url"]},
        {"id": "user_access", "label": "User Access", "fields": ["user_creation_mode", "email_verification_enabled", "email_service_configured", "smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_tls_mode", "smtp_sender_email", "smtp_sender_name", "email_verification_frontend_url", "email_verification_subject", "email_verification_text_template", "email_verification_html_template"]},
        {"id": "media_uploads", "label": "Media & Uploads", "fields": ["media_root", "media_base_url", "media_public_url", "max_image_upload_bytes", "image_max_width", "image_max_height", "max_pdf_upload_bytes", "library_max_pdf_bytes"]},
        {"id": "background_workers", "label": "Background Workers", "fields": ["celery_task_always_eager", "celery_expires_architect_seconds", "celery_expires_novelist_seconds", "celery_expires_reconciliation_seconds", "celery_stale_reaper_enabled", "celery_stale_reaper_interval_seconds", "celery_stale_reaper_max_task_age_seconds"]},
        {"id": "ai_agents", "label": "AI Agents", "fields": ["enable_ai_agents", "shreckllm_base_url"]},
        {"id": "architect", "label": "Architect Agent", "fields": ["model_architect_scene_chunking", "model_architect_entity_proposal", "model_architect_milestone_proposal", "model_architect_entity_generation", "model_agents_repair_json"]},
        {"id": "elder", "label": "Elder Agent", "fields": ["model_elder", "default_top_k", "elder_embedding_inference_concurrency", "elder_query_embedding_timeout_s", "elder_embedding_warmup_on_worker_start", "elder_embedding_manager_enabled", "elder_embedding_queue_max_size", "elder_embedding_batch_max_size", "elder_embedding_batch_wait_ms", "elder_embedding_cache_size", "elder_embedding_request_timeout_s", "embedding_runtime_enabled", "embedding_runtime_queue_max_size", "embedding_runtime_batch_max_size", "embedding_runtime_batch_wait_ms", "embedding_runtime_cache_size", "embedding_runtime_request_timeout_s", "embedding_runtime_startup_timeout_s", "embedding_runtime_fail_open_health", "embedding_model_id", "embedding_dimension", "embedding_device", "embedding_chunk_size", "embedding_chunk_overlap"]},
        {"id": "novelist", "label": "Novelist Agent", "fields": ["model_novelist_planning", "model_novelist_prose", "model_novelist_critic"]},
        {"id": "librarian", "label": "Librarian Agent", "fields": ["model_librarian", "librarian_debug_artifacts_enabled"]},
        {"id": "security_tokens", "label": "Security Tokens", "fields": ["jwt_issuer", "jwt_audience", "jwt_kid", "jwt_access_token_expiry_minutes"]},
        {"id": "legacy_migration", "label": "Legacy Migration", "fields": ["old_database_url"]},
    ]

    groups: list[dict[str, Any]] = []
    for definition in group_definitions:
        fields = [f for f in definition["fields"] if f in visible_fields]
        if not fields:
            continue
        property_value = "runtime"
        if all(f in restart_required_fields for f in fields):
            property_value = "restart_required"
        groups.append(
            {
                "id": definition["id"],
                "label": definition["label"],
                "fields": fields,
                "property": property_value,
            }
        )

    field_meta = dict(FIELD_UI_META)
    for field in runtime_changeable_fields:
        row = dict(field_meta.get(field, {}))
        row["change_impact"] = "hot"
        row.setdefault("frontend_editable", True)
        field_meta[field] = row
    for field in restart_required_fields:
        row = dict(field_meta.get(field, {}))
        row["change_impact"] = "service_restart"
        row.setdefault("frontend_editable", True)
        field_meta[field] = row
    for field in sorted(FRONTEND_LOCKED_FIELDS & visible_fields):
        row = dict(field_meta.get(field, {}))
        row["frontend_editable"] = False
        row.setdefault("change_impact", "locked")
        field_meta[field] = row

    return {
        "version": 2,
        "groups": groups,
        "field_meta": {k: v for k, v in field_meta.items() if k in visible_fields},
        "frontend_locked_fields": sorted(FRONTEND_LOCKED_FIELDS & visible_fields),
        "ungrouped_fields": ungrouped,
    }


async def _put_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Config payload must be an object",
        )
    # A blank secret returned by GET means "keep the stored secret", not erase it.
    payload = dict(payload)
    if payload.get("smtp_password") == "":
        payload.pop("smtp_password")
    updates = _validate_updates(payload)
    updates = {k: v for k, v in updates.items() if k not in BOOTSTRAP_ENV_FIELDS}
    current_settings = get_settings()
    candidate = current_settings.model_copy(update=updates)
    smtp_fields = {"smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_tls_mode", "smtp_sender_email"}
    if smtp_fields.intersection(updates) or updates.get("email_verification_enabled") is True:
        status_result = await EmailService(candidate).validate_and_record_status()
        if updates.get("email_verification_enabled") is True and not status_result["configured"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SMTP configuration could not be verified; email verification was not enabled.",
            )
        if not status_result["configured"] and current_settings.email_verification_enabled:
            # SMTP changes must never leave verification enabled without a working
            # delivery path. Keep the edited SMTP values, but disable verification.
            updates["email_verification_enabled"] = False
    if updates.get("enable_ai_agents") is True and not current_settings.enable_ai_agents:
        await require_shreckllm_operational_for_agents_enable()
        provider_validations = await get_all_provider_validations(current_settings)
        updates = _reconcile_llm_targets(current_settings, updates, provider_validations)
    settings = update_settings(updates)
    configure_celery_app()
    # Recheck after saving so the global state always describes the active config.
    if smtp_fields.intersection(updates) or updates.get("email_verification_enabled") is not None:
        status_result = await EmailService(settings).validate_and_record_status()
        if settings.email_verification_enabled and not status_result["configured"]:
            update_settings({"email_verification_enabled": False})
    return _get_config_payload()


@router.put(
    "",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def put_config_no_slash(payload: dict[str, Any]) -> dict[str, Any]:
    return await _put_config_payload(payload)


@router.put(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
async def put_config(payload: dict[str, Any]) -> dict[str, Any]:
    return await _put_config_payload(payload)


@router.post(
    "/reload",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
async def reload_config() -> dict[str, Any]:
    settings = reload_settings()
    configure_celery_app()
    status_result = await EmailService(settings).validate_and_record_status()
    if settings.email_verification_enabled and not status_result["configured"]:
        update_settings({"email_verification_enabled": False})
    return _get_config_payload()


@router.post(
    "/google-service-account",
    dependencies=[Depends(get_current_active_admin_or_world_builder)],
    status_code=status.HTTP_200_OK,
)
async def upload_google_service_account(
    file: UploadFile = File(...),
    request: Request = None,
) -> dict[str, Any]:
    settings = get_settings()
    if request is not None:
        logger.info(
            "Google Calendar service account upload headers: %s",
            dict(request.headers),
        )
    try:
        stored_path = await _save_google_service_account(file, settings)
    except HTTPException as exc:
        logger.warning("Google service account upload failed: %s (filename=%s, content_type=%s)", exc.detail, file.filename, file.content_type)
        raise
    return {
        "service_account_configured": True,
        "service_account_path": str(stored_path),
    }


@router.get(
    "/google-service-account",
    dependencies=[Depends(get_current_active_admin_or_world_builder)],
    status_code=status.HTTP_200_OK,
)
def get_google_service_account_metadata() -> dict[str, Any]:
    settings = get_settings()
    return _google_service_account_metadata(settings)


@router.get(
    "/google-service-account/file",
    dependencies=[Depends(get_current_active_admin_or_world_builder)],
    status_code=status.HTTP_200_OK,
)
def download_google_service_account() -> FileResponse:
    settings = get_settings()
    path = _google_service_account_path(settings)
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google service account file not found",
        )
    return FileResponse(
        path,
        media_type="application/json",
        filename=path.name,
    )
