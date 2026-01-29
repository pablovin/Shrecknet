"""Tests for Neo4j graph retriever."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.integrations.retrieval.neo4j_retriever import Neo4jGraphRetriever
from app.jobs.elder.schemas import RetrievedChunk


class TestNeo4jGraphRetriever:
    """Tests for Neo4jGraphRetriever."""

    @pytest.mark.asyncio
    async def test_search_with_empty_results(self):
        """Test that search handles empty results without error."""
        # Create a mock graph session
        mock_graph_session = AsyncMock()
        
        # Create retriever with mocked session
        retriever = Neo4jGraphRetriever(mock_graph_session)
        
        # Mock the semantic_search to return empty results
        retriever.retrieval_service.semantic_search = AsyncMock(
            return_value={"results": []}
        )
        
        # Execute search - should not raise UnboundLocalError
        results = await retriever.search(
            query="Who was Helblar?",
            ontology_ids=[1],
            top_k=5,
        )
        
        # Verify empty results are returned without error
        assert results == []
        assert retriever.last_errors == []

    @pytest.mark.asyncio
    async def test_search_aliases_with_empty_results(self):
        """Test that search_aliases handles empty results without error."""
        # Create a mock graph session
        mock_graph_session = AsyncMock()
        
        # Create retriever with mocked session
        retriever = Neo4jGraphRetriever(mock_graph_session)
        
        # Mock the semantic_search to return empty results
        retriever.retrieval_service.semantic_search = AsyncMock(
            return_value={"results": []}
        )
        
        # Execute search - should not raise UnboundLocalError
        results = await retriever.search_aliases(
            query="Who was Helblar?",
            ontology_ids=[1],
            top_k=5,
        )
        
        # Verify empty results are returned without error
        assert results == []
        assert retriever.last_errors == []

    @pytest.mark.asyncio
    async def test_search_with_results(self):
        """Test that search returns properly formatted chunks."""
        # Create a mock graph session
        mock_graph_session = AsyncMock()
        
        # Create retriever with mocked session
        retriever = Neo4jGraphRetriever(mock_graph_session)
        
        # Mock the semantic_search to return sample results
        retriever.retrieval_service.semantic_search = AsyncMock(
            return_value={
                "results": [
                    {
                        "node_id": "node-123",
                        "name": "Helblar",
                        "alias": "The Great Wizard",
                        "instance_id": "instance-456",
                        "chunk_id": "chunk-789",
                        "chunk_type": "text",
                        "chunk_index": 0,
                        "context_text": "Helblar was a legendary wizard.",
                        "score": 0.95,
                        "labels": ["Character"],
                        "properties": {"age": 500},
                    }
                ]
            }
        )
        
        # Execute search
        results = await retriever.search(
            query="Who was Helblar?",
            ontology_ids=[1],
            top_k=5,
        )
        
        # Verify results
        assert len(results) == 1
        chunk = results[0]
        assert chunk.node_id == "node-123"
        assert chunk.node_name == "Helblar"
        assert chunk.node_alias == "The Great Wizard"
        assert chunk.text == "Helblar was a legendary wizard."
        assert chunk.score == 0.95
        assert chunk.confidence_pct == 95.0
        assert chunk.source == "ontology_1"
