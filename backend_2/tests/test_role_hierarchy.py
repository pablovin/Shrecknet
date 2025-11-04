"""Tests for hierarchical role checking."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.api.deps import (
    require_roles,
    get_current_admin_user,
    get_current_active_admin_or_world_builder,
)
from app.core.roles import has_role, get_minimum_role, ROLE_HIERARCHY
from app.models.user import User, UserRole


class TestRoleHierarchyUtils:
    """Test the core role hierarchy utility functions."""

    def test_role_hierarchy_order(self):
        """Test that role hierarchy is defined correctly."""
        assert ROLE_HIERARCHY == [
            UserRole.PLAYER,
            UserRole.WRITER,
            UserRole.WORLD_BUILDER,
            UserRole.ADMIN,
        ]

    def test_has_role_same_level(self):
        """Test that a role satisfies itself."""
        assert has_role(UserRole.PLAYER, UserRole.PLAYER)
        assert has_role(UserRole.WRITER, UserRole.WRITER)
        assert has_role(UserRole.WORLD_BUILDER, UserRole.WORLD_BUILDER)
        assert has_role(UserRole.ADMIN, UserRole.ADMIN)

    def test_has_role_higher_satisfies_lower(self):
        """Test that higher roles satisfy lower role requirements."""
        # Admin can access everything
        assert has_role(UserRole.ADMIN, UserRole.PLAYER)
        assert has_role(UserRole.ADMIN, UserRole.WRITER)
        assert has_role(UserRole.ADMIN, UserRole.WORLD_BUILDER)

        # World Builder can access Writer and Player
        assert has_role(UserRole.WORLD_BUILDER, UserRole.PLAYER)
        assert has_role(UserRole.WORLD_BUILDER, UserRole.WRITER)

        # Writer can access Player
        assert has_role(UserRole.WRITER, UserRole.PLAYER)

    def test_has_role_lower_cannot_access_higher(self):
        """Test that lower roles cannot satisfy higher role requirements."""
        # Player cannot access higher roles
        assert not has_role(UserRole.PLAYER, UserRole.WRITER)
        assert not has_role(UserRole.PLAYER, UserRole.WORLD_BUILDER)
        assert not has_role(UserRole.PLAYER, UserRole.ADMIN)

        # Writer cannot access World Builder or Admin
        assert not has_role(UserRole.WRITER, UserRole.WORLD_BUILDER)
        assert not has_role(UserRole.WRITER, UserRole.ADMIN)

        # World Builder cannot access Admin
        assert not has_role(UserRole.WORLD_BUILDER, UserRole.ADMIN)

    def test_get_minimum_role_single(self):
        """Test getting minimum role from a single role."""
        assert get_minimum_role(UserRole.ADMIN) == UserRole.ADMIN
        assert get_minimum_role(UserRole.PLAYER) == UserRole.PLAYER

    def test_get_minimum_role_multiple(self):
        """Test getting minimum role from multiple roles."""
        assert get_minimum_role(UserRole.ADMIN, UserRole.PLAYER) == UserRole.PLAYER
        assert get_minimum_role(UserRole.WORLD_BUILDER, UserRole.WRITER) == UserRole.WRITER
        assert (
            get_minimum_role(UserRole.ADMIN, UserRole.WRITER, UserRole.WORLD_BUILDER)
            == UserRole.WRITER
        )

    def test_get_minimum_role_empty(self):
        """Test getting minimum role with no roles."""
        assert get_minimum_role() is None


class TestRequireRolesDependency:
    """Test the require_roles FastAPI dependency."""

    @pytest.mark.asyncio
    async def test_require_roles_player_access(self):
        """Test that Player can access Player-level endpoints."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.PLAYER

        dependency = require_roles(UserRole.PLAYER)
        result = await dependency(mock_user)

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_require_roles_writer_can_access_player_endpoint(self):
        """Test that Writer can access Player-level endpoints (hierarchy)."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.WRITER

        dependency = require_roles(UserRole.PLAYER)
        result = await dependency(mock_user)

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_require_roles_admin_can_access_all(self):
        """Test that Admin can access all role levels."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.ADMIN

        # Admin can access Player endpoints
        dependency = require_roles(UserRole.PLAYER)
        result = await dependency(mock_user)
        assert result == mock_user

        # Admin can access Writer endpoints
        dependency = require_roles(UserRole.WRITER)
        result = await dependency(mock_user)
        assert result == mock_user

        # Admin can access World Builder endpoints
        dependency = require_roles(UserRole.WORLD_BUILDER)
        result = await dependency(mock_user)
        assert result == mock_user

        # Admin can access Admin endpoints
        dependency = require_roles(UserRole.ADMIN)
        result = await dependency(mock_user)
        assert result == mock_user

    @pytest.mark.asyncio
    async def test_require_roles_player_cannot_access_writer(self):
        """Test that Player cannot access Writer-level endpoints."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.PLAYER

        dependency = require_roles(UserRole.WRITER)

        with pytest.raises(HTTPException) as exc_info:
            await dependency(mock_user)

        assert exc_info.value.status_code == 403
        assert "Insufficient permissions" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_require_roles_writer_cannot_access_admin(self):
        """Test that Writer cannot access Admin-level endpoints."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.WRITER

        dependency = require_roles(UserRole.ADMIN)

        with pytest.raises(HTTPException) as exc_info:
            await dependency(mock_user)

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_roles_multiple_roles_uses_minimum(self):
        """Test that when multiple roles are specified, minimum is used."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.WRITER

        # Writer should be able to access endpoint requiring Writer OR Admin
        # because minimum is Writer
        dependency = require_roles(UserRole.WRITER, UserRole.ADMIN)
        result = await dependency(mock_user)
        assert result == mock_user

        # World Builder should also be able to access
        mock_user.role = UserRole.WORLD_BUILDER
        result = await dependency(mock_user)
        assert result == mock_user

        # But Player should not
        mock_user.role = UserRole.PLAYER
        with pytest.raises(HTTPException) as exc_info:
            await dependency(mock_user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_roles_no_roles_allows_any_authenticated(self):
        """Test that require_roles() with no arguments allows any authenticated user."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.PLAYER

        dependency = require_roles()
        result = await dependency(mock_user)

        assert result == mock_user


