from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.db.base import Base
from app.db.session import engine as default_engine
from app.main import create_app


@pytest.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(test_engine: AsyncEngine) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    async def get_test_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_session

    @asynccontextmanager
    async def lifespan_override(_app):
        # Skip touching the default engine during tests.
        yield

    app.router.lifespan_context = lifespan_override

    async with AsyncClient(app=app, base_url="http://test") as async_client:
        yield async_client

    app.dependency_overrides.clear()
    # Ensure default engine is not persisted with test tables
    async with default_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
