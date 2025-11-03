"""Unit tests for authentication logic without full app dependencies."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.user_service import UserService
from app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_authenticate_user_by_username():
    """Test authentication with username works."""
    # Mock session and repository
    mock_session = AsyncMock()
    service = UserService(mock_session)

    # Mock user
    mock_user = MagicMock(spec=User)
    mock_user.id = 1
    mock_user.username = "testuser"
    mock_user.email = "test@example.com"
    mock_user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$valid_hash_here"

    # Mock repository methods
    service.repository.get_by_username = AsyncMock(return_value=mock_user)
    service.repository.get_by_email = AsyncMock(return_value=None)

    # Mock password verification using patch
    with patch("app.services.user_service.verify_password", return_value=True):
        # Authenticate with username
        result = await service.authenticate_user("testuser", "password123")

    # Verify username lookup was called
    service.repository.get_by_username.assert_called_once_with("testuser")
    # Email lookup should not be called since username found
    service.repository.get_by_email.assert_not_called()

    # Verify result is the user
    assert result == mock_user


@pytest.mark.asyncio
async def test_authenticate_user_by_email():
    """Test authentication with email works."""
    # Mock session and repository
    mock_session = AsyncMock()
    service = UserService(mock_session)

    # Mock user
    mock_user = MagicMock(spec=User)
    mock_user.id = 1
    mock_user.username = "testuser"
    mock_user.email = "test@example.com"
    mock_user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$valid_hash_here"

    # Mock repository methods - username not found, email found
    service.repository.get_by_username = AsyncMock(return_value=None)
    service.repository.get_by_email = AsyncMock(return_value=mock_user)

    # Mock password verification using patch
    with patch("app.services.user_service.verify_password", return_value=True):
        # Authenticate with email
        result = await service.authenticate_user("test@example.com", "password123")

    # Verify username lookup was called first
    service.repository.get_by_username.assert_called_once_with("test@example.com")
    # Email lookup should be called since username not found
    service.repository.get_by_email.assert_called_once_with("test@example.com")

    # Verify result is the user
    assert result == mock_user


@pytest.mark.asyncio
async def test_authenticate_user_not_found():
    """Test authentication fails when user not found."""
    # Mock session and repository
    mock_session = AsyncMock()
    service = UserService(mock_session)

    # Mock repository methods - no user found
    service.repository.get_by_username = AsyncMock(return_value=None)
    service.repository.get_by_email = AsyncMock(return_value=None)

    # Authenticate with non-existent user
    result = await service.authenticate_user("nonexistent", "password123")

    # Verify both lookups were called
    service.repository.get_by_username.assert_called_once_with("nonexistent")
    service.repository.get_by_email.assert_called_once_with("nonexistent")

    # Verify result is None
    assert result is None


@pytest.mark.asyncio
async def test_authenticate_user_wrong_password():
    """Test authentication fails with wrong password."""
    # Mock session and repository
    mock_session = AsyncMock()
    service = UserService(mock_session)

    # Mock user
    mock_user = MagicMock(spec=User)
    mock_user.id = 1
    mock_user.username = "testuser"
    mock_user.email = "test@example.com"
    mock_user.hashed_password = "$argon2id$v=19$m=65536,t=3,p=4$valid_hash_here"

    # Mock repository methods
    service.repository.get_by_username = AsyncMock(return_value=mock_user)
    service.repository.get_by_email = AsyncMock(return_value=None)

    # Mock password verification to fail using patch
    with patch("app.services.user_service.verify_password", return_value=False):
        # Authenticate with wrong password
        result = await service.authenticate_user("testuser", "wrongpassword")

    # Verify user was found
    service.repository.get_by_username.assert_called_once_with("testuser")

    # Verify result is None
    assert result is None
