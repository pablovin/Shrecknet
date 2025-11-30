"""Tests for helper utilities in architect_generation_v2."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.architect import (
    ArchitectProposalStatus,
    ArchitectProposalType,
)
from app.tasks.architect_generation_v2 import (
    _convert_validated_to_revised,
    _dedup_timeline_events,
    _group_events_by_entity,
    _normalize_timeline_event_entry,
)


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


def test_dedup_timeline_events_enforces_max_3_limit():
    """Ensure timeline events are limited to max 3 after deduplication."""
    # Create 5 distinct timeline events
    events = [
        {
            "title": f"Event {i}",
            "description": f"Description {i}",
            "order": i,
            "chunk_index": 0,
            "chunk_order": i,
            "temporal_hint": 0.0,
            "source_alias": None,
            "related_aliases": [],
        }
        for i in range(1, 6)
    ]
    
    result = _dedup_timeline_events(events)
    
    # Should be limited to max 3 events
    assert len(result) == 3
    # Should preserve the first 3 events (or most representative via clustering)
    assert all(event["title"] in [f"Event {i}" for i in range(1, 6)] for event in result)


def test_dedup_timeline_events_preserves_events_under_limit():
    """Ensure timeline events under the limit are preserved."""
    events = [
        {
            "title": "Event 1",
            "description": "Description 1",
            "order": 1,
            "chunk_index": 0,
            "chunk_order": 1,
            "temporal_hint": 0.0,
            "source_alias": None,
            "related_aliases": [],
        },
        {
            "title": "Event 2",
            "description": "Description 2",
            "order": 2,
            "chunk_index": 0,
            "chunk_order": 2,
            "temporal_hint": 0.0,
            "source_alias": None,
            "related_aliases": [],
        },
    ]
    
    result = _dedup_timeline_events(events)
    
    # Should preserve both events since under limit
    assert len(result) == 2
    assert result[0]["title"] == "Event 1"
    assert result[1]["title"] == "Event 2"


def test_dedup_timeline_events_removes_duplicates():
    """Ensure duplicate timeline events (same normalized title) are deduplicated."""
    events = [
        {
            "title": "Event One",
            "description": "First description",
            "order": 1,
            "chunk_index": 0,
            "chunk_order": 1,
            "temporal_hint": 0.0,
            "source_alias": None,
            "related_aliases": [],
        },
        {
            "title": "event one",  # Same title, different case
            "description": "Second description",
            "order": 2,
            "chunk_index": 1,
            "chunk_order": 2,
            "temporal_hint": 0.0,
            "source_alias": None,
            "related_aliases": [],
        },
        {
            "title": "Event Two",
            "description": "Third description",
            "order": 3,
            "chunk_index": 0,
            "chunk_order": 3,
            "temporal_hint": 0.0,
            "source_alias": None,
            "related_aliases": [],
        },
    ]
    
    result = _dedup_timeline_events(events)
    
    # Should only have 2 events (duplicates merged)
    assert len(result) == 2
    titles = [event["title"] for event in result]
    # Should keep one of the duplicate events
    assert any("event one" in title.lower() for title in titles)
    assert "Event Two" in titles


def test_normalize_timeline_event_entry_basic():
    """Test that timeline event normalization works correctly."""
    event = {
        "title": "Test Event",
        "description": "Test Description",
        "source_alias": "TestSource",
        "related_aliases": ["Alias1", "Alias2"],
        "order": 1,
    }
    
    result = _normalize_timeline_event_entry(event, chunk_index=0, fallback_order=1)
    
    assert result is not None
    assert result["title"] == "Test Event"
    assert result["description"] == "Test Description"
    assert result["source_alias"] == "TestSource"
    assert result["related_aliases"] == ["Alias1", "Alias2"]
    assert result["chunk_index"] == 0
    assert result["chunk_order"] == 1


def test_normalize_timeline_event_entry_rejects_invalid():
    """Test that invalid timeline events are rejected."""
    # Missing title
    event1 = {"description": "Test"}
    assert _normalize_timeline_event_entry(event1) is None
    
    # Missing description
    event2 = {"title": "Test"}
    assert _normalize_timeline_event_entry(event2) is None
    
    # Empty strings
    event3 = {"title": "", "description": ""}
    assert _normalize_timeline_event_entry(event3) is None


def test_group_events_by_entity_tracks_source_entity_id():
    """Timeline grouping should use the source entity identifier when present."""
    events = [
        {
            "timeline_event_id": "evt-1",
            "source_entity_id": "entity-a",
            "created_from_entity_id": None,
            "related_entity_ids": [],
            "related_instance_ids": [],
        }
    ]

    grouped = _group_events_by_entity(events)

    assert "entity-a" in grouped
    assert grouped["entity-a"][0]["timeline_event_id"] == "evt-1"


def test_group_events_by_entity_deduplicates_identifiers():
    """Ensure grouping only records an event once per entity even if repeated."""
    events = [
        {
            "timeline_event_id": "evt-9",
            "source_entity_id": "entity-b",
            "created_from_entity_id": "entity-b",
            "related_entity_ids": ["entity-b", "entity-c"],
            "related_instance_ids": ["entity-b"],
        }
    ]

    grouped = _group_events_by_entity(events)

    assert len(grouped["entity-b"]) == 1
    assert grouped["entity-b"][0]["timeline_event_id"] == "evt-9"
    assert grouped["entity-c"][0]["timeline_event_id"] == "evt-9"
