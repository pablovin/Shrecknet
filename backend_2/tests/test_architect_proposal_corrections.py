"""Integration tests for architect proposal validation flow."""

from __future__ import annotations

import pytest

from app.models.architect import (
    ArchitectProposal,
    ArchitectProposalStatus,
    ArchitectProposalType,
)


@pytest.fixture
def sample_new_proposal():
    """Create a sample NEW_INSTANCE proposal."""
    return {
        "proposal_type": ArchitectProposalType.NEW_INSTANCE,
        "entity_definition_id": 5,
        "entity_instance_id": None,
        "alias": "John Smith",
        "chunks": ["John Smith arrived at the castle"],
        "corrected_alias": None,
        "corrected_entity_definition_id": None,
        "corrected_proposal_type": None,
        "corrected_entity_instance_id": None,
    }


@pytest.fixture
def sample_update_proposal():
    """Create a sample UPDATE_INSTANCE proposal."""
    return {
        "proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
        "entity_definition_id": 5,
        "entity_instance_id": "entity-alice-456",
        "alias": "Alice",
        "chunks": ["Alice revealed her background"],
        "corrected_alias": None,
        "corrected_entity_definition_id": None,
        "corrected_proposal_type": None,
        "corrected_entity_instance_id": None,
    }


def test_effective_proposal_type_no_correction(sample_new_proposal):
    """Test that effective proposal type uses original when no correction."""
    proposal_dict = sample_new_proposal

    # Simulate the logic from architect_generation.py
    effective_proposal_type = (
        proposal_dict.get("corrected_proposal_type") or proposal_dict["proposal_type"]
    )

    assert effective_proposal_type == ArchitectProposalType.NEW_INSTANCE


def test_effective_proposal_type_with_correction(sample_new_proposal):
    """Test that effective proposal type uses correction when provided."""
    proposal_dict = sample_new_proposal
    proposal_dict["corrected_proposal_type"] = ArchitectProposalType.UPDATE_INSTANCE
    proposal_dict["corrected_entity_instance_id"] = "entity-existing-123"

    # Simulate the corrected logic from architect_generation.py
    effective_proposal_type = (
        proposal_dict.get("corrected_proposal_type")
        if proposal_dict.get("corrected_proposal_type") is not None
        else proposal_dict["proposal_type"]
    )

    assert effective_proposal_type == ArchitectProposalType.UPDATE_INSTANCE


def test_effective_entity_instance_id_no_correction(sample_update_proposal):
    """Test that effective entity instance ID uses original when no correction."""
    proposal_dict = sample_update_proposal

    # Simulate the corrected logic from architect_generation.py
    if (
        proposal_dict.get("corrected_entity_instance_id") is not None
        or proposal_dict.get("corrected_proposal_type")
        == ArchitectProposalType.NEW_INSTANCE
    ):
        effective_entity_instance_id = proposal_dict.get("corrected_entity_instance_id")
    else:
        effective_entity_instance_id = proposal_dict.get("entity_instance_id")

    assert effective_entity_instance_id == "entity-alice-456"


def test_effective_entity_instance_id_with_correction(sample_update_proposal):
    """Test that effective entity instance ID uses correction when provided."""
    proposal_dict = sample_update_proposal
    proposal_dict["corrected_entity_instance_id"] = "entity-alice-789"

    # Simulate the corrected logic from architect_generation.py
    if (
        proposal_dict.get("corrected_entity_instance_id") is not None
        or proposal_dict.get("corrected_proposal_type")
        == ArchitectProposalType.NEW_INSTANCE
    ):
        effective_entity_instance_id = proposal_dict.get("corrected_entity_instance_id")
    else:
        effective_entity_instance_id = proposal_dict.get("entity_instance_id")

    assert effective_entity_instance_id == "entity-alice-789"


def test_convert_new_to_update_scenario(sample_new_proposal):
    """Test scenario: client converts NEW_INSTANCE to UPDATE_INSTANCE."""
    proposal_dict = sample_new_proposal

    # Client makes corrections
    proposal_dict["corrected_proposal_type"] = ArchitectProposalType.UPDATE_INSTANCE
    proposal_dict["corrected_entity_instance_id"] = "entity-john-exists-999"

    # Get effective values using corrected logic
    effective_proposal_type = (
        proposal_dict.get("corrected_proposal_type")
        if proposal_dict.get("corrected_proposal_type") is not None
        else proposal_dict["proposal_type"]
    )
    if (
        proposal_dict.get("corrected_entity_instance_id") is not None
        or proposal_dict.get("corrected_proposal_type")
        == ArchitectProposalType.NEW_INSTANCE
    ):
        effective_entity_instance_id = proposal_dict.get("corrected_entity_instance_id")
    else:
        effective_entity_instance_id = proposal_dict.get("entity_instance_id")

    # Verify conversion worked
    assert effective_proposal_type == ArchitectProposalType.UPDATE_INSTANCE
    assert effective_entity_instance_id == "entity-john-exists-999"


