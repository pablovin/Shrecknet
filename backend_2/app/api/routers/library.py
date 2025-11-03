from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from app.api.deps import (
    get_current_user,
    get_library_service,
    require_roles,
)
from app.models.library import LibraryBookmark, LibraryItem
from app.models.user import User, UserRole
from app.schemas.library import (
    LibraryBookmarkCreate,
    LibraryBookmarkRead,
    LibraryBookmarkUpdate,
    LibraryItemRead,
    LibraryItemUpdate,
)
from app.services.library_service import LibraryService, PdfValidationError

router = APIRouter(prefix="/libraries", tags=["libraries"])


async def _get_item_or_404(
    service: LibraryService, ontology_id: int, item_id: int
) -> LibraryItem:
    item = await service.get_item(ontology_id, item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library item not found",
        )
    return item


async def _get_item_by_id_or_404(service: LibraryService, item_id: int) -> LibraryItem:
    item = await service.get_item_by_id(item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library item not found",
        )
    return item


async def _get_bookmark_or_404(
    service: LibraryService,
    bookmark_id: int,
) -> LibraryBookmark:
    bookmark = await service.get_bookmark(bookmark_id)
    if not bookmark:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bookmark not found",
        )
    return bookmark


def _serialize_item(service: LibraryService, item: LibraryItem) -> LibraryItemRead:
    return LibraryItemRead(**service.serialize_item(item))


def _serialize_bookmark(
    service: LibraryService, bookmark: LibraryBookmark
) -> LibraryBookmarkRead:
    return LibraryBookmarkRead(**service.serialize_bookmark(bookmark))


@router.get(
    "/{ontology_id}/items",
    response_model=list[LibraryItemRead],
)
async def list_library_items(
    ontology_id: int,
    skip: int = 0,
    limit: int = 50,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),  # noqa: ARG001 - ensures auth
) -> list[LibraryItemRead]:
    items = await service.list_items(ontology_id, skip=skip, limit=limit)
    return [_serialize_item(service, item) for item in items]


