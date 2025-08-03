from typing import List
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.model_news import News, NewsView, NewsTarget
from app.schemas.schema_news import NewsCreate, NewsRead


async def create_news(session: AsyncSession, news_in: NewsCreate) -> News:
    news_data = news_in.model_dump(exclude={"user_ids"})
    news = News(**news_data)
    session.add(news)
    await session.commit()
    await session.refresh(news)

    if news_in.user_ids:
        targets = [NewsTarget(news_id=news.id, user_id=uid) for uid in news_in.user_ids]
        session.add_all(targets)
        await session.commit()

    return news


async def get_news_for_user(session: AsyncSession, user_id: int) -> List[NewsRead]:
    targeted_subq = select(NewsTarget.news_id).where(NewsTarget.user_id == user_id)
    no_target_exists = (
        ~select(NewsTarget.id).where(NewsTarget.news_id == News.id).exists()
    )
    result = await session.execute(
        select(News).where(or_(News.id.in_(targeted_subq), no_target_exists))
    )
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
