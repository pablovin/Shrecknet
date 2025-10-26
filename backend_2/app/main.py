from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import get_api_router
from app.core.config import get_app_config
from app.db.init_db import init_db
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(engine)
    yield


def create_app() -> FastAPI:
    config = get_app_config()
    app = FastAPI(title=config.app_name, debug=config.debug, lifespan=lifespan)
    app.include_router(get_api_router())

    @app.get("/health", tags=["health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "service": config.app_name}

    return app


app = create_app()
