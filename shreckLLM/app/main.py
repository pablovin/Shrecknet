from __future__ import annotations

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
    try:
        yield
    finally:
        await app.state.chat_service.aclose()


settings = get_settings()
app = FastAPI(title="shreckLLM", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
    allow_credentials=settings.cors_allow_credentials,
)
app.include_router(router)
