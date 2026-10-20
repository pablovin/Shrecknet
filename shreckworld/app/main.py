from __future__ import annotations

import logging

from shreckconfig import load_cors_policy

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import router
from app.core.config import get_settings
from app.db.session import create_schema
from app.services.embedding import EmbeddingUnavailable, get_embedder

settings = get_settings()
settings.media_root.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="ShreckWorld", version="0.1.0")
cors_policy = load_cors_policy()
app.add_middleware(CORSMiddleware, **cors_policy.model_dump(), expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "ETag"])


@app.on_event("startup")
def startup() -> None:
    settings.media_root.mkdir(parents=True, exist_ok=True)
    create_schema()
    try:
        get_embedder().ensure_ready()
    except EmbeddingUnavailable as exc:
        # Original sources remain readable even when semantic inference is down.
        # /ready and semantic/indexing endpoints report the unavailable encoder.
        logging.getLogger(__name__).warning("ShreckWorld semantic readiness failed: %s", exc)


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ShreckWorld"}


@app.get("/ready", tags=["operations"])
def readiness() -> dict:
    try:
        embedding = get_embedder().ensure_ready()
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not settings.docling_artifacts_path.is_dir():
        raise HTTPException(status_code=503, detail="Docling artifacts are missing")
    return {"status": "ready", "embedding": embedding, "parser": "docling", "parser_artifacts_present": True}


app.include_router(router)
# Source and cover files are deliberately not mounted as public static media.
# Their download endpoints must apply the same Shrecknet authorization boundary.
