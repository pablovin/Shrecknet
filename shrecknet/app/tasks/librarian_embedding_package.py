"""Background import of portable Librarian embedding packages."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.db.session import AsyncSessionMaker
from app.graph.neo4j import get_driver
from app.models.background_job import AuthorType, JobType
from app.repositories.library_repository import LibraryRepository
from app.services.librarian_embedding_package_service import LibrarianEmbeddingPackageService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="library.import_embedding_package")
def import_librarian_embedding_package(
    package_path: str,
    library_item_id: int,
    ontology_id: int,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Validate and activate a previously staged embedding package."""
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.PDF_BOOK_EMBEDDING,
            description=f"Importing Librarian embedding (library item {library_item_id})",
            celery_task_id=import_librarian_embedding_package.request.id,
            details={
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
                "status": "File received; preparing import",
            },
            ontology_id=ontology_id,
        )
    )
    try:
        run_async(mark_job_running(job_id))
        run_async(update_job_progress(job_id, 0.05, {"status": "File received; preparing import"}))
        result = run_async(
            _run_import(
                Path(package_path),
                library_item_id=library_item_id,
                ontology_id=ontology_id,
                job_id=job_id,
            )
        )
        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, "status": "success", "result": result}
    except Exception as exc:
        logger.exception("Librarian embedding import failed for library item %s", library_item_id)
        run_async(mark_job_failed(job_id, str(exc)))
        raise
    finally:
        Path(package_path).unlink(missing_ok=True)


async def _run_import(
    package_path: Path, *, library_item_id: int, ontology_id: int, job_id: int
) -> dict[str, Any]:
    async with AsyncSessionMaker() as session:
        repo = LibraryRepository(session)
        item = await repo.get_item_by_id(library_item_id)
        if item is None or int(item.ontology_id) != int(ontology_id):
            raise ValueError(f"Library item {library_item_id} not found in ontology {ontology_id}")

    await update_job_progress(job_id, 0.15, {"status": "Validating embedding package"})
    package = package_path.read_bytes()

    settings = get_settings()
    await update_job_progress(job_id, 0.35, {"status": "Importing embedding graph"})
    async with get_driver().session(database=settings.neo4j_database) as graph_session:
        result = await LibrarianEmbeddingPackageService(graph_session).import_package(
            package, library_item_id=library_item_id, ontology_id=ontology_id
        )

    await update_job_progress(job_id, 0.9, {"status": "Activating imported embedding"})
    async with AsyncSessionMaker() as session:
        repo = LibraryRepository(session)
        item = await repo.get_item_by_id(library_item_id)
        if item is None or int(item.ontology_id) != int(ontology_id):
            raise ValueError(f"Library item {library_item_id} disappeared during import")
        await repo.update_item(
            item,
            {"vectorized": True, "last_vectorized_at": datetime.now(timezone.utc)},
        )
        await session.commit()

    await update_job_progress(job_id, 1.0, {"status": "Embedding import complete"})
    return {"message": "Librarian embedding imported and activated", **result}
