from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_active_admin_or_world_builder, get_current_admin_user
from app.celery_app import configure_celery_app
from app.core.config_store import Settings, get_settings, reload_settings, update_settings

router = APIRouter(prefix="/config", tags=["config"])
logger = logging.getLogger(__name__)

MAX_GOOGLE_SERVICE_ACCOUNT_BYTES = 1024 * 1024
GOOGLE_SERVICE_DIR = Path("secrets") / "google"
GOOGLE_SERVICE_FILENAME = "service_account.json"

SETTINGS_GROUPS: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "label": "Runtime",
        "fields": [
            "app_name",
            "debug",
            "enable_ai_agents",
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
            "model_architect",
            "architect_scene_entity_extraction_concurrency",
            "architect_milestone_extraction_concurrency",
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
            "model_novelist",
            "model_novelist_draft",
            "novelist_elder_query_concurrency",
            "novelist_elder_query_timeout_s",
            "model_librarian",
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
    "enable_ai_agents": {"type": "boolean", "help": "Global toggle for AI agent features."},
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
    "model_architect": {"type": "llm_target", "help": "Provider/model target for architect extraction."},
    "architect_scene_entity_extraction_concurrency": {"type": "integer", "help": "Architect scene extraction parallelism."},
    "architect_milestone_extraction_concurrency": {"type": "integer", "help": "Architect milestone extraction parallelism."},
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
    "model_novelist": {"type": "llm_target", "help": "Provider/model target for novelist."},
    "model_novelist_draft": {"type": "llm_target", "help": "Provider/model target for novelist draft mode."},
    "novelist_elder_query_concurrency": {"type": "integer", "help": "Novelist elder query concurrency."},
    "novelist_elder_query_timeout_s": {"type": "integer", "help": "Novelist elder query timeout."},
    "model_librarian": {"type": "llm_target", "help": "Provider/model target for librarian."},
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


def _validate_updates(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(Settings.model_fields)
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown config keys: {', '.join(sorted(unknown))}",
        )
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
    return get_settings().model_dump()


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


@router.get(
    "/schema",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def get_config_schema() -> dict[str, Any]:
    all_fields = set(Settings.model_fields)
    grouped_fields: set[str] = set()
    for group in SETTINGS_GROUPS:
        grouped_fields.update(group["fields"])
    ungrouped = sorted(all_fields - grouped_fields)
    return {
        "version": 1,
        "groups": SETTINGS_GROUPS,
        "field_meta": FIELD_UI_META,
        "ungrouped_fields": ungrouped,
    }


def _put_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Config payload must be an object",
        )
    updates = _validate_updates(payload)
    settings = update_settings(updates)
    configure_celery_app()
    return settings.model_dump()


@router.put(
    "",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def put_config_no_slash(payload: dict[str, Any]) -> dict[str, Any]:
    return _put_config_payload(payload)


@router.put(
    "/",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def put_config(payload: dict[str, Any]) -> dict[str, Any]:
    return _put_config_payload(payload)


@router.post(
    "/reload",
    dependencies=[Depends(get_current_admin_user)],
    status_code=status.HTTP_200_OK,
)
def reload_config() -> dict[str, Any]:
    settings = reload_settings()
    configure_celery_app()
    return settings.model_dump()


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
