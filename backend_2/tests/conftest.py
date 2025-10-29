from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.db.base import Base
from app.db.session import engine as default_engine
from app.main import create_app
from app.models.user import UserRole


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
    from httpx import ASGITransport

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

    async with AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()
    # Ensure default engine is not persisted with test tables
    async with default_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture()
async def admin_token(client: AsyncClient) -> str:
    """Create an admin user and return auth token."""
    admin_payload = {
        "username": "test-admin",
        "password": "AdminPass123",
        "full_name": "Test Admin",
        "email": "admin@test.com",
        "timezone": "UTC",
        "role": UserRole.ADMIN.value,
    }
    
    # Register admin
    register_response = await client.post("/users/", json=admin_payload)
    assert register_response.status_code == 201
    
    # Get token
    token_response = await client.post(
        "/auth/token",
        data={
            "username": admin_payload["username"],
            "password": admin_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    return token_response.json()["access_token"]


@pytest_asyncio.fixture()
async def user_token(client: AsyncClient, admin_token: str) -> str:
    """Create a regular player user and return auth token. Depends on admin_token to ensure admin is created first."""
    user_payload = {
        "username": "test-player",
        "password": "PlayerPass123",
        "full_name": "Test Player",
        "email": "player@test.com",
        "timezone": "UTC",
        "role": UserRole.PLAYER.value,
    }
    
    # Register user
    register_response = await client.post("/users/", json=user_payload)
    assert register_response.status_code == 201
    
    # Get token
    token_response = await client.post(
        "/auth/token",
        data={
            "username": user_payload["username"],
            "password": user_payload["password"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert token_response.status_code == 200
    return token_response.json()["access_token"]


@pytest_asyncio.fixture()
async def ontology_id(client: AsyncClient, admin_token: str) -> int:
    """Create a test ontology and return its ID."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    ontology_data = {
        "name": "Test Ontology",
        "description": "A test ontology for agent tests",
    }
    
    response = await client.post("/ontologies/", json=ontology_data, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]

