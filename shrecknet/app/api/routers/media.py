from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db_session
from app.models import MediaItem, User

router = APIRouter(prefix="/media", tags=["media"])


class MediaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: str
    url: str


@router.get("", response_model=list[MediaRead])
async def list_media(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[MediaRead]:
    rows = (await session.execute(select(MediaItem))).scalars().all()
    return [MediaRead.model_validate(row) for row in rows]
