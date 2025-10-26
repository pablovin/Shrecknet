from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import get_api_router
from app.core.config import get_app_config, get_settings
from app.db.init_db import init_db
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(engine)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    config = get_app_config(settings)
    app = FastAPI(title=config.app_name, debug=config.debug, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://c56f54ad-02b8-428f-9e87-43f81dab0914.lovableproject.com",
            "https://lovableproject.com",
            "http://localhost",
            "http://localhost:3000",
            "https://shrecknet.club",
        ],
        allow_origin_regex=r"https://.*\.lovableproject\.com",
        allow_credentials=True,
        allow_methods=["*"],           # or ["GET","POST","PUT","DELETE","OPTIONS"]
        allow_headers=["*"],           # temporarily wide; tighten later if desired
        max_age=3600,
    )
    app.include_router(get_api_router())

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "service": config.app_name}

    return app


app = create_app()
