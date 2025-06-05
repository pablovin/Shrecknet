import pytest
from httpx import AsyncClient
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import asyncio
import pytest_asyncio
from httpx import AsyncClient
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import get_session

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def session(test_engine):
    async_session_maker = sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session

@pytest.fixture(autouse=True)
def override_get_session(monkeypatch, session):
    async def _override():
        yield session
    monkeypatch.setattr("app.database.get_session", _override)

@pytest_asyncio.fixture
async def async_client(session):
    async def get_session_override():
        yield session
    app.dependency_overrides[get_session] = get_session_override

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def create_user(async_client):
    async def _create_user(email, password, role):
        user_data = {
            "nickname": email.split('@')[0],
            "email": email,
            "password": password,
            "role": role,
            "image_url": "http://test"
        }

        print (f"User created: {email}")
        response = await async_client.post("/users/", json=user_data)
        assert response.status_code == 200, response.text
        return response.json()
    return _create_user

@pytest.fixture
async def login_and_get_token(async_client, create_user):
    async def _login(email, password, role):
        # await create_user(email, password, role)
        data = {"username": email, "password": password}
        response = await async_client.post("/users/login", data=data)
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        return token
    return _login        
