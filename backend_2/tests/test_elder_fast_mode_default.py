"""Test that fast mode is the default for Elder queries."""

import pytest
from app.jobs.elder.schemas import ElderQueryRequest


class TestElderFastModeDefault:
    """Tests for Elder fast mode default behavior."""

    def test_fast_mode_is_default(self):
        """Test that fast mode is True by default."""
        # Create a minimal request without specifying fast
        request = ElderQueryRequest(query="What is the weather today?")

        # Verify fast mode is True by default
        assert request.fast is True, "Fast mode should be True by default"

    def test_fast_mode_can_be_overridden(self):
        """Test that fast mode can be explicitly set to False."""
        # Create a request with fast=False
        request = ElderQueryRequest(query="What is the weather today?", fast=False)

        # Verify fast mode is False when explicitly set
        assert request.fast is False, "Fast mode should be False when explicitly set"

    def test_fast_mode_explicit_true(self):
        """Test that fast mode can be explicitly set to True."""
        # Create a request with fast=True
        request = ElderQueryRequest(query="What is the weather today?", fast=True)

        # Verify fast mode is True when explicitly set
        assert request.fast is True, "Fast mode should be True when explicitly set"

    def test_request_with_all_defaults(self):
        """Test request creation with all default values."""
        request = ElderQueryRequest(query="Tell me about dragons")

        assert request.query == "Tell me about dragons"
        assert request.mode == "both"
        assert request.top_k is None
        assert request.include_trace is False
        assert request.fast is True  # This is the key assertion
        assert request.chat_id is None
        assert request.entities_hint is None
