from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routers import router
from app.core.config import get_settings
from app.db.session import create_schema

settings = get_settings()
settings.media_root.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="ShreckWorld", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup() -> None:
    settings.media_root.mkdir(parents=True, exist_ok=True)
    create_schema()


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ShreckWorld"}


app.include_router(router)
app.mount(settings.media_base_url, StaticFiles(directory=settings.media_root), name="media")
