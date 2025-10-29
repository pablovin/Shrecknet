from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.library import LibraryBookmark, LibraryItem
from app.models.ontology import Ontology
from app.models.user import User
from app.repositories.library_repository import LibraryRepository

settings = get_settings()


class PdfValidationError(ValueError):
    """Raised when uploaded PDF fails validation."""


class LibraryService:
    """Business logic for ontology library items and bookmarks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = LibraryRepository(session)
        self.base_path = Path(settings.media_root)
        self.max_pdf_bytes = settings.library_max_pdf_bytes

    # Library items -----------------------------------------------------
    async def list_items(
        self, ontology_id: int, *, skip: int = 0, limit: int = 50
    ) -> list[LibraryItem]:
        await self._assert_ontology_exists(ontology_id)
        return list(
            await self.repository.list_items(ontology_id, skip=skip, limit=limit)
        )

    async def get_item(self, ontology_id: int, item_id: int) -> LibraryItem | None:
        await self._assert_ontology_exists(ontology_id)
        return await self.repository.get_item(ontology_id, item_id)

    async def get_item_by_id(self, item_id: int) -> LibraryItem | None:
        return await self.repository.get_item_by_id(item_id)

    async def create_item(
        self,
        ontology_id: int,
        *,
        title: str,
        description: str | None,
        cover_url: str | None,
        pdf: UploadFile,
    ) -> LibraryItem:
        await self._assert_ontology_exists(ontology_id)
        await self._validate_pdf(pdf)
        item = await self.repository.create_item(
            {
                "ontology_id": ontology_id,
                "title": title,
                "description": description,
                "cover_url": cover_url,
                "pdf_path": "",
            }
        )
        relative_path = self._build_pdf_relative_path(ontology_id, item.id)
        await self._write_pdf(pdf, relative_path)
        item.pdf_path = relative_path.as_posix()
        await self.repository.save(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def update_item(
        self,
        item: LibraryItem,
        *,
        title: str | None = None,
        description: str | None = None,
        cover_url: str | None = None,
        vectorized: bool | None = None,
        last_vectorized_at: datetime | None = None,
    ) -> LibraryItem:
        data: dict[str, object | None] = {}
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description
        if cover_url is not None:
            data["cover_url"] = cover_url
        if vectorized is not None:
            data["vectorized"] = vectorized
        if last_vectorized_at is not None:
            data["last_vectorized_at"] = last_vectorized_at
        updated = await self.repository.update_item(item, data)
        await self.session.commit()
        return updated

    async def replace_pdf(self, item: LibraryItem, pdf: UploadFile) -> LibraryItem:
        await self._validate_pdf(pdf)
        relative_path = self._build_pdf_relative_path(item.ontology_id, item.id)
        await self._write_pdf(pdf, relative_path)
        item.pdf_path = relative_path.as_posix()
        await self.repository.save(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def delete_item(self, item: LibraryItem) -> None:
        await self.repository.delete_item(item)
        await self.session.commit()
        self._delete_pdf_file(item)

    # Bookmarks ---------------------------------------------------------
    async def list_bookmarks(
        self, item: LibraryItem, viewer: User
    ) -> list[LibraryBookmark]:
        return list(await self.repository.list_bookmarks_for_item(item.id, viewer.id))

    async def create_bookmark(
        self,
        item: LibraryItem,
        owner: User,
        *,
        page: int,
        title: str,
        description: str | None,
        is_private: bool,
        shared_user_ids: Sequence[int],
    ) -> LibraryBookmark:
        if is_private:
            shared_users: Sequence[User] = []
        else:
            shared_users = await self._resolve_share_users(owner.id, shared_user_ids)
        bookmark = await self.repository.create_bookmark(
            {
                "item_id": item.id,
                "owner_id": owner.id,
                "page": page,
                "title": title,
                "description": description,
                "is_private": is_private,
            },
            shared_users,
        )
        await self.session.commit()
        await self.session.refresh(bookmark)
        return bookmark

    async def get_bookmark(self, bookmark_id: int) -> LibraryBookmark | None:
        return await self.repository.get_bookmark(bookmark_id)

    async def update_bookmark(
        self,
        bookmark: LibraryBookmark,
        *,
        page: int | None = None,
        title: str | None = None,
        description: str | None = None,
        is_private: bool | None = None,
        shared_user_ids: Sequence[int] | None = None,
    ) -> LibraryBookmark:
        new_is_private = is_private if is_private is not None else bookmark.is_private

        data: dict[str, object | None] = {}
        if page is not None:
            data["page"] = page
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description
        if is_private is not None:
            data["is_private"] = is_private

        shared_users: Sequence[User] | None = None
        if new_is_private:
            shared_users = []
        else:
            desired_ids: Sequence[int]
            if shared_user_ids is not None:
                desired_ids = shared_user_ids
            else:
                desired_ids = [user.id for user in bookmark.shared_with]
            shared_users = await self._resolve_share_users(
                bookmark.owner_id, desired_ids
            )

        updated = await self.repository.update_bookmark(bookmark, data, shared_users)
        await self.session.commit()
        return updated

    async def delete_bookmark(self, bookmark: LibraryBookmark) -> None:
        await self.repository.delete_bookmark(bookmark)
        await self.session.commit()

    # Serialization helpers ---------------------------------------------
    def build_pdf_url(self, item: LibraryItem) -> str:
        base_url = (
            settings.media_public_url.rstrip("/")
            if settings.media_public_url
            else settings.media_base_url.rstrip("/")
        )
        return f"{base_url}/{item.pdf_path}"

    def serialize_item(self, item: LibraryItem) -> dict:
        return {
            "id": item.id,
            "ontology_id": item.ontology_id,
            "title": item.title,
            "description": item.description,
            "cover_url": item.cover_url,
            "added_at": item.added_at,
            "updated_at": item.updated_at,
            "vectorized": item.vectorized,
            "last_vectorized_at": item.last_vectorized_at,
            "pdf_url": self.build_pdf_url(item),
        }

    def serialize_bookmark(self, bookmark: LibraryBookmark) -> dict:
        return {
            "id": bookmark.id,
            "page": bookmark.page,
            "title": bookmark.title,
            "description": bookmark.description,
            "is_private": bookmark.is_private,
            "created_at": bookmark.created_at,
            "updated_at": bookmark.updated_at,
            "owner": {
                "id": bookmark.owner.id,
                "username": bookmark.owner.username,
                "full_name": bookmark.owner.full_name,
            },
            "shared_with": [
                {
                    "id": user.id,
                    "username": user.username,
                    "full_name": user.full_name,
                }
                for user in bookmark.shared_with
            ],
        }

    # Internal helpers ---------------------------------------------------
    async def _assert_ontology_exists(self, ontology_id: int) -> None:
        result = await self.session.execute(
            select(Ontology.id).where(Ontology.id == ontology_id)
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Ontology not found")

    def _build_pdf_relative_path(self, ontology_id: int, item_id: int) -> Path:
        return Path("library") / str(ontology_id) / str(item_id) / "content.pdf"

    async def _write_pdf(self, upload: UploadFile, relative_path: Path) -> None:
        absolute_path = self.base_path / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = absolute_path.with_suffix(".tmp")
        total = 0
        try:
            with temp_path.open("wb") as buffer:
                while True:
                    chunk = await upload.read(1_048_576)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_pdf_bytes:
                        raise PdfValidationError("Uploaded PDF exceeds size limit")
                    buffer.write(chunk)
            temp_path.replace(absolute_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.seek(0)

    def _delete_pdf_file(self, item: LibraryItem) -> None:
        if not item.pdf_path:
            return
        file_path = self.base_path / item.pdf_path
        try:
            file_path.unlink(missing_ok=True)
            parent = file_path.parent
            while parent != self.base_path:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        except OSError:
            pass

    async def _validate_pdf(self, upload: UploadFile) -> None:
        if not upload.filename or not upload.filename.lower().endswith(".pdf"):
            raise PdfValidationError("Only PDF files are supported")
        content_type = upload.content_type or ""
        if not content_type.endswith("pdf"):
            # Allow fallback based on extension
            if not upload.filename.lower().endswith(".pdf"):
                raise PdfValidationError("Invalid PDF content type")

    async def _resolve_share_users(
        self, owner_id: int, user_ids: Sequence[int]
    ) -> list[User]:
        if not user_ids:
            raise ValueError("Shared bookmarks must target at least one user")
        result = await self.session.execute(select(User).where(User.id.in_(user_ids)))
        users = result.scalars().all()
        missing = set(user_ids) - {user.id for user in users}
        if missing:
            raise ValueError(f"Share targets not found: {sorted(missing)}")
        if owner_id in {user.id for user in users}:
            raise ValueError("Owner cannot be included in share targets")
        return list(users)
