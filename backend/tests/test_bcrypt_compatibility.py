"""Test bcrypt password compatibility for imported passwords from old database."""

from __future__ import annotations

import pytest
from passlib.context import CryptContext

from app.core.security import verify_password, get_password_hash


def test_bcrypt_password_verification():
    """Test that bcrypt hashes from old database can be verified."""
    # Create a bcrypt context to simulate old database hash
    bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    # Simulate a bcrypt hash from the old database
    bcrypt_hash = bcrypt_context.hash("password123")

    # Verify that the new security module can verify bcrypt hashes
    assert verify_password("password123", bcrypt_hash) is True
    assert verify_password("wrongpassword", bcrypt_hash) is False


def test_argon2_password_hashing():
    """Test that new passwords use argon2 hashing."""
    # Hash a new password
    hashed = get_password_hash("newpassword")

    # Verify it uses argon2 (the preferred scheme)
    assert hashed.startswith("$argon2")

    # Verify the password can be verified
    assert verify_password("newpassword", hashed) is True
    assert verify_password("wrongpassword", hashed) is False


def test_both_schemes_work_together():
    """Test that both bcrypt and argon2 passwords work in the same system."""
    # Create hashes with both schemes
    bcrypt_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    bcrypt_hash = bcrypt_context.hash("oldpassword")
    argon2_hash = get_password_hash("newpassword")

    # Verify both work correctly
    assert verify_password("oldpassword", bcrypt_hash) is True
    assert verify_password("newpassword", argon2_hash) is True

    # Verify wrong passwords fail for both
    assert verify_password("wrongpassword", bcrypt_hash) is False
    assert verify_password("wrongpassword", argon2_hash) is False
