from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.core.config import get_settings
from app.service import CompanionService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.companion_service = CompanionService(settings)
    try:
        yield
    finally:
        await app.state.companion_service.aclose()


settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
app = FastAPI(title="ShreckCompanion", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
    allow_credentials=settings.cors_allow_credentials,
)
settings.media_path.mkdir(parents=True, exist_ok=True)
app.mount(settings.media_base_url, StaticFiles(directory=str(settings.media_path)), name="media")
app.include_router(router)
