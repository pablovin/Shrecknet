from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette import status

from app.api.routers import get_api_router
from app.core.config_store import get_app_config, get_settings
from app.core.logging_config import configure_logging
from app.db.init_db import init_db
from app.db.init_jobs_db import init_jobs_db
from app.db.jobs_session import get_jobs_engine
from app.db.migrations import migrate_neo4j_embedding_properties
from app.db.session import get_engine
from app.graph.neo4j import close_driver as close_neo4j_driver
from app.graph.neo4j import get_neo4j_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(get_engine())
    await init_jobs_db(get_jobs_engine())
    logging.getLogger("backend_2.startup").info(
        "Calendar sync service is active"
    )
    # Run Neo4j migrations
    async for session in get_neo4j_session():
        try:
            await migrate_neo4j_embedding_properties(session)
        except Exception as e:
            logging.getLogger("backend_2.migrations").error(
                f"Neo4j migration failed: {e}"
            )
        break  # Only use the first session
    try:
        yield
    finally:
        await close_neo4j_driver()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    config = get_app_config(settings)
    app = FastAPI(title=config.app_name, debug=config.debug, lifespan=lifespan)

    media_root = Path(settings.media_root)
    media_root.mkdir(parents=True, exist_ok=True)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        max_age=settings.cors_max_age,
    )

    app.mount(
        settings.media_base_url,
        StaticFiles(directory=str(media_root), html=False),
        name="media",
    )

    request_logger = logging.getLogger("backend_2.requests")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):  # type: ignore[override]
        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            request_logger.exception(
                "Request raised unhandled exception",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
            raise

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if response.status_code >= 400:
            request_logger.warning(
                "Request completed with error",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        elif settings.debug:
            request_logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        return response

    error_logger = logging.getLogger("backend_2.errors")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        error_logger.warning(
            "HTTP error response",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "detail": exc.detail,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        error_logger.exception(
            "Unhandled server error",
            extra={
                "method": request.method,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    app.include_router(get_api_router())

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "service": config.app_name}

    return app


app = create_app()
