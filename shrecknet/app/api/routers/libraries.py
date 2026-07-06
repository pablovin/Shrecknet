from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, delete, or_, select, update

from app.api.deps import get_current_user, require_roles
from app.core.config_store import get_settings
from app.db.jobs_session import JobsSessionMaker
from app.db.session import AsyncSessionCompat, get_db_session
from app.graph.neo4j import get_driver
from app.models import BackgroundJob, JobStatus, JobType, LibraryBookmark, LibraryItem, User

router = APIRouter(prefix="/libraries", tags=["libraries"])
legacy_router = APIRouter(prefix="/v1/libraries", tags=["libraries-legacy"])
logger = logging.getLogger(__name__)


def _parse_job_details(raw_details: str | None) -> dict[str, object] | None:
    if not raw_details:
        return None
    if isinstance(raw_details, dict):
        return raw_details
    try:
        parsed = json.loads(raw_details)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_library_item_id(job: BackgroundJob) -> int | None:
    details = _parse_job_details(job.details)
    if details and details.get("library_item_id") is not None:
        try:
            return int(details["library_item_id"])
        except (TypeError, ValueError):
            return None
    if job.description:
        match = re.search(r"library item\s+(\d+)", job.description, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


async def _get_pdf_chunk_counts(item_ids: list[int]) -> dict[int, int]:
    """Read PdfChunk counts per library item without failing the endpoint on graph issues."""
    if not item_ids:
        return {}

    settings = get_settings()
    driver = get_driver()
    item_ids_as_str = [str(item_id) for item_id in item_ids]
    try:
        async with driver.session(database=settings.neo4j_database) as graph_session:
            result = await graph_session.run(
                """
                MATCH (chunk:PdfChunk)
                WHERE toString(chunk.library_item_id) IN $item_ids
                RETURN chunk.library_item_id AS library_item_id, count(chunk) AS chunk_count
                """,
                item_ids=item_ids_as_str,
            )
            rows = await result.data()
    except Exception:
        return {}

    chunk_counts: dict[int, int] = {}
    for row in rows:
        item_id = row.get("library_item_id")
        if item_id is None:
            continue
        chunk_counts[int(item_id)] = int(row.get("chunk_count") or 0)
    return chunk_counts


async def _get_library_embedding_stats(
    session: AsyncSessionCompat,
    ontology_id: int,
) -> dict[str, int]:
    items = (
        await session.execute(
            select(LibraryItem).where(LibraryItem.ontology_id == ontology_id)
        )
    ).scalars().all()
    item_ids = [item.id for item in items]
    chunk_counts = await _get_pdf_chunk_counts(item_ids)

    try:
        async with JobsSessionMaker() as jobs_session:
            job_rows = (
                await jobs_session.execute(
                    select(BackgroundJob)
                    .where(
                        BackgroundJob.job_type == JobType.PDF_BOOK_EMBEDDING,
                        BackgroundJob.ontology_id == ontology_id,
                    )
                    .order_by(BackgroundJob.started_at.desc(), BackgroundJob.id.desc())
                )
            ).scalars().all()
    except Exception:
        logger.exception("Unable to query embedding jobs for library embedding stats")
        job_rows = []

    latest_job_by_item: dict[int, BackgroundJob] = {}
    for job in job_rows:
        item_id = _extract_library_item_id(job)
        if item_id is None or item_id not in item_ids:
            continue
        latest_job_by_item.setdefault(item_id, job)

    embedded = 0
    missing = 0
    failed = 0
    processing = 0
    for item in items:
        chunk_count = chunk_counts.get(item.id, 0)
        latest_job = latest_job_by_item.get(item.id)
        if chunk_count > 0 and item.vectorized:
            embedded += 1
        elif latest_job and latest_job.status in {JobStatus.RUNNING, JobStatus.QUEUED}:
            processing += 1
            missing += 1
        elif latest_job and latest_job.status == JobStatus.FAILED:
            failed += 1
            missing += 1
        else:
            missing += 1

    return {
        "total": len(items),
        "embedded": embedded,
        "unembedded": missing,
        "outdated": failed,
        "processing": processing,
        "failed": failed,
    }


class LibraryUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str


class LibraryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ontology_id: int
    title: str
    authors: str | None
    description: str | None
    cover_url: str | None
    pdf_path: str
    pdf_url: str
    added_at: datetime
    updated_at: datetime
    vectorized: bool
    last_vectorized_at: datetime | None


class LibraryItemUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    authors: str | None = Field(default=None, max_length=512)
    description: str | None = None
    cover_url: str | None = Field(default=None, max_length=512)
    vectorized: bool | None = None
    last_vectorized_at: datetime | None = None


class LibraryBookmarkCreate(BaseModel):
    page: int = Field(ge=1)
    title: str = Field(max_length=255)
    description: str | None = None
    is_private: bool = True
    shared_user_ids: list[int] = Field(default_factory=list)


class LibraryBookmarkUpdate(BaseModel):
    page: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    is_private: bool | None = None
    shared_user_ids: list[int] | None = None


class LibraryBookmarkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    owner: LibraryUserSummary
    page: int
    title: str
    description: str | None
    is_private: bool
    shared_with: list[LibraryUserSummary]
    created_at: datetime
    updated_at: datetime


class LibraryBulkDeleteRequest(BaseModel):
    item_ids: list[int] = Field(default_factory=list)


class LibraryEmbeddingStatsResponse(BaseModel):
    ontology_id: int
    total_nodes: int
    embedded_nodes: int
    unembedded_nodes: int
    outdated_nodes: int
    processing_nodes: int = 0
    failed_nodes: int = 0


def _serialize_bookmark(bookmark: LibraryBookmark) -> LibraryBookmarkRead:
    return LibraryBookmarkRead(
        id=bookmark.id,
        item_id=bookmark.item_id,
        owner=LibraryUserSummary.model_validate(bookmark.owner),
        page=bookmark.page,
        title=bookmark.title,
        description=bookmark.description,
        is_private=bookmark.is_private,
        shared_with=[LibraryUserSummary.model_validate(user) for user in bookmark.shared_with],
        created_at=bookmark.created_at,
        updated_at=bookmark.updated_at,
    )


async def _get_item_or_404(session: AsyncSessionCompat, ontology_id: int, item_id: int) -> LibraryItem:
    item = await session.scalar(
        select(LibraryItem).where(LibraryItem.id == item_id, LibraryItem.ontology_id == ontology_id)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library item not found")
    return item


async def _get_item_by_id_or_404(session: AsyncSessionCompat, item_id: int) -> LibraryItem:
    item = await session.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library item not found")
    return item


async def _get_bookmark_or_404(session: AsyncSessionCompat, bookmark_id: int) -> LibraryBookmark:
    bookmark = await session.get(LibraryBookmark, bookmark_id)
    if bookmark is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bookmark not found")
    return bookmark


async def _delete_library_item_embeddings(item_ids: list[int]) -> None:
    if not item_ids:
        return

    driver = get_driver()
    async with driver.session(database=get_settings().neo4j_database) as graph_session:
        from app.services.pdf_embedding_service import PdfEmbeddingService

        pdf_service = PdfEmbeddingService(graph_session)
        for item_id in item_ids:
            await pdf_service.delete_embeddings(item_id)


def _bookmark_visibility_filter(current_user: User) -> Select[tuple[LibraryBookmark]]:
    return select(LibraryBookmark).where(
        or_(
            LibraryBookmark.owner_id == current_user.id,
            LibraryBookmark.is_private.is_(False),
            LibraryBookmark.shared_with.any(User.id == current_user.id),
        )
    )


def _library_pdf_relative_path(ontology_id: int, item_id: int) -> Path:
    return Path("library") / str(ontology_id) / str(item_id) / "content.pdf"


async def _write_pdf(upload: UploadFile, relative_path: Path) -> None:
    settings = get_settings()
    absolute_path = Path(settings.media_root) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = absolute_path.with_suffix(".tmp")
    total = 0
    upload_name = upload.filename or "unknown.pdf"
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = await upload.read(1_048_576)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.library_max_pdf_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Uploaded PDF exceeds size limit",
                    )
                handle.write(chunk)
        temp_path.replace(absolute_path)
        logger.info(
            "library_pdf_write completed filename=%s relative_path=%s bytes_written=%s",
            upload_name,
            relative_path.as_posix(),
            total,
        )
        if total < 1024:
            logger.warning(
                "library_pdf_write suspiciously_small_pdf filename=%s relative_path=%s bytes_written=%s",
                upload_name,
                relative_path.as_posix(),
                total,
            )
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        await upload.seek(0)


def _require_pdf(upload: UploadFile) -> None:
    if not upload.filename or not upload.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF uploads are supported",
        )


