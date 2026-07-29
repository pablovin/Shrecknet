import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
from urllib.parse import unquote
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routers import (
    agents,
    architect,
    audit_logs,
    auth,
    backups,
    character_agents,
    configurations,
    contracts,
    elder,
    elder_chats,
    events,
    graphrag,
    jobs,
    llm_status,
    librarian,
    libraries,
    media,
    media_admin,
    novelist,
    ontologies,
    ontology_instances,
    personal_companion_agents,
    setup,
    service_email,
    users,
    worlds,
)
from app.services.pdf_embedding_service import PdfEmbeddingService
from app.core.config_store import get_settings, update_settings
from app.core.config_store import LLMModelTarget
from app.celery_queue_reaper import reset_jobs_and_queues_on_startup
from app.db.init_db import init_db_async
from app.db.init_jobs_db import init_jobs_db
from app.db.jobs_session import get_jobs_engine
from app.graphrag.embedding_service import get_embedding_model
from app.graphrag.embedding_runtime import (
    get_embedding_runtime_status,
    start_embedding_runtime_background,
    stop_embedding_runtime,
)
from app.graph.neo4j import (
    ensure_character_graph_constraints,
    ensure_elder_hybrid_indexes,
    ensure_temporal_graph_constraints,
    get_driver,
)
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.librarian.retrieval_strategies import preload_librarian_reranker
from app.services.shreckllm_status_service import get_all_provider_validations


logger = logging.getLogger(__name__)
_embedding_prewarm_task: asyncio.Task | None = None


def _effective_cors_origins(origins: list[str]) -> list[str]:
    local_defaults = [
        "http://localhost",
        "http://localhost:80",
        "http://127.0.0.1",
        "http://127.0.0.1:80",
    ]
    merged = [origin.strip() for origin in origins if origin and origin.strip()]
    for origin in local_defaults:
        if origin not in merged:
            merged.append(origin)
    return merged


def _origin_matches(origin: str, allowed_origins: list[str], allowed_regex: str | None) -> bool:
    if origin in allowed_origins:
        return True
    if allowed_regex:
        try:
            return re.match(allowed_regex, origin) is not None
        except re.error:
            return False
    return False


def _sqlite_dir_from_url(url: str) -> Path | None:
    prefixes = ("sqlite:///", "sqlite+aiosqlite:///")
    for prefix in prefixes:
        if not url.startswith(prefix):
            continue
        raw_path = unquote(url[len(prefix) :])
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute():
            path = path.resolve()
        return path.parent
    return None


def _probe_storage_path(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_file = path / f".startup-perm-probe-{uuid4().hex}"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
        return True, "ok"
    except OSError as exc:
        return False, str(exc)


def _log_storage_diagnostics(settings) -> None:
    uid = getattr(os, "getuid", lambda: -1)()
    gid = getattr(os, "getgid", lambda: -1)()

    candidate_paths: list[Path] = [Path("/data"), Path(settings.media_root)]
    for url in (settings.database_url, settings.jobs_database_url):
        sqlite_dir = _sqlite_dir_from_url(url)
        if sqlite_dir is not None:
            candidate_paths.append(sqlite_dir)

    seen: set[Path] = set()
    for path in candidate_paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)

        writable, reason = _probe_storage_path(resolved)
        try:
            stat_result = resolved.stat()
            owner = f"{stat_result.st_uid}:{stat_result.st_gid}"
            mode = oct(stat_result.st_mode & 0o7777)
        except OSError:
            owner = "unknown"
            mode = "unknown"

        level = logging.INFO if writable else logging.WARNING
        logger.log(
            level,
            "[startup-storage] uid=%s gid=%s path=%s owner=%s mode=%s writable=%s detail=%s",
            uid,
            gid,
            resolved,
            owner,
            mode,
            writable,
            reason,
        )


async def _run_embedding_prewarm() -> None:
    started = asyncio.get_running_loop().time()
    logger.info("embedding_prewarm_start")
    try:
        settings = get_settings()
        if settings.embedding_runtime_enabled:
            start_embedding_runtime_background()
            duration_s = asyncio.get_running_loop().time() - started
            logger.info("embedding_prewarm_done duration_s=%.3f", duration_s)
            return
        loop = asyncio.get_running_loop()

        def _warm() -> None:
            model = get_embedding_model()
            model.encode(
                ["startup prewarm"],
                convert_to_numpy=True,
                normalize_embeddings=True,
            )

        await loop.run_in_executor(None, _warm)
        duration_s = asyncio.get_running_loop().time() - started
        logger.info("embedding_prewarm_done duration_s=%.3f", duration_s)
    except Exception as exc:
        logger.warning("embedding_prewarm_failed error=%s", exc, exc_info=True)


