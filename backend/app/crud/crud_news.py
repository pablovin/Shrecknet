from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.model_news import News, NewsView
from app.schemas.schema_news import NewsCreate, NewsRead

async def create_news(session: AsyncSession, news_in: NewsCreate) -> News:
    news = News(**news_in.model_dump())
    session.add(news)
    await session.commit()
    await session.refresh(news)
    return news

async def get_news_for_user(session: AsyncSession, user_id: int) -> List[NewsRead]:
    result = await session.execute(select(News))
    news_items = result.scalars().all()
    seen_ids_result = await session.execute(
        select(NewsView.news_id).where(NewsView.user_id == user_id)
    )
    seen_ids = set(seen_ids_result.scalars().all())
    return [
        NewsRead(
            id=n.id,
            title=n.title,
            type=n.type,
            description=n.description,
            created_at=n.created_at,
            seen=n.id in seen_ids,
        )
        for n in news_items
    ]

async def mark_news_seen(session: AsyncSession, user_id: int, news_id: int) -> None:
    result = await session.execute(
        select(NewsView).where(NewsView.user_id == user_id, NewsView.news_id == news_id)
    )
    view = result.scalar_one_or_none()
    if not view:
        view = NewsView(user_id=user_id, news_id=news_id)
        session.add(view)
        await session.commit()
    return view
