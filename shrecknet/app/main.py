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
    setup,
    users,
    worlds,
)
from app.core.config_store import get_settings
from app.db.init_db import init_db_async
from app.db.init_jobs_db import init_jobs_db
from app.db.jobs_session import get_jobs_engine
from app.graphrag.embedding_service import get_embedding_model
from app.graph.neo4j import ensure_temporal_graph_constraints, get_driver


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    _log_storage_diagnostics(get_settings())
    await init_db_async()
    init_jobs_db(get_jobs_engine())
    _start_embedding_prewarm()
    try:
        settings = get_settings()
        driver = get_driver()
        async with driver.session(database=settings.neo4j_database) as neo4j_session:
            await ensure_temporal_graph_constraints(neo4j_session)
    except Exception:
        logger.exception("Unable to ensure temporal graph constraints during startup")
    yield


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
app.include_router(events.router)
app.include_router(contracts.router)
app.include_router(backups.router)
app.include_router(llm_status.router)
app.include_router(setup.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().app_name}