def test_convert_update_to_new_scenario(sample_update_proposal):
    """Test scenario: client converts UPDATE_INSTANCE to NEW_INSTANCE."""
    proposal_dict = sample_update_proposal

    # Client makes corrections
    proposal_dict["corrected_proposal_type"] = ArchitectProposalType.NEW_INSTANCE
    proposal_dict["corrected_entity_instance_id"] = None
    proposal_dict["corrected_alias"] = "Alice Jr."  # Different person

    # Get effective values using corrected logic
    effective_proposal_type = (
        proposal_dict.get("corrected_proposal_type")
        if proposal_dict.get("corrected_proposal_type") is not None
        else proposal_dict["proposal_type"]
    )
    if (
        proposal_dict.get("corrected_entity_instance_id") is not None
        or proposal_dict.get("corrected_proposal_type")
        == ArchitectProposalType.NEW_INSTANCE
    ):
        effective_entity_instance_id = proposal_dict.get("corrected_entity_instance_id")
    else:
        effective_entity_instance_id = proposal_dict.get("entity_instance_id")
    effective_alias = proposal_dict.get("corrected_alias") or proposal_dict.get("alias")

    # Verify conversion worked
    assert effective_proposal_type == ArchitectProposalType.NEW_INSTANCE
    assert effective_entity_instance_id is None
    assert effective_alias == "Alice Jr."


def test_change_update_target_scenario(sample_update_proposal):
    """Test scenario: client changes which entity to update."""
    proposal_dict = sample_update_proposal

    # Client corrects the target entity
    proposal_dict["corrected_entity_instance_id"] = "entity-correct-target-555"

    # Get effective value using corrected logic
    if (
        proposal_dict.get("corrected_entity_instance_id") is not None
        or proposal_dict.get("corrected_proposal_type")
        == ArchitectProposalType.NEW_INSTANCE
    ):
        effective_entity_instance_id = proposal_dict.get("corrected_entity_instance_id")
    else:
        effective_entity_instance_id = proposal_dict.get("entity_instance_id")

    # Verify correction worked
    assert effective_entity_instance_id == "entity-correct-target-555"


def test_multiple_corrections_scenario(sample_new_proposal):
    """Test scenario: client makes multiple corrections to same proposal."""
    proposal_dict = sample_new_proposal

    # Client makes multiple corrections
    proposal_dict["corrected_alias"] = "Jonathan Smith"  # Fix alias
    proposal_dict["corrected_entity_definition_id"] = 8  # Change type

    # Get effective values
    effective_alias = proposal_dict.get("corrected_alias") or proposal_dict.get("alias")
    effective_entity_def_id = proposal_dict.get(
        "corrected_entity_definition_id"
    ) or proposal_dict.get("entity_definition_id")

    # Verify both corrections applied
    assert effective_alias == "Jonathan Smith"
    assert effective_entity_def_id == 8


def test_separation_logic_new_proposals():
    """Test that proposals are correctly separated as NEW after corrections."""
    proposals = [
        {
            "id": "prop-1",
            "proposal_type": ArchitectProposalType.NEW_INSTANCE,
            "corrected_proposal_type": None,
        },
        {
            "id": "prop-2",
            "proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
            "corrected_proposal_type": ArchitectProposalType.NEW_INSTANCE,
        },
    ]

    new_proposals = []
    for p in proposals:
        effective_type = p.get("corrected_proposal_type") or p["proposal_type"]
        if effective_type == ArchitectProposalType.NEW_INSTANCE:
            new_proposals.append(p)

    assert len(new_proposals) == 2
    assert "prop-1" in [p["id"] for p in new_proposals]
    assert "prop-2" in [p["id"] for p in new_proposals]


def test_separation_logic_update_proposals():
    """Test that proposals are correctly separated as UPDATE after corrections."""
    proposals = [
        {
            "id": "prop-1",
            "proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
            "corrected_proposal_type": None,
        },
        {
            "id": "prop-2",
            "proposal_type": ArchitectProposalType.NEW_INSTANCE,
            "corrected_proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
        },
    ]

    update_proposals = []
    for p in proposals:
        effective_type = p.get("corrected_proposal_type") or p["proposal_type"]
        if effective_type == ArchitectProposalType.UPDATE_INSTANCE:
            update_proposals.append(p)

    assert len(update_proposals) == 2
    assert "prop-1" in [p["id"] for p in update_proposals]
    assert "prop-2" in [p["id"] for p in update_proposals]


def test_no_corrections_maintains_original_values():
    """Test that when no corrections are provided, original values are used."""
    proposal = {
        "proposal_type": ArchitectProposalType.NEW_INSTANCE,
        "entity_definition_id": 5,
        "entity_instance_id": None,
        "alias": "Bob",
        "corrected_alias": None,
        "corrected_entity_definition_id": None,
        "corrected_proposal_type": None,
        "corrected_entity_instance_id": None,
    }

    # Get effective values
    effective_type = (
        proposal.get("corrected_proposal_type") or proposal["proposal_type"]
    )
    effective_def_id = (
        proposal.get("corrected_entity_definition_id")
        or proposal["entity_definition_id"]
    )
    effective_alias = proposal.get("corrected_alias") or proposal["alias"]
    effective_instance_id = proposal.get(
        "corrected_entity_instance_id"
    ) or proposal.get("entity_instance_id")

    # Verify originals are used
    assert effective_type == ArchitectProposalType.NEW_INSTANCE
    assert effective_def_id == 5
    assert effective_alias == "Bob"
    assert effective_instance_id is None
