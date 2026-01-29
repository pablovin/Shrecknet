"""Test that PDF embedding accepts valid AuthorType values."""

from __future__ import annotations

import pytest

from app.models.background_job import AuthorType


def test_author_type_enum_values():
    """Test that AuthorType enum has correct values."""
    assert AuthorType.USER.value == "user"
    assert AuthorType.AGENT.value == "agent"


def test_author_type_from_string():
    """Test that AuthorType can be created from string values."""
    # These should work
    assert AuthorType("user") == AuthorType.USER
    assert AuthorType("agent") == AuthorType.AGENT


def test_author_type_invalid_value():
    """Test that invalid AuthorType values raise ValueError."""
    with pytest.raises(ValueError, match="'system' is not a valid AuthorType"):
        AuthorType("system")


def test_author_type_valid_for_auto_embed():
    """Test that 'agent' is a valid author_type for auto-embed functionality."""
    # This is the value we use in library_service.py when auto_embed=True
    author_type = "agent"
    # Should not raise
    result = AuthorType(author_type)
    assert result == AuthorType.AGENT
