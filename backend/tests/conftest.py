import os
import pytest
from httpx import AsyncClient
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

import asyncio
import pytest_asyncio
from httpx import AsyncClient
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("USE_DUMMY_EMBEDDINGS", "true")
# Configure chat history location for tests
from pathlib import Path
from app.config import settings

test_chat_dir = Path.cwd() / "test_chat_history"
settings.chat_history_dir = str(test_chat_dir / "{user_id}")

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

@pytest.fixture
async def async_session(test_engine):
    async_session_maker = sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session

@pytest.fixture
async def session():
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def session(test_engine):
    async_session_maker = sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        yield session

@pytest.fixture(autouse=True)
def override_get_session(monkeypatch, async_session):
    async def _override():
        yield async_session
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
    async def _create_user(email=None, password=None, role=None, **kwargs):
        if email is None:
            email = kwargs.get("email")
        if password is None:
            password = kwargs.get("password")
        if role is None:
            role = kwargs.get("role")
        user_data = {
            "nickname": kwargs.get("nickname", email.split('@')[0]),
            "email": email,
            "password": password,
            "role": role,
            "image_url": kwargs.get("image_url", "http://test"),
        }

        print (f"User created: {email}")
        response = await async_client.post("/user/", json=user_data)
        if response.status_code == 400 and "already registered" in response.text:
            # User already exists from another test - fetch token instead
            login_resp = await async_client.post(
                "/user/login",
                data={"username": email, "password": password},
            )
            assert login_resp.status_code == 200, login_resp.text
            return login_resp.json()
        assert response.status_code == 200, response.text
        return response.json()
    return _create_user

@pytest.fixture
async def login_and_get_token(async_client, create_user):
    async def _login(email, password, role):
        # await create_user(email, password, role)
        data = {"username": email, "password": password}
        response = await async_client.post("/user/login", data=data)
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        return token
    return _login        