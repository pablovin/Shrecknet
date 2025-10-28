"""
Integration test for import endpoints.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker

from app.models.user import User, UserRole
from app.models.game import Game, GameSession
from app.core.security import create_access_token


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine):
    """Create a database session for testing."""
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def admin_token(db_session: AsyncSession) -> str:
    """Create an admin user and return auth token."""
    from app.services.user_service import UserService
    
    service = UserService(db_session)
    admin_user = await service.register_user({
        "username": "admin",
        "email": "admin@import-test.com",
        "password": "adminpass123",
        "full_name": "Admin User",
        "timezone": "UTC",
        "role": UserRole.ADMIN,
    })
    
    return create_access_token(subject=str(admin_user.id))


@pytest.mark.asyncio
async def test_import_users(client: AsyncClient, db_session: AsyncSession, admin_token: str):
    """Test importing users from old database."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    response = await client.post("/imports/users", headers=headers)
    
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "User import completed"
    assert data["imported"] > 0
    print(f"✓ Imported {data['imported']} users")
    print(f"  Skipped: {data['skipped']}, Errors: {data['errors']}")


@pytest.mark.asyncio
async def test_import_game_tables(client: AsyncClient, db_session: AsyncSession, admin_token: str):
    """Test importing game tables from old database."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # First import users to ensure members exist
    await client.post("/imports/users", headers=headers)
    
    response = await client.post("/imports/game-tables", headers=headers)
    
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Game table import completed"
    assert data["imported"] > 0
    print(f"✓ Imported {data['imported']} game tables")
    print(f"  Skipped: {data['skipped']}, Errors: {data['errors']}")


@pytest.mark.asyncio
async def test_import_sessions(client: AsyncClient, db_session: AsyncSession, admin_token: str):
    """Test importing sessions from old database."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # First import users and games
    await client.post("/imports/users", headers=headers)
    await client.post("/imports/game-tables", headers=headers)
    
    response = await client.post("/imports/sessions", headers=headers)
    
    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Session import completed"
    assert data["imported"] > 0
    print(f"✓ Imported {data['imported']} sessions")
    print(f"  Skipped: {data['skipped']}, Errors: {data['errors']}")


@pytest.mark.asyncio
async def test_import_full_workflow(client: AsyncClient, db_session: AsyncSession, admin_token: str):
    """Test the complete import workflow."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    print("\n=== Full Import Workflow ===")
    
    # Import users
    response = await client.post("/imports/users", headers=headers)
    assert response.status_code == 201
    users_data = response.json()
    print(f"✓ Users: {users_data['imported']} imported, {users_data['skipped']} skipped, {users_data['errors']} errors")
    
    # Import game tables
    response = await client.post("/imports/game-tables", headers=headers)
    assert response.status_code == 201
    games_data = response.json()
    print(f"✓ Games: {games_data['imported']} imported, {games_data['skipped']} skipped, {games_data['errors']} errors")
    
    # Import sessions
    response = await client.post("/imports/sessions", headers=headers)
    assert response.status_code == 201
    sessions_data = response.json()
    print(f"✓ Sessions: {sessions_data['imported']} imported, {sessions_data['skipped']} skipped, {sessions_data['errors']} errors")
    
    # Verify all data
    from sqlalchemy import select, func
    
    user_count = await db_session.execute(select(func.count(User.id)))
    game_count = await db_session.execute(select(func.count(Game.id)))
    session_count = await db_session.execute(select(func.count(GameSession.id)))
    
    print(f"\n✓ Total in new database:")
    print(f"  Users: {user_count.scalar()}")
    print(f"  Games: {game_count.scalar()}")
    print(f"  Sessions: {session_count.scalar()}")


@pytest.mark.asyncio
async def test_import_idempotency(client: AsyncClient, db_session: AsyncSession, admin_token: str):
    """Test that imports are idempotent (can be run multiple times safely)."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # First import
    response1 = await client.post("/imports/users", headers=headers)
    assert response1.status_code == 201
    data1 = response1.json()
    
    # Second import (should skip already imported users)
    response2 = await client.post("/imports/users", headers=headers)
    assert response2.status_code == 201
    data2 = response2.json()
    
    assert data2["imported"] == 0, "Second import should not import any new users"
    assert data2["skipped"] == data1["imported"], "Second import should skip all previously imported users"
    print(f"✓ Idempotency test passed: {data2['skipped']} users skipped on second import")

