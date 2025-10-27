from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import selectinload

from app.models.library import (
    LibraryBookmark,
    LibraryItem,
    library_bookmark_shares,
)
from app.models.user import User
from app.repositories.base import BaseRepository


class LibraryRepository(BaseRepository):
    """Data access helpers for library items and bookmarks."""

    async def list_items(
        self, ontology_id: int, *, skip: int = 0, limit: int = 50,
    ) -> Sequence[LibraryItem]:
        query: Select[tuple[LibraryItem]] = (
            select(LibraryItem)
            .where(LibraryItem.ontology_id == ontology_id)
            .order_by(LibraryItem.added_at.desc())
            .offset(skip)
            .limit(limit)
            .options(selectinload(LibraryItem.bookmarks))
        )
        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def get_item(self, ontology_id: int, item_id: int) -> LibraryItem | None:
        result = await self.session.execute(
            select(LibraryItem)
            .where(LibraryItem.ontology_id == ontology_id, LibraryItem.id == item_id)
            .options(
                selectinload(LibraryItem.bookmarks).selectinload(
                    LibraryBookmark.shared_with
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_item_by_id(self, item_id: int) -> LibraryItem | None:
        result = await self.session.execute(
            select(LibraryItem)
            .where(LibraryItem.id == item_id)
            .options(
                selectinload(LibraryItem.bookmarks).selectinload(
                    LibraryBookmark.shared_with
                )
            )
        )
        return result.scalar_one_or_none()

    async def create_item(self, data: dict[str, Any]) -> LibraryItem:
        item = LibraryItem(**data)
        await self.save(item)
        await self.session.flush()
        return item

    async def update_item(self, item: LibraryItem, data: dict[str, Any]) -> LibraryItem:
        for key, value in data.items():
            setattr(item, key, value)
        await self.save(item)
        await self.session.refresh(item)
        return item

    async def delete_item(self, item: LibraryItem) -> None:
        await self.delete(item)

    # Bookmarks ------------------------------------------------------------
    async def list_bookmarks_for_item(
        self, item_id: int, viewer_id: int,
    ) -> Sequence[LibraryBookmark]:
        share_alias = library_bookmark_shares.alias()
        query = (
            select(LibraryBookmark)
            .options(selectinload(LibraryBookmark.shared_with))
            .join(LibraryBookmark.owner)
            .outerjoin(
                share_alias,
                and_(
                    share_alias.c.bookmark_id == LibraryBookmark.id,
                    share_alias.c.user_id == viewer_id,
                ),
            )
            .where(
                LibraryBookmark.item_id == item_id,
                or_(
                    LibraryBookmark.owner_id == viewer_id,
                    and_(
                        LibraryBookmark.is_private.is_(False),
                        share_alias.c.user_id.is_not(None),
                    ),
                ),
            )
            .order_by(LibraryBookmark.created_at.desc())
        )
        result = await self.session.execute(query)
        return result.scalars().unique().all()

    async def get_bookmark(self, bookmark_id: int) -> LibraryBookmark | None:
        result = await self.session.execute(
            select(LibraryBookmark)
            .options(
                selectinload(LibraryBookmark.shared_with),
                selectinload(LibraryBookmark.item),
            )
            .where(LibraryBookmark.id == bookmark_id)
        )
        return result.scalar_one_or_none()

    async def create_bookmark(
        self, data: dict[str, Any], shared_users: Sequence[User]
    ) -> LibraryBookmark:
        bookmark = LibraryBookmark(**data)
        bookmark.shared_with = list(shared_users)
        await self.save(bookmark)
        await self.session.refresh(bookmark)
        return bookmark

    async def update_bookmark(
        self,
        bookmark: LibraryBookmark,
        data: dict[str, Any],
        shared_users: Sequence[User] | None = None,
    ) -> LibraryBookmark:
        for key, value in data.items():
            setattr(bookmark, key, value)
        if shared_users is not None:
            bookmark.shared_with = list(shared_users)
        await self.save(bookmark)
        await self.session.refresh(bookmark)
        return bookmark

    async def delete_bookmark(self, bookmark: LibraryBookmark) -> None:
        await self.delete(bookmark)

    async def list_shares(self, bookmark_id: int) -> Sequence[User]:
        result = await self.session.execute(
            select(User)
            .join(
                library_bookmark_shares, library_bookmark_shares.c.user_id == User.id,
            )
            .where(library_bookmark_shares.c.bookmark_id == bookmark_id)
        )
        return result.scalars().all()
