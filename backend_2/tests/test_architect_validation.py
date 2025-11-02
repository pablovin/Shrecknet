"""Tests for architect proposal validation and correction features."""

from __future__ import annotations

import pytest

from app.models.architect import ArchitectProposalStatus, ArchitectProposalType
from app.schemas.architect import ValidatedProposalItem


def test_validated_proposal_item_basic():
    """Test basic validation of proposal items."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
    )
    
    assert proposal.proposal_id == "prop-123"
    assert proposal.status == ArchitectProposalStatus.APPROVED
    assert proposal.corrected_alias is None
    assert proposal.corrected_entity_definition_id is None
    assert proposal.corrected_proposal_type is None
    assert proposal.corrected_entity_instance_id is None
    assert proposal.merged_into_proposal_id is None


def test_validated_proposal_item_with_alias_correction():
    """Test proposal with corrected alias."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_alias="John Smith",
    )
    
    assert proposal.corrected_alias == "John Smith"


def test_validated_proposal_item_with_entity_type_correction():
    """Test proposal with corrected entity definition."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_entity_definition_id=42,
    )
    
    assert proposal.corrected_entity_definition_id == 42


def test_validated_proposal_item_convert_new_to_update():
    """Test converting NEW_INSTANCE to UPDATE_INSTANCE."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_proposal_type=ArchitectProposalType.UPDATE_INSTANCE,
        corrected_entity_instance_id="entity-existing-456",
    )
    
    assert proposal.corrected_proposal_type == ArchitectProposalType.UPDATE_INSTANCE
    assert proposal.corrected_entity_instance_id == "entity-existing-456"


def test_validated_proposal_item_convert_update_to_new():
    """Test converting UPDATE_INSTANCE to NEW_INSTANCE."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_proposal_type=ArchitectProposalType.NEW_INSTANCE,
        corrected_entity_instance_id=None,
    )
    
    assert proposal.corrected_proposal_type == ArchitectProposalType.NEW_INSTANCE
    assert proposal.corrected_entity_instance_id is None


def test_validated_proposal_item_change_update_target():
    """Test changing which entity to update."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_entity_instance_id="entity-different-789",
    )
    
    assert proposal.corrected_entity_instance_id == "entity-different-789"


def test_validated_proposal_item_merge():
    """Test merging proposals."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.MERGED,
        merged_into_proposal_id="prop-main",
    )
    
    assert proposal.status == ArchitectProposalStatus.MERGED
    assert proposal.merged_into_proposal_id == "prop-main"


def test_validated_proposal_item_reject():
    """Test rejecting proposals."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.REJECTED,
    )
    
    assert proposal.status == ArchitectProposalStatus.REJECTED


def test_validated_proposal_item_all_corrections():
    """Test proposal with all possible corrections."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_alias="Corrected Name",
        corrected_entity_definition_id=99,
        corrected_proposal_type=ArchitectProposalType.UPDATE_INSTANCE,
        corrected_entity_instance_id="entity-corrected-555",
        merged_into_proposal_id=None,
    )
    
    assert proposal.corrected_alias == "Corrected Name"
    assert proposal.corrected_entity_definition_id == 99
    assert proposal.corrected_proposal_type == ArchitectProposalType.UPDATE_INSTANCE
    assert proposal.corrected_entity_instance_id == "entity-corrected-555"


def test_proposal_type_enum_values():
    """Test that ArchitectProposalType has expected values."""
    assert ArchitectProposalType.NEW_INSTANCE.value == "new_instance"
    assert ArchitectProposalType.UPDATE_INSTANCE.value == "update_instance"


def test_proposal_status_enum_values():
    """Test that ArchitectProposalStatus has all expected values."""
    assert ArchitectProposalStatus.PENDING.value == "pending"
    assert ArchitectProposalStatus.APPROVED.value == "approved"
    assert ArchitectProposalStatus.REJECTED.value == "rejected"
    assert ArchitectProposalStatus.MERGED.value == "merged"


def test_validated_proposal_serialization():
    """Test that ValidatedProposalItem serializes correctly."""
    proposal = ValidatedProposalItem(
        proposal_id="prop-123",
        status=ArchitectProposalStatus.APPROVED,
        corrected_alias="John",
        corrected_entity_definition_id=5,
        corrected_proposal_type=ArchitectProposalType.NEW_INSTANCE,
        corrected_entity_instance_id=None,
        merged_into_proposal_id=None,
    )
    
    data = proposal.model_dump()
    
    assert data["proposal_id"] == "prop-123"
    assert data["status"] == ArchitectProposalStatus.APPROVED
    assert data["corrected_alias"] == "John"
    assert data["corrected_entity_definition_id"] == 5
    assert data["corrected_proposal_type"] == ArchitectProposalType.NEW_INSTANCE
    assert data["corrected_entity_instance_id"] is None
    assert data["merged_into_proposal_id"] is None


def test_validated_proposal_deserialization():
    """Test that ValidatedProposalItem deserializes correctly."""
    data = {
        "proposal_id": "prop-456",
        "status": "approved",
        "corrected_alias": "Alice",
        "corrected_entity_definition_id": 10,
        "corrected_proposal_type": "update_instance",
        "corrected_entity_instance_id": "entity-alice-123",
        "merged_into_proposal_id": None,
    }
    
    proposal = ValidatedProposalItem(**data)
    
    assert proposal.proposal_id == "prop-456"
    assert proposal.status == ArchitectProposalStatus.APPROVED
    assert proposal.corrected_alias == "Alice"
    assert proposal.corrected_entity_definition_id == 10
    assert proposal.corrected_proposal_type == ArchitectProposalType.UPDATE_INSTANCE
    assert proposal.corrected_entity_instance_id == "entity-alice-123"