async def _trigger_library_followups(
    *,
    item: LibraryItem,
    ontology_id: int,
    author_id: int,
    auto_extract_metadata: bool,
    auto_embed: bool,
) -> dict[str, str | None]:
    metadata_task_id: str | None = None
    embedding_task_id: str | None = None

    if auto_extract_metadata:
        from app.tasks.library_metadata import extract_metadata

        task = extract_metadata.delay(item.id)
        metadata_task_id = task.id

    if auto_embed:
        from app.tasks.pdf_embedding import embed_pdf_book

        task = embed_pdf_book.delay(
            library_item_id=item.id,
            ontology_id=ontology_id,
            author_type="user",
            author_id=str(author_id),
        )
        embedding_task_id = task.id

    return {
        "metadata_task_id": metadata_task_id,
        "embedding_task_id": embedding_task_id,
    }


def _job_ontology_id(ontology_id: int) -> int | None:
    try:
        return int(ontology_id)
    except ValueError:
        return None


async def _list_items(
    ontology_id: int,
    skip: int,
    limit: int,
    _: User,
    session: AsyncSessionCompat,
) -> list[LibraryItemRead]:
    rows = (
        await session.execute(
            select(LibraryItem)
            .where(LibraryItem.ontology_id == ontology_id)
            .offset(skip)
            .limit(limit)
            .order_by(LibraryItem.id.desc())
        )
    ).scalars().all()
    return [LibraryItemRead.model_validate(row) for row in rows]


