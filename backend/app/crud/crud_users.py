from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from app.models.model_user import User
from app import auth, schemas
from app.schemas.schema_user import UserCreate, UserUpdate
from datetime import datetime


async def get_user_by_email(session: AsyncSession, email: str):
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: int):
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, user: UserCreate):
    db_user = User(
        nickname=user.nickname,
        email=user.email,
        hashed_password=auth.hash_password(user.password),
        role=user.role,
        image_url=user.image_url,
        timezone=user.timezone,
    )
    session.add(db_user)
    try:
        await session.commit()
        await session.refresh(db_user)
        return db_user
    except IntegrityError:
        await session.rollback()
        return None


async def update_user_crud(
    session: AsyncSession, user_id: int, user_update: UserUpdate
):
    db_user = await get_user(session, user_id)
    if not db_user:
        return None
    for field, value in user_update.dict(exclude_unset=True).items():
        if field == "password" and value:
            db_user.hashed_password = auth.hash_password(value)
        elif field != "password":
            setattr(db_user, field, value)
    db_user.updated_at = datetime.utcnow()
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user


async def delete_user_crud(session: AsyncSession, user_id: int):
    db_user = await get_user(session, user_id)
    if db_user:
        await session.delete(db_user)
        await session.commit()
        return True
    return False


async def list_all_users(session: AsyncSession, skip=0, limit=100):
    result = await session.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()
