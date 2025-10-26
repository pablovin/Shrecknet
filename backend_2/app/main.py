from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette import status

from app.api.routers import get_api_router
from app.core.config import get_app_config, get_settings
from app.core.logging_config import configure_logging
from app.db.init_db import init_db
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(engine)
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    config = get_app_config(settings)
    app = FastAPI(title=config.app_name, debug=config.debug, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        max_age=settings.cors_max_age,
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
            extra={"method": request.method, "path": request.url.path,},
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
