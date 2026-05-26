from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import get_settings
from app.service import ChatService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.chat_service = ChatService(settings)
    app.state.chat_service.ensure_background_tasks()
    try:
        await app.state.chat_service.prewarm_local_llm()
        yield
    finally:
        await app.state.chat_service.aclose()


settings = get_settings()
# Ensure application loggers (e.g. app.service) emit INFO lines to container stdout.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(name)s: %(message)s")
app = FastAPI(title="shreckLLM", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
    allow_credentials=settings.cors_allow_credentials,
)
app.include_router(router)
