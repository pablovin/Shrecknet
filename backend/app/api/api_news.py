from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from app.database import get_session
from app.dependencies import get_current_user
from app.models.model_user import User
from app.schemas.schema_news import NewsCreate, NewsRead
from app.crud.crud_news import create_news, get_news_for_user, mark_news_seen

router = APIRouter(prefix="/news", tags=["news"])

@router.post("/", response_model=NewsRead)
async def create_news_endpoint(
    news: NewsCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if user.role != "system admin":
        raise HTTPException(status_code=403, detail="Not authorized")
    return await create_news(session, news)

@router.get("/", response_model=List[NewsRead])
async def list_news_endpoint(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await get_news_for_user(session, user.id)

@router.post("/{news_id}/seen")
async def mark_seen_endpoint(
    news_id: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await mark_news_seen(session, user.id, news_id)
    return {"ok": True}