def _start_embedding_prewarm() -> asyncio.Task | None:
    global _embedding_prewarm_task
    if _embedding_prewarm_task is not None and not _embedding_prewarm_task.done():
        return _embedding_prewarm_task
    try:
        _embedding_prewarm_task = asyncio.create_task(_run_embedding_prewarm())
    except RuntimeError:
        # No running loop available at this point; startup must remain resilient.
        logger.warning("embedding_prewarm_failed error=no_running_event_loop")
        _embedding_prewarm_task = None
    return _embedding_prewarm_task


def _target_key(target: LLMModelTarget | str) -> str:
    if isinstance(target, LLMModelTarget):
        return f"{target.provider}:{target.name}"
    return f"openai:{target}"


async def _run_llm_prewarm() -> None:
    started = asyncio.get_running_loop().time()
    settings = get_settings()
    if not settings.llm_prewarm_on_startup:
        logger.info("llm_prewarm_skipped reason=disabled")
        return
    try:
        provider_validation = await get_all_provider_validations(settings)
    except Exception as exc:
        logger.warning("llm_prewarm_skipped reason=runtime_unavailable error=%s", exc)
        return

    operational_provider_ids = (
        provider_validation.get("operational_provider_ids")
        if isinstance(provider_validation, dict)
        else None
    )
    active_provider_ids = {
        str(provider_id)
        for provider_id in (
            operational_provider_ids if isinstance(operational_provider_ids, list) else []
        )
    }
    if not active_provider_ids:
        logger.info("llm_prewarm_skipped reason=no_active_providers")
        return

    # Warm every agent model used by the Elder, Librarian, Architect, and
    # Novelist pipelines. Duplicate provider/model targets are loaded once.
    targets: list[LLMModelTarget] = [
        settings.model_elder_planner,
        settings.model_elder_synthesis,
        getattr(settings, "model_elder_character_incorporation", settings.model_elder_synthesis),
        settings.model_librarian_planner,
        settings.model_librarian_synthesis,
        getattr(settings, "model_librarian_character_incorporation", settings.model_librarian_synthesis),
        getattr(settings, "model_character_agent_framing", settings.model_elder_planner),
        getattr(settings, "model_character_agent_deliberation", settings.model_elder_planner),
        getattr(settings, "model_character_agent_character_incorporation", settings.model_elder_planner),
        getattr(settings, "model_character_agent_scene_interpretation", settings.model_elder_planner),
        getattr(settings, "model_character_agent_update", settings.model_elder_planner),
        settings.model_agents_repair_json,
        settings.model_architect_scene_chunking,
        settings.model_architect_entity_proposal,
        settings.model_architect_milestone_proposal,
        settings.model_architect_entity_generation,
        settings.model_novelist_planning,
        settings.model_novelist_prose,
        settings.model_novelist_critic,
    ]
    unique: dict[str, LLMModelTarget] = {}
    for target in targets:
        if not target.provider.strip() or not target.name.strip() or target.provider not in active_provider_ids:
            continue
        unique[_target_key(target)] = target
    if not unique:
        logger.info("llm_prewarm_skipped reason=no_active_targets")
        return
    logger.info(
        "llm_prewarm_start configured_targets=%d unique_targets=%d models=%s",
        len(targets),
        len(unique),
        list(unique.keys()),
    )
    client = ShreckLLMClient(
        base_url=settings.shreckllm_base_url,
        timeout=max(settings.llm_prewarm_timeout_s, settings.shreckllm_request_timeout_s),
        max_retries=0,
    )
    try:
        for key, target in unique.items():
            try:
                await asyncio.wait_for(
                    client.chat(
                        model=target,
                        messages=[{"role": "user", "content": "ping"}],
                        temperature=0.0,
                        usage_tag="startup_model_prewarm",
                    ),
                    timeout=max(1.0, settings.llm_prewarm_timeout_s),
                )
                logger.info("llm_prewarm_done model=%s", key)
            except Exception as exc:
                logger.warning("llm_prewarm_failed model=%s error=%s", key, exc)
    finally:
        await client.aclose()
    duration_s = asyncio.get_running_loop().time() - started
    logger.info("llm_prewarm_finished duration_s=%.3f", duration_s)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _log_storage_diagnostics(get_settings())
    await init_db_async()
    init_jobs_db(get_jobs_engine())
    try:
        reset_jobs_and_queues_on_startup()
    except Exception:
        logger.exception("Unable to run startup Celery cleanup")
    settings = get_settings()
    from app.services.email_service import EmailService
    email_status = await EmailService(settings).validate_and_record_status()
    if settings.email_verification_enabled and not email_status["configured"]:
        update_settings({"email_verification_enabled": False})
    _start_embedding_prewarm()
    # Gate readiness on interactive query-model warmup. Failures are isolated
    # inside the warmup and do not prevent the API from starting.
    await _run_llm_prewarm()
    try:
        await preload_librarian_reranker()
    except Exception:
        # Retrieval remains available through deterministic RRF fallback.
        logger.exception("Unable to preload Librarian cross-encoder; using RRF fallback")
    try:
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as neo4j_session:
            # Startup intentionally clears all jobs/queues. Any lock from a
            # terminated Librarian ingestion is therefore stale as well.
            lock_result = await neo4j_session.run(
                "MATCH (lock:PdfIngestionLock) DELETE lock RETURN count(lock) AS deleted"
            )
            lock_record = await lock_result.single()
            if lock_record and int(lock_record["deleted"] or 0):
                logger.warning("Cleared %s stale Librarian ingestion locks on startup", lock_record["deleted"])
            await ensure_temporal_graph_constraints(neo4j_session)
            await ensure_character_graph_constraints(neo4j_session)
            await ensure_elder_hybrid_indexes(neo4j_session)
            await PdfEmbeddingService(neo4j_session).ensure_vector_index()
    except Exception:
        logger.exception("Unable to ensure Neo4j startup graph/index state")
    try:
        yield
    finally:
        try:
            await stop_embedding_runtime()
        except Exception:
            logger.exception("Unable to stop embedding runtime during shutdown")


