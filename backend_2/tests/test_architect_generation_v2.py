"""Tests for helper utilities in architect_generation_v2."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.architect import (
    ArchitectProposalStatus,
    ArchitectProposalType,
)
from app.tasks.architect_generation_v2 import _convert_validated_to_revised


def _make_base_proposal(**overrides):
    base = {
        "proposal_type": ArchitectProposalType.NEW_INSTANCE,
        "entity_definition_id": 1,
        "entity_instance_id": None,
        "alias": "Original Alias",
        "corrected_alias": None,
        "corrected_entity_definition_id": None,
        "corrected_proposal_type": None,
        "corrected_entity_instance_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_convert_validated_respects_corrected_proposal_type():
    """Ensure we honour the client's conversion from NEW -> UPDATE."""
    base = _make_base_proposal()
    validated = [
        {
            "proposal_id": "prop-1",
            "status": ArchitectProposalStatus.APPROVED,
            "corrected_proposal_type": ArchitectProposalType.UPDATE_INSTANCE,
            "corrected_entity_instance_id": "entity-123",
        }
    ]

    result = _convert_validated_to_revised(validated, {"prop-1": base})

    assert result == [
        {
            "suggestion_id": "prop-1",
            "action": "updated",
            "alias": "Original Alias",
            "entity_definition_id": 1,
            "entity_instance_id": "entity-123",
            "merged_suggestion_ids": None,
        }
    ]


def test_convert_validated_uses_stored_corrections_when_missing():
    """Ensure stored corrections (DB) are applied even if payload omits them."""
    base = _make_base_proposal(
        corrected_proposal_type=ArchitectProposalType.UPDATE_INSTANCE,
        corrected_entity_instance_id="entity-db-456",
    )
    validated = [
        {
            "proposal_id": "prop-1",
            "status": ArchitectProposalStatus.APPROVED,
        }
    ]

    result = _convert_validated_to_revised(validated, {"prop-1": base})

    assert result[0]["action"] == "updated"
    assert result[0]["entity_instance_id"] == "entity-db-456"
