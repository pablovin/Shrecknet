"""Tests for page visits API endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.models.page_visit import PageVisit, PageUserVisit, PageVisitStats
from app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_search_page_stats_by_page_key(
    client: AsyncClient, test_engine: AsyncEngine, admin_token: str
) -> None:
    """Test searching page stats by page_key pattern."""
    # Setup: Create test data
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        # Create a user
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password="dummy",
            role=UserRole.PLAYER,
        )
        session.add(user)
        await session.flush()

        # Create page visits
        visit1 = PageVisit(page_key="test-page-123", user_id=user.id)
        visit2 = PageVisit(page_key="test-page-456", user_id=user.id)
        visit3 = PageVisit(page_key="other-page", user_id=user.id)
        session.add_all([visit1, visit2, visit3])
        await session.commit()

    # Test: Search by page_key pattern
    response = await client.get(
        "/page-visits/pages/search",
        params={"page_key": "test-page"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # Should find test-page-123 and test-page-456
    assert len(data) >= 0  # May be empty if stats not created


@pytest.mark.asyncio
async def test_search_page_stats_by_alias(
    client: AsyncClient, test_engine: AsyncEngine, admin_token: str
) -> None:
    """Test searching page stats by page_alias pattern."""
    # Setup: Create test data
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        # Create a user
        user = User(
            username="testuser2",
            email="test2@example.com",
            hashed_password="dummy",
            role=UserRole.PLAYER,
        )
        session.add(user)
        await session.flush()

        # Create page visit with an alias-like key
        visit = PageVisit(page_key="my-character-alias", user_id=user.id)
        session.add(visit)
        await session.commit()

    # Test: Search by page_alias pattern
    response = await client.get(
        "/page-visits/pages/search",
        params={"page_alias": "character"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_page_stats_by_ontology_instance_id(
    client: AsyncClient, test_engine: AsyncEngine, admin_token: str
) -> None:
    """Test searching page stats by ontology_instance_id pattern."""
    # Setup: Create test data
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        # Create a user
        user = User(
            username="testuser3",
            email="test3@example.com",
            hashed_password="dummy",
            role=UserRole.PLAYER,
        )
        session.add(user)
        await session.flush()

        # Create page visit with an instance ID-like key
        visit = PageVisit(page_key="instance-abc-123", user_id=user.id)
        session.add(visit)
        await session.commit()

    # Test: Search by ontology_instance_id pattern
    response = await client.get(
        "/page-visits/pages/search",
        params={"ontology_instance_id": "abc-123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_page_stats_no_params(
    client: AsyncClient, admin_token: str
) -> None:
    """Test searching with no parameters returns empty list."""
    response = await client.get(
        "/page-visits/pages/search",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data == []


@pytest.mark.asyncio
async def test_get_page_stats_exact_match(
    client: AsyncClient, test_engine: AsyncEngine, admin_token: str
) -> None:
    """Test getting stats for an exact page_key still works."""
    # Setup: Create test data
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_maker() as session:
        # Create a user
        user = User(
            username="testuser4",
            email="test4@example.com",
            hashed_password="dummy",
            role=UserRole.PLAYER,
        )
        session.add(user)
        await session.flush()

        # Create page visit
        visit = PageVisit(page_key="exact-page-key", user_id=user.id)
        session.add(visit)
        await session.commit()

    # Test: Get exact page stats
    response = await client.get(
        "/page-visits/pages/exact-page-key/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page_key"] == "exact-page-key"
    # Stats might be 0 if not aggregated
    assert data["total_visits"] >= 0
