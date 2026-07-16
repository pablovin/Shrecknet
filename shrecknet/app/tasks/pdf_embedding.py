"""Celery task for PDF book embedding."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.core.config_store import get_settings
from app.graph.neo4j import get_driver
from app.models.background_job import AuthorType, JobType
from app.repositories.library_repository import LibraryRepository
from app.repositories.ontology_repository import OntologyRepository
from app.services.docling_ingestion_service import DoclingIngestionService
from app.services.legacy_pdf_ingestion_service import LegacyPdfIngestionService
from app.services.pdf_embedding_service import PdfEmbeddingService
from app.utils.async_helpers import run_async
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="library.embed_pdf_book")
def embed_pdf_book(
    library_item_id: int,
    ontology_id: int,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """Run the active, structured Docling ingestion pipeline."""
    logger.info(
        "librarian_embedding_received library_item_id=%s ontology_id=%s task_id=%s",
        library_item_id,
        ontology_id,
        embed_pdf_book.request.id,
    )
    print(
        f"[LIBRARIAN_EMBED] step=task_received library_item_id={library_item_id} "
        f"ontology_id={ontology_id} task_id={embed_pdf_book.request.id}"
    )
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.PDF_BOOK_EMBEDDING,
            description=f"Docling ingestion for PDF book (library item {library_item_id})",
            celery_task_id=embed_pdf_book.request.id,
            details={
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
                "pipeline": "docling",
            },
            ontology_id=_coerce_ontology_id(ontology_id),
        )
    )
    try:
        run_async(mark_job_running(job_id))
        run_async(update_job_progress(job_id, 0.05, {"status": "Preparing Docling ingestion"}))
        result = run_async(
            _run_docling_ingestion(
                library_item_id=library_item_id,
                ontology_id=ontology_id,
                job_id=job_id,
            )
        )
        run_async(mark_job_done(job_id, result))
        return {"job_id": job_id, "status": "success", "result": result}
    except Exception as exc:
        logger.exception(
            "docling_embedding_task_failed library_item_id=%s job_id=%s",
            library_item_id,
            job_id,
        )
        # The previous active graph and SQL readiness flag remain intact on failure.
        run_async(mark_job_failed(job_id, str(exc)))
        raise


async def _run_docling_ingestion(
    *, library_item_id: int, ontology_id: int, job_id: int
) -> dict[str, Any]:
    from datetime import datetime, timezone

    from app.db.session import AsyncSessionMaker

    settings = get_settings()
    async with AsyncSessionMaker() as session:
        library_repo = LibraryRepository(session)
        ontology_repo = OntologyRepository(session)
        item = await library_repo.get_item_by_id(library_item_id)
        ontology = await ontology_repo.get(ontology_id)
        if item is None or int(item.ontology_id) != int(ontology_id):
            raise ValueError(f"Library item {library_item_id} not found in ontology {ontology_id}")
        if ontology is None:
            raise ValueError(f"Ontology {ontology_id} not found")
        if not item.pdf_path:
            raise ValueError(f"Library item {library_item_id} has no PDF file")
        pdf_path = Path(settings.media_root) / item.pdf_path
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        book_title = item.title
        rpg_system = (ontology.rpg_system or "Unknown").strip() or "Unknown"

    await update_job_progress(job_id, 0.12, {"status": "Parsing PDF with Docling"})
    print(
        f"[LIBRARIAN_EMBED] step=book_resolved book={book_title!r} library_item_id={library_item_id} "
        f"pdf_path={pdf_path} percent=0"
    )
    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as graph_session:
        retrieval_service = PdfEmbeddingService(graph_session)
        await retrieval_service.ensure_vector_index()
        service = DoclingIngestionService(graph_session)

        async def report_progress(percent: float, status: str, details: dict[str, Any]) -> None:
            await update_job_progress(
                job_id,
                percent,
                {"status": status, **details},
            )

        result = await service.ingest(
            library_item_id=library_item_id,
            ontology_id=ontology_id,
            pdf_path=pdf_path,
            book_title=book_title,
            rpg_system=rpg_system,
            progress_callback=report_progress,
        )
        status_text = (
            "Repairing SQL state for the existing ingestion"
            if result.get("status") == "already_active"
            else "Activating structured document"
        )
        await update_job_progress(job_id, 0.9, {"status": status_text})
        try:
            async with AsyncSessionMaker() as session:
                repo = LibraryRepository(session)
                current = await repo.get_item_by_id(library_item_id)
                if current is None:
                    raise ValueError(f"Library item {library_item_id} disappeared during ingestion")
                await repo.update_item(
                    current,
                    {"vectorized": True, "last_vectorized_at": datetime.now(timezone.utc)},
                )
                await session.commit()
        except Exception as exc:
            if result.get("status") != "already_active":
                await service.compensate_activation(
                    library_item_id,
                    str(result["ingestion_id"]),
                    result.get("previous_ingestion_id"),
                )
                if result.get("manifest_path"):
                    service.mark_manifest_compensated(str(result["manifest_path"]), exc)
            raise

        try:
            await service.cleanup_retired(library_item_id, str(result["ingestion_id"]))
            service.cleanup_parsed_directories(pdf_path, str(result["ingestion_id"]))
        except Exception:
            # Activation and SQL state are already committed. Cleanup is safe to retry
            # on the next ingestion and must not turn a usable version into a failed job.
            logger.exception(
                "docling_retired_cleanup_failed library_item_id=%s ingestion_id=%s",
                library_item_id,
                result["ingestion_id"],
            )
        await update_job_progress(job_id, 1.0, {"status": "Docling ingestion complete"})
        print(
            f"[LIBRARIAN_EMBED] step=complete book={book_title!r} library_item_id={library_item_id} "
            f"children={result.get('chunks_created', 0)} percent=100"
        )
        return result


@celery_app.task(name="library.embed_pdf_book_old")
def embed_pdf_book_old(
    library_item_id: int,
    ontology_id: int,
    author_type: str = "user",
    author_id: str = "system",
) -> dict[str, Any]:
    """
    Embed a PDF book into Neo4j for semantic search.

    This task:
    1. Reads the PDF file
    2. Extracts text from each page
    3. Creates embeddings for each page
    4. Stores chunks in Neo4j with vector embeddings

    Args:
        library_item_id: ID of the library item
        ontology_id: ID of the ontology this book belongs to
        author_type: Type of author triggering the task (user/agent)
        author_id: ID of the author

    Returns:
        Dictionary with job_id and status
    """
    settings = get_settings()

    # Create job entry
    job_id = run_async(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.PDF_BOOK_EMBEDDING,
            description=f"Embedding PDF book (library item {library_item_id})",
            celery_task_id=embed_pdf_book_old.request.id,
            details={
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
            },
            ontology_id=_coerce_ontology_id(ontology_id),
        )
    )

    deleted_old_chunks = 0
    duplicate_chunk_keys = 0

    try:
        # Mark as running
        run_async(mark_job_running(job_id))
        logger.info(
            "pdf_embedding_task stage=start library_item_id=%s ontology_id=%s job_id=%s",
            library_item_id,
            ontology_id,
            job_id,
        )

        # Update progress: Getting library item
        run_async(
            update_job_progress(
                job_id, 0.1, {"status": "Fetching library item details"}
            )
        )

        # Get library item to find PDF path
        from app.db.session import AsyncSessionMaker

        async def get_library_item():
            async with AsyncSessionMaker() as session:
                repo = LibraryRepository(session)
                return await repo.get_item_by_id(library_item_id)

        library_item = run_async(get_library_item())

        if not library_item:
            raise ValueError(f"Library item {library_item_id} not found")

        if not library_item.pdf_path:
            raise ValueError(f"Library item {library_item_id} has no PDF file")

        # Build full path to PDF
        pdf_path = Path(settings.media_root) / library_item.pdf_path

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        pdf_size = pdf_path.stat().st_size

        logger.info(
            "pdf_embedding_task stage=pdf_ready library_item_id=%s job_id=%s pdf_path=%s pdf_size_bytes=%s",
            library_item_id,
            job_id,
            pdf_path,
            pdf_size,
        )
        if pdf_size < 1024:
            logger.warning(
                "pdf_embedding_task stage=suspicious_pdf_size library_item_id=%s job_id=%s pdf_path=%s pdf_size_bytes=%s",
                library_item_id,
                job_id,
                pdf_path,
                pdf_size,
            )

        # Update progress: Processing PDF
        run_async(update_job_progress(job_id, 0.2, {"status": "Reading PDF file"}))

        # Embed the PDF
        async def embed_pdf():
            driver = get_driver()
            async with driver.session(database=settings.neo4j_database) as graph_session:
                service = LegacyPdfIngestionService(graph_session)

                # Always remove previous chunks first to guarantee single-version state.
                await update_job_progress(
                    job_id, 0.25, {"status": "Clearing previous embeddings"}
                )
                deleted_old = await service.delete_embeddings(library_item_id)
                logger.info(
                    "pdf_embedding_task stage=delete_old_embeddings library_item_id=%s job_id=%s deleted_old_chunks=%s",
                    library_item_id,
                    job_id,
                    deleted_old,
                )

                # Ensure vector index
                await update_job_progress(
                    job_id, 0.3, {"status": "Ensuring vector index"}
                )
                logger.info(
                    "pdf_embedding_task stage=ensure_vector_index library_item_id=%s job_id=%s",
                    library_item_id,
                    job_id,
                )
                await service.ensure_vector_index()
                index_info = await service.ensure_vector_index()
                model_mismatch = index_info.get("embedding_model") != service.embedding_service.model_id
                dim_mismatch = int(index_info.get("embedding_dim", 0)) != int(service.embedding_service.embed_dim)
                if model_mismatch or dim_mismatch:
                    logger.warning(
                        "pdf_embedding_task stage=index_diagnostic library_item_id=%s job_id=%s index_model=%s service_model=%s index_dim=%s service_dim=%s",
                        library_item_id,
                        job_id,
                        index_info.get("embedding_model"),
                        service.embedding_service.model_id,
                        index_info.get("embedding_dim"),
                        service.embedding_service.embed_dim,
                    )

                # Embed the book
                await update_job_progress(
                    job_id, 0.4, {"status": "Embedding PDF pages"}
                )
                logger.info(
                    "pdf_embedding_task stage=embed_book library_item_id=%s job_id=%s batch_size=%s",
                    library_item_id,
                    job_id,
                    20,
                )

                result = await service.embed_pdf_book(
                    library_item_id=library_item_id,
                    ontology_id=ontology_id,
                    pdf_path=pdf_path,
                    batch_size=20,
                )

                verify_query = """
                MATCH (c:PdfChunk {library_item_id: $library_item_id})
                WITH c.library_item_id AS library_item_id,
                     c.chunk_index AS chunk_index,
                     count(*) AS occurrences
                WHERE occurrences > 1
                RETURN count(*) AS duplicate_keys
                """
                verify_result = await graph_session.run(
                    verify_query, library_item_id=library_item_id
                )
                verify_record = await verify_result.single()
                duplicate_keys = int(
                    verify_record["duplicate_keys"] if verify_record else 0
                )

                return {
                    "result": result,
                    "deleted_old_chunks": deleted_old,
                    "duplicate_chunk_keys": duplicate_keys,
                    "index_info": index_info,
                }

        embed_payload = run_async(embed_pdf())
        result = embed_payload["result"]
        deleted_old_chunks = int(embed_payload.get("deleted_old_chunks", 0))
        duplicate_chunk_keys = int(embed_payload.get("duplicate_chunk_keys", 0))
        index_info = embed_payload.get("index_info", {})
        logger.info(
            "pdf_embedding_task stage=embed_complete library_item_id=%s job_id=%s total_pages=%s embedded_pages=%s missing_pages=%s chunks_created=%s chunks_failed=%s deleted_old_chunks=%s duplicate_chunk_keys=%s status=%s",
            library_item_id,
            job_id,
            result.get("total_pages", 0),
            result.get("pages_extracted", 0),
            result.get("pages_with_no_text", 0),
            result.get("chunks_created", 0),
            result.get("chunks_failed", 0),
            deleted_old_chunks,
            duplicate_chunk_keys,
            result.get("status", "unknown"),
        )

        if result.get("chunks_created", 0) <= 0:
            raise ValueError(
                "No text could be extracted from the PDF. "
                "If this is a scanned/image PDF, OCR support is required."
            )
        if duplicate_chunk_keys > 0:
            raise ValueError(
                f"Duplicate chunk keys detected for library item {library_item_id}: {duplicate_chunk_keys}"
            )

        # Update library item to mark as vectorized
        run_async(
            update_job_progress(job_id, 0.9, {"status": "Updating library item status"})
        )

        async def update_item():
            async with AsyncSessionMaker() as session:
                repo = LibraryRepository(session)
                item = await repo.get_item_by_id(library_item_id)
                if item:
                    from datetime import datetime

                    await repo.update_item(
                        item,
                        {
                            "vectorized": True,
                            "last_vectorized_at": datetime.now(),
                        },
                    )
                    await session.commit()

        run_async(update_item())

        # Mark as complete
        run_async(
            mark_job_done(
                job_id,
                {
                    "deleted_old_chunks": deleted_old_chunks,
                    "duplicate_chunk_keys": duplicate_chunk_keys,
                    "index_info": index_info,
                    "chunks_created": result.get("chunks_created", 0),
                    "chunks_failed": result.get("chunks_failed", 0),
                    "total_pages": result.get("total_pages", 0),
                    "status": result.get("status", "success"),
                },
            )
        )

        logger.info(
            "pdf_embedding_task stage=done library_item_id=%s job_id=%s chunks_created=%s total_pages=%s embedded_pages=%s missing_pages=%s deleted_old_chunks=%s duplicate_chunk_keys=%s",
            library_item_id,
            job_id,
            result.get("chunks_created", 0),
            result.get("total_pages", 0),
            result.get("pages_extracted", 0),
            result.get("pages_with_no_text", 0),
            deleted_old_chunks,
            duplicate_chunk_keys,
        )

        return {"job_id": job_id, "status": "success", "result": result}

    except Exception as e:
        logger.error(f"PDF embedding failed for item {library_item_id}: {e}")
        from app.db.session import AsyncSessionMaker

        async def mark_item_unvectorized() -> None:
            async with AsyncSessionMaker() as session:
                repo = LibraryRepository(session)
                item = await repo.get_item_by_id(library_item_id)
                if item:
                    await repo.update_item(
                        item,
                        {
                            "vectorized": False,
                            "last_vectorized_at": None,
                        },
                    )
                    await session.commit()

        run_async(mark_item_unvectorized())
        run_async(mark_job_failed(job_id, str(e)))
        raise


def _coerce_ontology_id(ontology_id: int) -> int | None:
    try:
        return int(ontology_id)
    except (TypeError, ValueError):
        return None
