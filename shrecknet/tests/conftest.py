from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.db.base import Base
from app.db.jobs_session import get_jobs_session
from app.main import app


@pytest_asyncio.fixture()
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def session_maker(
    test_engine: AsyncEngine,
) -> AsyncGenerator[async_sessionmaker, None]:
    yield async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture()
async def client(
    session_maker: async_sessionmaker,
) -> AsyncGenerator[AsyncClient, None]:
    async def get_test_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_session
    app.dependency_overrides[get_jobs_session] = get_test_session

    @asynccontextmanager
    async def lifespan_override(_app):
        yield

    app.router.lifespan_context = lifespan_override

    async with AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()
