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
    title: str = Form(...),
    description: str | None = Form(None),
    cover_url: str | None = Form(None),
    service: LibraryService = Depends(get_library_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> LibraryItemRead:
    try:
        item = await service.create_item(
            ontology_id,
            title=title,
            description=description,
            cover_url=cover_url,
            pdf=file,
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
