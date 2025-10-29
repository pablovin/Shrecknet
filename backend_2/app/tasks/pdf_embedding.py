"""Celery task for PDF book embedding."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from app.celery_app import celery_app
from app.core.config import get_settings
from app.graph.neo4j import get_driver
from app.models.background_job import AuthorType, JobType
from app.repositories.library_repository import LibraryRepository
from app.services.pdf_embedding_service import PdfEmbeddingService
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="library.embed_pdf_book")
def embed_pdf_book(
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
    # Create job entry
    job_id = asyncio.run(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.PDF_BOOK_EMBEDDING,
            description=f"Embedding PDF book (library item {library_item_id})",
            celery_task_id=embed_pdf_book.request.id,
            details={
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
            },
            ontology_id=ontology_id,
        )
    )

    try:
        # Mark as running
        asyncio.run(mark_job_running(job_id))

        # Update progress: Getting library item
        asyncio.run(
            update_job_progress(
                job_id, 0.1, {"status": "Fetching library item details"}
            )
        )

        # Get library item to find PDF path
        from app.db.session import async_session_maker

        async def get_library_item():
            async with async_session_maker() as session:
                repo = LibraryRepository(session)
                return await repo.get_item_by_id(library_item_id)

        library_item = asyncio.run(get_library_item())

        if not library_item:
            raise ValueError(f"Library item {library_item_id} not found")

        if not library_item.pdf_path:
            raise ValueError(f"Library item {library_item_id} has no PDF file")

        # Build full path to PDF
        pdf_path = Path(settings.media_root) / library_item.pdf_path

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        # Update progress: Processing PDF
        asyncio.run(update_job_progress(job_id, 0.2, {"status": "Reading PDF file"}))

        # Embed the PDF
        async def embed_pdf():
            driver = get_driver()
            async with driver.session() as graph_session:
                service = PdfEmbeddingService(graph_session)

                # Ensure vector index
                asyncio.run(
                    update_job_progress(
                        job_id, 0.3, {"status": "Ensuring vector index"}
                    )
                )
                await service.ensure_vector_index()

                # Embed the book
                asyncio.run(
                    update_job_progress(job_id, 0.4, {"status": "Embedding PDF pages"})
                )

                result = await service.embed_pdf_book(
                    library_item_id=library_item_id,
                    ontology_id=ontology_id,
                    pdf_path=pdf_path,
                    batch_size=20,
                )

                return result

        result = asyncio.run(embed_pdf())

        # Update library item to mark as vectorized
        asyncio.run(
            update_job_progress(job_id, 0.9, {"status": "Updating library item status"})
        )

        async def update_item():
            async with async_session_maker() as session:
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

        asyncio.run(update_item())

        # Mark as complete
        asyncio.run(
            mark_job_done(
                job_id,
                {
                    "chunks_created": result.get("chunks_created", 0),
                    "chunks_failed": result.get("chunks_failed", 0),
                    "total_pages": result.get("total_pages", 0),
                    "status": result.get("status", "success"),
                },
            )
        )

        logger.info(
            f"Successfully embedded PDF book {library_item_id}: "
            f"{result.get('chunks_created', 0)} chunks created"
        )

        return {"job_id": job_id, "status": "success", "result": result}

    except Exception as e:
        logger.error(f"PDF embedding failed for item {library_item_id}: {e}")
        asyncio.run(mark_job_failed(job_id, str(e)))
        raise
