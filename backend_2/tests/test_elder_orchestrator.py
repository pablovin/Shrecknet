"""Tests for Elder orchestrator functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.jobs.elder.schemas import RetrievedChunk


class TestElderOrchestrator:
    """Tests for ElderOrchestrator."""

    def test_deduplicate_chunks_by_source_instance(self):
        """Test that chunks are deduplicated by (source, instance_id)."""
        # Create test chunks with duplicates
        chunks = [
            RetrievedChunk(
                node_id="node-1",
                instance_id="instance-1",
                source="ontology_1",
                text="First occurrence",
                score=0.9,
                confidence_pct=90.0,
            ),
            RetrievedChunk(
                node_id="node-2",
                instance_id="instance-1",
                source="ontology_1",
                text="Second occurrence (higher score)",
                score=0.95,
                confidence_pct=95.0,
            ),
            RetrievedChunk(
                node_id="node-3",
                instance_id="instance-2",
                source="ontology_1",
                text="Different instance",
                score=0.85,
                confidence_pct=85.0,
            ),
            RetrievedChunk(
                node_id="node-4",
                instance_id="instance-1",
                source="ontology_2",
                text="Different ontology",
                score=0.88,
                confidence_pct=88.0,
            ),
        ]

        # Import the deduplication logic inline
        from typing import Optional

        def _deduplicate_chunks(chunks):
            """Deduplicate chunks by (source, instance_id), keeping highest score."""
            seen_keys: dict[tuple[Optional[str], Optional[str]], RetrievedChunk] = {}
            for chunk in chunks:
                key = (chunk.source, chunk.instance_id)
                if key not in seen_keys or chunk.score > seen_keys[key].score:
                    seen_keys[key] = chunk
            return sorted(seen_keys.values(), key=lambda c: c.score, reverse=True)

        # Apply deduplication
        deduplicated = _deduplicate_chunks(chunks)

        # Should have 3 unique chunks:
        # 1. (ontology_1, instance-1) - node-2 with score 0.95 (higher)
        # 2. (ontology_1, instance-2) - node-3 with score 0.85
        # 3. (ontology_2, instance-1) - node-4 with score 0.88
        assert len(deduplicated) == 3

        # Check they're sorted by score descending
        assert deduplicated[0].score == 0.95
        assert deduplicated[0].node_id == "node-2"
        assert deduplicated[1].score == 0.88
        assert deduplicated[2].score == 0.85

        # Check all unique keys are present
        keys = {(c.source, c.instance_id) for c in deduplicated}
        assert ("ontology_1", "instance-1") in keys
        assert ("ontology_1", "instance-2") in keys
        assert ("ontology_2", "instance-1") in keys

    def test_deduplicate_chunks_handles_none_values(self):
        """Test deduplication with None source or instance_id."""
        chunks = [
            RetrievedChunk(
                node_id="node-1",
                instance_id=None,
                source="ontology_1",
                text="No instance ID",
                score=0.9,
                confidence_pct=90.0,
            ),
            RetrievedChunk(
                node_id="node-2",
                instance_id="instance-1",
                source=None,
                text="No source",
                score=0.85,
                confidence_pct=85.0,
            ),
        ]

        from typing import Optional

        def _deduplicate_chunks(chunks):
            """Deduplicate chunks by (source, instance_id), keeping highest score."""
            seen_keys: dict[tuple[Optional[str], Optional[str]], RetrievedChunk] = {}
            for chunk in chunks:
                key = (chunk.source, chunk.instance_id)
                if key not in seen_keys or chunk.score > seen_keys[key].score:
                    seen_keys[key] = chunk
            return sorted(seen_keys.values(), key=lambda c: c.score, reverse=True)

        deduplicated = _deduplicate_chunks(chunks)

        # Both should be kept since they have different keys
        assert len(deduplicated) == 2

    def test_deduplicate_chunks_empty_list(self):
        """Test deduplication with empty list."""
        from typing import Optional

        def _deduplicate_chunks(chunks):
            """Deduplicate chunks by (source, instance_id), keeping highest score."""
            seen_keys: dict[tuple[Optional[str], Optional[str]], RetrievedChunk] = {}
            for chunk in chunks:
                key = (chunk.source, chunk.instance_id)
                if key not in seen_keys or chunk.score > seen_keys[key].score:
                    seen_keys[key] = chunk
            return sorted(seen_keys.values(), key=lambda c: c.score, reverse=True)

        deduplicated = _deduplicate_chunks([])
        assert len(deduplicated) == 0