@router.get("/{ontology_id}/items", response_model=list[LibraryItemRead])
@legacy_router.get("/{ontology_id}/items", response_model=list[LibraryItemRead])
async def list_library_items(
    ontology_id: int,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> list[LibraryItemRead]:
    return await _list_items(ontology_id, skip, min(limit, 100), current_user, session)


@router.post("/{ontology_id}/items", response_model=LibraryItemRead, status_code=status.HTTP_201_CREATED)
@legacy_router.post("/{ontology_id}/items", response_model=LibraryItemRead, status_code=status.HTTP_201_CREATED)
async def create_library_item(
    ontology_id: int,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    authors: str | None = Form(default=None),
    description: str | None = Form(default=None),
    cover_url: str | None = Form(default=None),
    auto_extract_metadata: bool = Form(default=False),  # noqa: FBT001
    auto_embed: bool = Form(default=False),  # noqa: FBT001
    _: User = Depends(require_roles("admin", "world_builder")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryItemRead:
    _require_pdf(file)
    item = LibraryItem(
        ontology_id=ontology_id,
        title=title or Path(file.filename).stem,
        authors=authors,
        description=description,
        cover_url=cover_url,
        pdf_path="",
        vectorized=False,
        last_vectorized_at=None,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    relative_path = _library_pdf_relative_path(ontology_id, item.id)
    await _write_pdf(file, relative_path)
    item.pdf_path = relative_path.as_posix()
    session.add(item)
    await session.commit()
    await session.refresh(item)
    await _trigger_library_followups(
        item=item,
        ontology_id=ontology_id,
        author_id=_.id,
        auto_extract_metadata=auto_extract_metadata,
        auto_embed=auto_embed,
    )
    return LibraryItemRead.model_validate(item)


@router.get("/{ontology_id}/items/{item_id}", response_model=LibraryItemRead)
@legacy_router.get("/{ontology_id}/items/{item_id}", response_model=LibraryItemRead)
async def get_library_item(
    ontology_id: int,
    item_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryItemRead:
    item = await _get_item_or_404(session, ontology_id, item_id)
    return LibraryItemRead.model_validate(item)


@router.put("/{ontology_id}/items/{item_id}", response_model=LibraryItemRead)
@legacy_router.put("/{ontology_id}/items/{item_id}", response_model=LibraryItemRead)
async def update_library_item(
    ontology_id: int,
    item_id: int,
    payload: LibraryItemUpdate,
    _: User = Depends(require_roles("admin", "world_builder")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryItemRead:
    item = await _get_item_or_404(session, ontology_id, item_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(item, key, value)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return LibraryItemRead.model_validate(item)


@router.delete("/{ontology_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
@legacy_router.delete("/{ontology_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_library_item(
    ontology_id: int,
    item_id: int,
    _: User = Depends(require_roles("admin", "world_builder")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> Response:
    item = await _get_item_or_404(session, ontology_id, item_id)
    await _delete_library_item_embeddings([item.id])
    await session.delete(item)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{ontology_id}/items/bulk-delete", response_model=dict)
@legacy_router.post("/{ontology_id}/items/bulk-delete", response_model=dict)
async def bulk_delete_library_items(
    ontology_id: int,
    payload: LibraryBulkDeleteRequest,
    _: User = Depends(require_roles("admin", "world_builder")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> dict[str, int]:
    if not payload.item_ids:
        return {"deleted": 0}

    rows = (
        await session.execute(
            select(LibraryItem).where(
                LibraryItem.ontology_id == ontology_id,
                LibraryItem.id.in_(payload.item_ids),
            )
        )
    ).scalars().all()
    await _delete_library_item_embeddings([row.id for row in rows])
    for row in rows:
        await session.delete(row)
    await session.commit()
    return {"deleted": len(rows)}


@router.post("/{ontology_id}/items/{item_id}/content", response_model=LibraryItemRead)
@legacy_router.post("/{ontology_id}/items/{item_id}/content", response_model=LibraryItemRead)
@router.put("/{ontology_id}/items/{item_id}/pdf", response_model=LibraryItemRead)
@legacy_router.put("/{ontology_id}/items/{item_id}/pdf", response_model=LibraryItemRead)
async def replace_library_item_content(
    ontology_id: int,
    item_id: int,
    file: UploadFile = File(...),
    _: User = Depends(require_roles("admin", "world_builder")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryItemRead:
    _require_pdf(file)
    item = await _get_item_or_404(session, ontology_id, item_id)

    # Hard-delete old embeddings when replacing content so stale chunks cannot leak.
    driver = get_driver()
    async with driver.session(database=get_settings().neo4j_database) as graph_session:
        from app.services.pdf_embedding_service import PdfEmbeddingService

        pdf_service = PdfEmbeddingService(graph_session)
        await pdf_service.delete_embeddings(item.id)

    relative_path = _library_pdf_relative_path(ontology_id, item.id)
    await _write_pdf(file, relative_path)
    item.pdf_path = relative_path.as_posix()
    item.vectorized = False
    item.last_vectorized_at = None
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return LibraryItemRead.model_validate(item)


@router.get("/embedding-stats", response_model=LibraryEmbeddingStatsResponse)
@legacy_router.get("/embedding-stats", response_model=LibraryEmbeddingStatsResponse)
async def get_library_embedding_stats(
    ontology_id: int = Query(...),
    _: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryEmbeddingStatsResponse:
    stats = await _get_library_embedding_stats(session, ontology_id)

    return LibraryEmbeddingStatsResponse(
        ontology_id=ontology_id,
        total_nodes=stats["total"],
        embedded_nodes=stats["embedded"],
        unembedded_nodes=stats["unembedded"],
        outdated_nodes=stats["outdated"],
        processing_nodes=stats["processing"],
        failed_nodes=stats["failed"],
    )


@router.get("/embedding-jobs")
@legacy_router.get("/embedding-jobs")
async def list_embedding_jobs(
    ontology_id: int | None = Query(default=None),
    limit: int = 10,
    _: User = Depends(get_current_user),
) -> list[dict[str, object | None]]:
    async with JobsSessionMaker() as session:
        query = select(BackgroundJob).where(BackgroundJob.job_type == JobType.PDF_BOOK_EMBEDDING)
        if ontology_id is not None:
            query = query.where(BackgroundJob.ontology_id == ontology_id)
        query = query.order_by(BackgroundJob.started_at.desc()).limit(min(limit, 100))
        jobs = (await session.execute(query)).scalars().all()

    return [
        {
            "job_id": job.id,
            "library_item_id": _extract_library_item_id(job),
            "ontology_id": job.ontology_id,
            "status": job.status,
            "progress": job.progress,
            "description": job.description,
            "started_at": job.started_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "duration_seconds": job.duration_seconds,
            "error_message": job.error_message,
            "details": _parse_job_details(job.details) or job.details,
            "celery_task_id": job.celery_task_id,
        }
        for job in jobs
    ]


@router.delete("/{ontology_id}/items/{item_id}/embeddings", status_code=status.HTTP_200_OK)
@legacy_router.delete("/{ontology_id}/items/{item_id}/embeddings", status_code=status.HTTP_200_OK)
async def clear_library_item_embeddings(
    ontology_id: int,
    item_id: int,
    _: User = Depends(require_roles("admin")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> dict[str, str | int]:
    item = await _get_item_or_404(session, ontology_id, item_id)

    driver = get_driver()
    async with driver.session(database=get_settings().neo4j_database) as graph_session:
        from app.services.pdf_embedding_service import PdfEmbeddingService

        pdf_service = PdfEmbeddingService(graph_session)
        deleted_count = await pdf_service.delete_embeddings(item.id)

    item.vectorized = False
    item.last_vectorized_at = None
    session.add(item)
    await session.commit()

    return {
        "message": f"Cleared embeddings for library item {item_id}",
        "library_item_id": item_id,
        "ontology_id": ontology_id,
        "chunks_deleted": deleted_count,
    }


@router.delete("/admin/clear-all-embeddings", status_code=status.HTTP_200_OK)
@legacy_router.delete("/admin/clear-all-embeddings", status_code=status.HTTP_200_OK)
async def clear_all_library_embeddings(
    _: User = Depends(require_roles("admin")),
    ontology_id: int | None = Query(default=None),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> dict[str, str | int | None]:
    if ontology_id is not None:
        items = (
            await session.execute(select(LibraryItem).where(LibraryItem.ontology_id == ontology_id))
        ).scalars().all()
    else:
        items = (await session.execute(select(LibraryItem))).scalars().all()

    driver = get_driver()
    total_deleted = 0
    orphan_deleted = 0
    async with driver.session(database=get_settings().neo4j_database) as graph_session:
        from app.services.pdf_embedding_service import PdfEmbeddingService

        pdf_service = PdfEmbeddingService(graph_session)
        if ontology_id is not None:
            total_deleted = await pdf_service.delete_embeddings_for_ontology(
                ontology_id=ontology_id,
                library_item_ids=[item.id for item in items],
            )
        else:
            total_deleted = await pdf_service.delete_all_embeddings()

        # Also remove orphan chunks not tied to SQL library_items to avoid stale pull risk.
        orphan_deleted = await pdf_service.delete_orphan_embeddings(
            valid_library_item_ids=[item.id for item in items]
        )

    item_ids = [item.id for item in items]
    if ontology_id is not None:
        await session.execute(
            update(LibraryItem)
            .where(LibraryItem.ontology_id == ontology_id)
            .values(vectorized=False, last_vectorized_at=None)
        )
    elif items:
        await session.execute(update(LibraryItem).values(vectorized=False, last_vectorized_at=None))
    await session.commit()

    jobs_deleted = 0
    ontology_filter = _job_ontology_id(ontology_id) if ontology_id is not None else None
    try:
        async with JobsSessionMaker() as jobs_session:
            query = delete(BackgroundJob).where(
                BackgroundJob.job_type == JobType.PDF_BOOK_EMBEDDING,
                BackgroundJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            if ontology_filter is not None:
                query = query.where(BackgroundJob.ontology_id == ontology_filter)
            result = await jobs_session.execute(query)
            jobs_deleted = int(result.rowcount or 0)
            await jobs_session.commit()
    except Exception:
        logger.exception("Unable to clear queued/running embedding jobs")

    return {
        "message": f"Cleared embeddings for {len(item_ids)} library items",
        "items_affected": len(item_ids),
        "ontology_id": ontology_id,
        "chunks_deleted": total_deleted,
        "orphan_chunks_deleted": orphan_deleted,
        "jobs_deleted": jobs_deleted,
    }


@router.post("/{ontology_id}/items/{item_id}/trigger-embedding", status_code=status.HTTP_202_ACCEPTED)
@legacy_router.post("/{ontology_id}/items/{item_id}/trigger-embedding", status_code=status.HTTP_202_ACCEPTED)
async def trigger_embedding(
    ontology_id: int,
    item_id: int,
    current_user: User = Depends(require_roles("admin", "world_builder")),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> dict[str, str | int]:
    item = await _get_item_or_404(session, ontology_id, item_id)
    from app.tasks.pdf_embedding import embed_pdf_book

    task = embed_pdf_book.delay(
        library_item_id=item.id,
        ontology_id=ontology_id,
        author_type="user",
        author_id=str(current_user.id),
    )
    return {
        "message": f"Embedding job triggered for library item {item_id}",
        "library_item_id": item_id,
        "ontology_id": ontology_id,
        "author_id": current_user.id,
        "celery_task_id": task.id,
    }


@router.get("/{ontology_id}/items/{item_id}/embedding-status")
@legacy_router.get("/{ontology_id}/items/{item_id}/embedding-status")
async def get_embedding_status(
    ontology_id: int,
    item_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> dict[str, Any]:
    item = await _get_item_or_404(session, ontology_id, item_id)
    total_chunks = 0
    settings = get_settings()
    driver = get_driver()
    async with driver.session(database=settings.neo4j_database) as graph_session:
        chunk_result = await graph_session.run(
            """
            MATCH (chunk:PdfChunk)
            WHERE toString(chunk.library_item_id) = $item_id
            RETURN count(chunk) AS total_chunks
            """,
            item_id=str(item.id),
        )
        chunk_record = await chunk_result.single()
        total_chunks = int(chunk_record["total_chunks"] if chunk_record else 0)

    job_ontology_id = _job_ontology_id(ontology_id)
    latest_job = None
    if job_ontology_id is not None:
        try:
            async with JobsSessionMaker() as jobs_session:
                candidate_jobs = (
                    await jobs_session.execute(
                        select(BackgroundJob)
                        .where(
                            BackgroundJob.job_type == JobType.PDF_BOOK_EMBEDDING,
                            BackgroundJob.ontology_id == job_ontology_id,
                        )
                        .order_by(BackgroundJob.id.desc())
                    )
                ).scalars().all()
                latest_job = next(
                    (
                        job
                        for job in candidate_jobs
                        if _extract_library_item_id(job) == item.id
                    ),
                    None,
                )
        except Exception:
            logger.exception("Unable to query embedding jobs for item status")

    latest_job_details = _parse_job_details(latest_job.details) if latest_job else None

    return {
        "library_item_id": item.id,
        "ontology_id": ontology_id,
        "vectorized": item.vectorized,
        "last_vectorized_at": item.last_vectorized_at.isoformat() if item.last_vectorized_at else None,
        "total_chunks": total_chunks,
        "is_embedded": bool(item.vectorized and total_chunks > 0),
        "job_status": latest_job.status if latest_job else None,
        "job_error": latest_job.error_message if latest_job else None,
        "job_id": latest_job.id if latest_job else None,
        "job_progress": latest_job.progress if latest_job else None,
        "job_details": latest_job_details,
        "celery_task_id": latest_job.celery_task_id if latest_job else None,
    }


@router.get("/items/{item_id}/bookmarks", response_model=list[LibraryBookmarkRead])
@legacy_router.get("/items/{item_id}/bookmarks", response_model=list[LibraryBookmarkRead])
async def list_bookmarks(
    item_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> list[LibraryBookmarkRead]:
    await _get_item_by_id_or_404(session, item_id)
    rows = (
        await session.execute(_bookmark_visibility_filter(current_user).where(LibraryBookmark.item_id == item_id))
    ).scalars().all()
    return [_serialize_bookmark(row) for row in rows]


@router.post("/items/{item_id}/bookmarks", response_model=LibraryBookmarkRead, status_code=status.HTTP_201_CREATED)
@legacy_router.post("/items/{item_id}/bookmarks", response_model=LibraryBookmarkRead, status_code=status.HTTP_201_CREATED)
async def create_bookmark(
    item_id: int,
    payload: LibraryBookmarkCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryBookmarkRead:
    await _get_item_by_id_or_404(session, item_id)
    shared_users: list[User] = []
    if payload.shared_user_ids:
        shared_users = (await session.execute(select(User).where(User.id.in_(payload.shared_user_ids)))).scalars().all()
    bookmark = LibraryBookmark(
        item_id=item_id,
        owner_id=current_user.id,
        page=payload.page,
        title=payload.title,
        description=payload.description,
        is_private=payload.is_private,
        shared_with=shared_users,
    )
    session.add(bookmark)
    await session.commit()
    await session.refresh(bookmark)
    return _serialize_bookmark(bookmark)


@router.put("/bookmarks/{bookmark_id}", response_model=LibraryBookmarkRead)
@legacy_router.put("/bookmarks/{bookmark_id}", response_model=LibraryBookmarkRead)
async def update_bookmark(
    bookmark_id: int,
    payload: LibraryBookmarkUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryBookmarkRead:
    bookmark = await _get_bookmark_or_404(session, bookmark_id)
    if bookmark.owner_id != current_user.id and current_user.role.lower() not in {"admin", "world_builder"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    data = payload.model_dump(exclude_unset=True)
    shared_ids = data.pop("shared_user_ids", None)
    for key, value in data.items():
        setattr(bookmark, key, value)

    if shared_ids is not None:
        bookmark.shared_with = (await session.execute(select(User).where(User.id.in_(shared_ids)))).scalars().all()

    session.add(bookmark)
    await session.commit()
    await session.refresh(bookmark)
    return _serialize_bookmark(bookmark)


@router.delete("/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
@legacy_router.delete("/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bookmark(
    bookmark_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> Response:
    bookmark = await _get_bookmark_or_404(session, bookmark_id)
    if bookmark.owner_id != current_user.id and current_user.role.lower() not in {"admin", "world_builder"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    await session.delete(bookmark)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/bookmarks/{bookmark_id}/share/me", response_model=LibraryBookmarkRead)
@legacy_router.delete("/bookmarks/{bookmark_id}/share/me", response_model=LibraryBookmarkRead)
async def leave_shared_bookmark(
    bookmark_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> LibraryBookmarkRead:
    bookmark = await _get_bookmark_or_404(session, bookmark_id)
    if bookmark.owner_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bookmark owners cannot remove themselves")

    bookmark.shared_with = [user for user in bookmark.shared_with if user.id != current_user.id]
    session.add(bookmark)
    await session.commit()
    await session.refresh(bookmark)
    return _serialize_bookmark(bookmark)
