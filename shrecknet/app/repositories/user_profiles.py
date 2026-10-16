from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.db.session import AsyncSessionCompat
from app.models import User


async def public_profiles(session: AsyncSessionCompat, ids: list[int]) -> list[User]:
    rows = await session.execute(
        select(User)
        .options(load_only(User.id, User.username, User.full_name, User.avatar_url))
        .where(User.id.in_(set(ids)))
        .order_by(User.id)
    )
    return list(rows.scalars().all())
