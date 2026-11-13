from __future__ import annotations

import logging
from threading import Thread

from shreckconfig import load_cors_policy

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import router
from app.api.routers.ontology_items import router as ontology_items_router
from app.api.routers.sources_workspace import router as sources_workspace_router
from app.api.routers.source_collections import router as source_collections_router
from app.api.routers.ontology_migration import router as ontology_migration_router
from app.api.routers.game_engine import router as game_engine_router
from app.core.config import get_settings
from app.db.session import create_schema
from app.services.embedding import EmbeddingUnavailable, get_embedder
from app.services.pymupdf_parsing import validate_parser
from app.services.book_map import has_missing_book_maps

settings = get_settings(check_mineru=True)
settings.media_root.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="ShreckWorld", version="0.1.0")
cors_policy = load_cors_policy()
app.add_middleware(CORSMiddleware, **cors_policy.model_dump(), expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "ETag"])


def _import_graph_narrative_sources() -> None:
    """Automatic, insert-only import; retry unavailable graph services on startup."""
    from time import sleep

    log = logging.getLogger(__name__)
    for attempt in range(12):
        try:
            from app.db.session import session_factory
            from app.services.graph_source_import import import_graph_sources
            import_graph_sources(session_factory, settings)
            return
        except (OSError, ConnectionError) as exc:
            log.warning("Graph source import input unavailable (%s); attempt %d/12",
                        type(exc).__name__, attempt + 1)
        except Exception as exc:
            # Neo4j may start after ShreckWorld; retry connection/driver failures.
            from neo4j.exceptions import ServiceUnavailable, SessionExpired
            if not isinstance(exc, (ServiceUnavailable, SessionExpired)):
                log.exception("Graph source startup import failed; no automatic retry")
                return
            log.warning("Graph source import Neo4j unavailable (%s); attempt %d/12",
                        type(exc).__name__, attempt + 1)
        if attempt < 11:
            sleep(10)


def _queue_startup_book_map_backfill() -> None:
    try:
        from app.db.session import session_factory
        with session_factory()() as session:
            backfill_needed = has_missing_book_maps(session)
        if backfill_needed:
            from app.tasks.book_map import backfill_book_maps_on_startup
            backfill_book_maps_on_startup.apply_async(retry=False)
    except Exception as exc:
        # The next service start will repeat this idempotent check.
        logging.getLogger(__name__).warning("Unable to queue startup Book Map backfill (%s)", type(exc).__name__)


@app.on_event("startup")
def startup() -> None:
    settings.media_root.mkdir(parents=True, exist_ok=True)
    create_schema()
    if settings.graph_import_on_startup:
        logging.getLogger(__name__).info("Starting automatic graph narrative Sources import")
        Thread(target=_import_graph_narrative_sources, name="graph-source-import", daemon=True).start()
    Thread(target=_queue_startup_book_map_backfill, name="book-map-backfill-check", daemon=True).start()
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
    if settings.pdf_parser == "pymupdf":
        try:
            validate_parser()
        except (ImportError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    mineru = None
    if settings.pdf_parser == "mineru":
        try:
            from app.services.mineru_parsing import check_ready
            mineru = check_ready()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Configured MinerU parser is unavailable") from exc
    return {"status": "ready", "embedding": embedding, "parser": settings.pdf_parser,
            "pdf_parser": settings.pdf_parser,
            "configured_pdf_parser": settings.configured_pdf_parser or settings.pdf_parser,
            "parser_fallback_reason": settings.parser_fallback_reason,
            "parser_device": settings.parser_device,
            "parser_table_structure_enabled": settings.parser_table_structure_enabled,
            "parser_table_former_mode": settings.parser_table_former_mode,
            "parser_artifacts_present": True,
            "mineru": mineru}


app.include_router(router)
app.include_router(ontology_items_router)
app.include_router(sources_workspace_router)
app.include_router(source_collections_router)
app.include_router(ontology_migration_router)
app.include_router(game_engine_router)
# Source and cover files are deliberately not mounted as public static media.
# Their download endpoints must apply the same Shrecknet authorization boundary.