class TestCurrentAdminUserDependency:
    """Test the get_current_admin_user dependency."""

    @pytest.mark.asyncio
    async def test_admin_user_can_access(self):
        """Test that Admin user can access admin-only endpoints."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.ADMIN

        result = await get_current_admin_user(mock_user)
        assert result == mock_user

    @pytest.mark.asyncio
    async def test_non_admin_cannot_access(self):
        """Test that non-admin users cannot access admin-only endpoints."""
        for role in [UserRole.PLAYER, UserRole.WRITER, UserRole.WORLD_BUILDER]:
            mock_user = MagicMock(spec=User)
            mock_user.role = role

            with pytest.raises(HTTPException) as exc_info:
                await get_current_admin_user(mock_user)

            assert exc_info.value.status_code == 403
            assert "Admin privileges required" in exc_info.value.detail


class TestAdminOrWorldBuilderDependency:
    """Test the get_current_active_admin_or_world_builder dependency."""

    @pytest.mark.asyncio
    async def test_admin_can_access(self):
        """Test that Admin can access admin/world-builder endpoints."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.ADMIN

        result = await get_current_active_admin_or_world_builder(mock_user)
        assert result == mock_user

    @pytest.mark.asyncio
    async def test_world_builder_can_access(self):
        """Test that World Builder can access admin/world-builder endpoints."""
        mock_user = MagicMock(spec=User)
        mock_user.role = UserRole.WORLD_BUILDER

        result = await get_current_active_admin_or_world_builder(mock_user)
        assert result == mock_user

    @pytest.mark.asyncio
    async def test_lower_roles_cannot_access(self):
        """Test that Writer and Player cannot access admin/world-builder endpoints."""
        for role in [UserRole.PLAYER, UserRole.WRITER]:
            mock_user = MagicMock(spec=User)
            mock_user.role = role

            with pytest.raises(HTTPException) as exc_info:
                await get_current_active_admin_or_world_builder(mock_user)

            assert exc_info.value.status_code == 403
            assert "Admin or world builder privileges required" in exc_info.value.detail