app = FastAPI(title=get_settings().app_name, version="0.5.7", lifespan=lifespan)

settings = get_settings()
effective_origins = _effective_cors_origins(settings.cors_allow_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=effective_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
    max_age=settings.cors_max_age,
)
media_root = Path(settings.media_root)
media_root.mkdir(parents=True, exist_ok=True)
app.mount(
    settings.media_base_url,
    StaticFiles(directory=str(media_root), html=False),
    name="media",
)


@app.middleware("http")
async def ensure_media_cors_headers(request, call_next):
    response = await call_next(request)
    origin = request.headers.get("origin")
    if (
        origin
        and request.url.path.startswith(settings.media_base_url)
        and _origin_matches(origin, effective_origins, settings.cors_allow_origin_regex)
    ):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        if settings.cors_allow_credentials:
            response.headers["Access-Control-Allow-Credentials"] = "true"
    return response

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(worlds.router)
app.include_router(ontologies.router)
app.include_router(agents.router)
app.include_router(character_agents.router)
app.include_router(character_agents.aspect_router)
app.include_router(character_agents.goal_router)
app.include_router(jobs.router)
app.include_router(architect.router)
app.include_router(elder.router)
app.include_router(elder.chat_router)
app.include_router(elder_chats.router)
app.include_router(librarian.router)
app.include_router(novelist.router)
app.include_router(graphrag.router)
app.include_router(configurations.router)
app.include_router(audit_logs.router)
app.include_router(media.router)
app.include_router(media_admin.router)
app.include_router(libraries.router)
app.include_router(ontology_instances.router)
app.include_router(personal_companion_agents.router)
app.include_router(events.router)
app.include_router(contracts.router)
app.include_router(backups.router)
app.include_router(llm_status.router)
app.include_router(setup.router)
app.include_router(service_email.router)


@app.get("/health")
def health() -> dict[str, str | None]:
    settings = get_settings()
    runtime = get_embedding_runtime_status()
    runtime_status = str(runtime.get("status"))
    fail_open = bool(settings.embedding_runtime_fail_open_health)
    overall_status = "ok"
    if runtime_status == "failed" and not fail_open:
        overall_status = "failed"
    return {
        "status": overall_status,
        "service": settings.app_name,
        "embedding_runtime_status": runtime_status,
        "embedding_runtime_reason": runtime.get("reason"),
    }