@router.post(
    "/{ontology_id}/items",
    response_model=LibraryItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_library_item(
    ontology_id: int,
    *,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    authors: str | None = Form(None),
    description: str | None = Form(None),
    cover_url: str | None = Form(None),
    auto_extract_metadata: bool = Form(False),
    auto_embed: bool = Form(False),
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> LibraryItemRead:
    """
    Create a new library item with a PDF file.

    Args:
        ontology_id: ID of the ontology to add the item to
        file: PDF file to upload
        title: Title of the item (optional if auto_extract_metadata=True)
        authors: Authors of the item (optional if auto_extract_metadata=True)
        description: Description of the item (optional if auto_extract_metadata=True)
        cover_url: URL to cover image (optional if auto_extract_metadata=True)
        auto_extract_metadata: If True, extract metadata (title, authors, description)
                               from PDF and use first page as cover image when fields are not provided
        auto_embed: If True, automatically trigger embedding job for this item

    Returns:
        The created library item with all metadata
    """
    try:
        item = await service.create_item(
            ontology_id,
            title=title,
            authors=authors,
            description=description,
            cover_url=cover_url,
            pdf=file,
            auto_extract_metadata=auto_extract_metadata,
            auto_embed=auto_embed,
        )
    except (ValueError, PdfValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _serialize_item(service, item)


@router.get(
    "/{ontology_id}/items/{item_id}",
    response_model=LibraryItemRead,
)
async def get_library_item(
    ontology_id: int,
    item_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),  # noqa: ARG001
) -> LibraryItemRead:
    item = await _get_item_or_404(service, ontology_id, item_id)
    return _serialize_item(service, item)


@router.put(
    "/{ontology_id}/items/{item_id}",
    response_model=LibraryItemRead,
)
async def update_library_item(
    ontology_id: int,
    item_id: int,
    payload: LibraryItemUpdate,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> LibraryItemRead:
    item = await _get_item_or_404(service, ontology_id, item_id)
    try:
        updated = await service.update_item(
            item,
            title=payload.title,
            authors=payload.authors,
            description=payload.description,
            cover_url=payload.cover_url,
            vectorized=payload.vectorized,
            last_vectorized_at=payload.last_vectorized_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _serialize_item(service, updated)


@router.post(
    "/{ontology_id}/items/{item_id}/content",
    response_model=LibraryItemRead,
)
async def replace_library_pdf(
    ontology_id: int,
    item_id: int,
    file: UploadFile = File(...),
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> LibraryItemRead:
    item = await _get_item_or_404(service, ontology_id, item_id)
    try:
        updated = await service.replace_pdf(item, file)
    except PdfValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _serialize_item(service, updated)


@router.delete(
    "/{ontology_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_library_item(
    ontology_id: int,
    item_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    item = await _get_item_or_404(service, ontology_id, item_id)
    await service.delete_item(item)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/items/{item_id}/bookmarks",
    response_model=list[LibraryBookmarkRead],
)
async def list_bookmarks(
    item_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),
) -> list[LibraryBookmarkRead]:
    item = await _get_item_by_id_or_404(service, item_id)
    bookmarks = await service.list_bookmarks(item, current_user)
    return [_serialize_bookmark(service, bookmark) for bookmark in bookmarks]


@router.post(
    "/items/{item_id}/bookmarks",
    response_model=LibraryBookmarkRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_bookmark(
    item_id: int,
    payload: LibraryBookmarkCreate,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),
) -> LibraryBookmarkRead:
    item = await _get_item_by_id_or_404(service, item_id)
    try:
        bookmark = await service.create_bookmark(
            item,
            current_user,
            page=payload.page,
            title=payload.title,
            description=payload.description,
            is_private=payload.is_private,
            shared_user_ids=payload.shared_user_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return _serialize_bookmark(service, bookmark)


@router.put(
    "/bookmarks/{bookmark_id}",
    response_model=LibraryBookmarkRead,
)
async def update_bookmark(
    bookmark_id: int,
    payload: LibraryBookmarkUpdate,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),
) -> LibraryBookmarkRead:
    bookmark = await _get_bookmark_or_404(service, bookmark_id)
    if bookmark.owner_id != current_user.id and current_user.role not in {
        UserRole.ADMIN,
        UserRole.WORLD_BUILDER,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    try:
        updated = await service.update_bookmark(
            bookmark,
            page=payload.page,
            title=payload.title,
            description=payload.description,
            is_private=payload.is_private,
            shared_user_ids=payload.shared_user_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await service.session.refresh(updated)
    return _serialize_bookmark(service, updated)


@router.delete(
    "/bookmarks/{bookmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_bookmark(
    bookmark_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),
) -> Response:
    bookmark = await _get_bookmark_or_404(service, bookmark_id)
    if bookmark.owner_id != current_user.id and current_user.role not in {
        UserRole.ADMIN,
        UserRole.WORLD_BUILDER,
    }:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )
    await service.delete_bookmark(bookmark)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/bookmarks/{bookmark_id}/share/me",
    response_model=LibraryBookmarkRead,
)
async def leave_shared_bookmark(
    bookmark_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),
) -> LibraryBookmarkRead:
    bookmark = await _get_bookmark_or_404(service, bookmark_id)
    if bookmark.owner_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bookmark owners cannot remove themselves."
        )
    if all(user.id != current_user.id for user in bookmark.shared_with):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bookmark is not shared with this user.",
        )
    try:
        updated = await service.remove_self_from_bookmark(bookmark, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _serialize_bookmark(service, updated)


# PDF Embedding endpoints -----------------------------------------------


@router.post(
    "/{ontology_id}/items/{item_id}/trigger-embedding",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_pdf_embedding(
    ontology_id: int,
    item_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> dict:
    """
    Trigger background job to embed a PDF book into Neo4j.

    This creates embeddings for semantic search and retrieval by Librarian agents.
    Requires admin or world_builder role.
    """
    from app.tasks.pdf_embedding import embed_pdf_book

    item = await _get_item_or_404(service, ontology_id, item_id)

    # Trigger the embedding task
    result = embed_pdf_book.delay(
        library_item_id=item.id,
        ontology_id=ontology_id,
        author_type="user",
        author_id=str(current_user.id),
    )

    return {
        "message": f"Embedding job triggered for library item {item_id}",
        "library_item_id": item_id,
        "ontology_id": ontology_id,
        "celery_task_id": result.id,
    }


@router.get(
    "/{ontology_id}/items/{item_id}/embedding-status",
)
async def get_embedding_status(
    ontology_id: int,
    item_id: int,
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(get_current_user),  # noqa: ARG001
) -> dict:
    """
    Get embedding status for a library item.

    Returns information about whether the PDF has been embedded
    and how many chunks are in the vector database.
    """
    from app.graph.neo4j import get_driver
    from app.services.pdf_embedding_service import PdfEmbeddingService

    item = await _get_item_or_404(service, ontology_id, item_id)

    # Get stats from Neo4j
    driver = get_driver()
    async with driver.session() as graph_session:
        pdf_service = PdfEmbeddingService(graph_session)
        stats = await pdf_service.get_embedding_stats(item.id)

    return {
        "library_item_id": item.id,
        "ontology_id": ontology_id,
        "vectorized": item.vectorized,
        "last_vectorized_at": (
            item.last_vectorized_at.isoformat() if item.last_vectorized_at else None
        ),
        "total_chunks": stats.get("total_chunks", 0),
        "is_embedded": stats.get("is_embedded", False),
    }


@router.get(
    "/embedding-jobs",
)
async def list_embedding_jobs(
    ontology_id: int | None = None,
    limit: int = 10,
    current_user: User = Depends(get_current_user),  # noqa: ARG001
) -> list[dict]:
    """
    Get recent PDF embedding jobs.

    Returns a list of background jobs for PDF embedding, optionally
    filtered by ontology_id.
    """
    from app.db.jobs_session import JobsSessionMaker
    from app.models.background_job import BackgroundJob, JobType
    from sqlalchemy import select

    async with JobsSessionMaker() as session:
        query = select(BackgroundJob).where(
            BackgroundJob.job_type == JobType.PDF_BOOK_EMBEDDING
        )

        if ontology_id is not None:
            query = query.where(BackgroundJob.ontology_id == ontology_id)

        query = query.order_by(BackgroundJob.started_at.desc()).limit(min(limit, 100))

        result = await session.execute(query)
        jobs = result.scalars().all()

        return [
            {
                "job_id": job.id,
                "library_item_id": None,  # Would need to parse from details
                "ontology_id": job.ontology_id,
                "status": job.status,
                "progress": job.progress,
                "description": job.description,
                "started_at": job.started_at.isoformat(),
                "completed_at": (
                    job.completed_at.isoformat() if job.completed_at else None
                ),
                "duration_seconds": job.duration_seconds,
                "error_message": job.error_message,
                "details": job.details,
            }
            for job in jobs
        ]
