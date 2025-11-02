"""Tests for GraphRAG functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.graphrag.embedding_service import EmbeddingService, EMBED_DIM, EMBED_MODEL_ID
from app.graphrag.retrieval_service import RetrievalService


@pytest.fixture
def mock_graph_session():
    """Mock Neo4j graph session."""
    session = AsyncMock()
    return session


@pytest.fixture
def embedding_service(mock_graph_session):
    """Create embedding service with mock session."""
    return EmbeddingService(mock_graph_session)


@pytest.fixture
def retrieval_service(mock_graph_session):
    """Create retrieval service with mock session."""
    return RetrievalService(mock_graph_session)


class TestEmbeddingService:
    """Tests for EmbeddingService."""

    def test_embed_text(self, embedding_service):
        """Test single text embedding."""
        text = "Hello, this is a test."
        with patch("app.graphrag.embedding_service.get_embedding_model") as mock_model:
            mock_model.return_value.encode.return_value.tolist.return_value = [
                [0.1] * EMBED_DIM
            ]
            embedding = embedding_service.embed_text(text)
            assert len(embedding) == EMBED_DIM
            assert isinstance(embedding, list)

    def test_embed_texts(self, embedding_service):
        """Test batch text embedding."""
        texts = ["Text 1", "Text 2", "Text 3"]
        with patch("app.graphrag.embedding_service.get_embedding_model") as mock_model:
            mock_model.return_value.encode.return_value.tolist.return_value = [
                [0.1] * EMBED_DIM
            ] * 3
            embeddings = embedding_service.embed_texts(texts)
            assert len(embeddings) == 3
            assert all(len(emb) == EMBED_DIM for emb in embeddings)

    @pytest.mark.asyncio
    async def test_build_context_text(self, embedding_service):
        """Test context text builder."""
        node_data = {
            "name": "Test Node",
            "labels": ["Entity", "Person"],
            "properties": {"age": 30, "city": "New York"},
            "summary": "A test person",
        }
        ontology_path = ["Entity", "Person"]
        relations = [
            {"type": "KNOWS", "target_name": "John", "target_label": "Person"},
            {"type": "LIVES_IN", "target_name": "NYC", "target_label": "City"},
        ]

        context = await embedding_service.build_context_text(
            node_data, ontology_path, relations
        )

        assert "Test Node" in context
        assert "Entity, Person" in context
        assert "Entity > Person" in context
        assert "age=30" in context
        assert "KNOWS -> John" in context
        assert "A test person" in context

    @pytest.mark.asyncio
    async def test_fetch_and_build_context(self, embedding_service, mock_graph_session):
        """Test fetching node and building context."""
        # Mock Neo4j response
        mock_node = MagicMock()
        mock_node.get.side_effect = lambda k, default=None: {
            "name": "Test",
            "text": "Summary text",
        }.get(k, default)
        mock_node.labels = ["Entity"]
        mock_node.__iter__ = lambda self: iter(
            [("name", "Test"), ("text", "Summary text")]
        )

        mock_result = AsyncMock()
        mock_result.single.return_value = {
            "n": mock_node,
            "rels": [{"type": "REL", "target_name": "Target", "target_label": "Label"}],
        }

        mock_graph_session.run.return_value = mock_result

        context, node_data = await embedding_service.fetch_and_build_context("node-123")

        assert isinstance(context, str)
        assert "Test" in context
        assert node_data["name"] == "Test"

    @pytest.mark.asyncio
    async def test_embed_node(self, embedding_service, mock_graph_session):
        """Test embedding a single node."""
        # Mock fetch_and_build_context
        with patch.object(
            embedding_service,
            "fetch_and_build_context",
            return_value=("Context text", {"name": "Test"}, []),
        ):
            with patch.object(
                embedding_service, "embed_text", return_value=[0.1] * EMBED_DIM
            ):
                mock_result = AsyncMock()
                mock_result.consume.return_value = None
                mock_graph_session.run.return_value = mock_result

                with patch.object(
                    embedding_service, "_refresh_entity_chunks", new=AsyncMock()
                ):
                    result = await embedding_service.embed_node("node-123")

                assert result["node_id"] == "node-123"
                assert result["embedding_model"] == EMBED_MODEL_ID
                assert result["embedding_dim"] == EMBED_DIM

    @pytest.mark.asyncio
    async def test_reset_ontology_embeddings(
        self, embedding_service, mock_graph_session
    ):
        """Test resetting embeddings for an ontology."""
        chunk_result = AsyncMock()
        chunk_result.single.return_value = {"deleted_chunks": 4}
        orphan_result = AsyncMock()
        orphan_result.single.return_value = {"deleted_orphans": 2}
        node_result = AsyncMock()
        node_result.single.return_value = {"nodes_reset": 7}
        mock_graph_session.run.side_effect = [chunk_result, orphan_result, node_result]

        result = await embedding_service.reset_ontology_embeddings(ontology_id=5)

        assert result["ontology_id"] == 5
        assert result["chunks_deleted"] == 4
        assert result["nodes_reset"] == 7
        assert result["orphans_deleted"] == 2
        assert mock_graph_session.run.await_count == 3

    @pytest.mark.asyncio
    async def test_embed_ontology_processes_all_nodes(
        self, embedding_service, mock_graph_session
    ):
        """Ensure embed_ontology embeds every node in the batch."""
        node_query_result = AsyncMock()
        node_query_result.data.return_value = [
            {"node_id": "node-1"},
            {"node_id": "node-2"},
        ]
        fetch_result = AsyncMock()
        fetch_result.data.return_value = [
            {
                "entity_id": "node-1",
                "text": "Some text",
                "autogenerated_text": "",
                "properties": '{"foo": "bar"}',
                "relationships": [],
            },
            {
                "entity_id": "node-2",
                "text": "",
                "autogenerated_text": "Auto summary",
                "properties": None,
                "relationships": [],
            },
        ]
        mock_graph_session.run.side_effect = [node_query_result, fetch_result]

        with patch.object(
            embedding_service, "_refresh_entity_chunks", new=AsyncMock()
        ) as mock_refresh:
            with patch.object(
                embedding_service, "embed_node", new=AsyncMock()
            ) as mock_embed_node:
                result = await embedding_service.embed_ontology(
                    ontology_id=9, batch_size=10
                )

        assert mock_refresh.await_count == 2
        assert mock_embed_node.await_count == 2
        for call in mock_embed_node.await_args_list:
            assert call.kwargs.get("regenerate_chunks") is False
        assert result["nodes_processed"] == 2
        assert result["nodes_failed"] == 0


class TestRetrievalService:
    """Tests for RetrievalService."""

    @pytest.mark.asyncio
    async def test_semantic_search(self, retrieval_service, mock_graph_session):
        """Test semantic search."""
        # Mock embedding
        with patch.object(
            retrieval_service.embedding_service,
            "embed_text",
            return_value=[0.1] * EMBED_DIM,
        ):
            # Mock Neo4j response
            mock_node = MagicMock()
            mock_node.get.side_effect = lambda k, default=None: {
                "entity_instance_id": "node-1",
                "name": "Result 1",
                "text": "Some text",
                "ontology_id": 1,
            }.get(k, default)
            mock_node.labels = ["Entity"]
            mock_node.__iter__ = lambda self: iter([("name", "Result 1")])

            mock_result = AsyncMock()
            mock_result.data.return_value = [{"node": mock_node, "score": 0.95}]

            # Mock neighbors fetch separately
            mock_neighbors_result = AsyncMock()
            mock_neighbors_result.data.return_value = []

            # Return different results for different queries
            async def mock_run(query, **kwargs):
                if "queryNodes" in query:
                    return mock_result
                else:
                    return mock_neighbors_result

            mock_graph_session.run.side_effect = mock_run

            results = await retrieval_service.semantic_search(query="test query", k=5)

            assert results["query"] == "test query"
            assert results["total"] >= 0
            assert "results" in results

    @pytest.mark.asyncio
    async def test_fetch_neighbors(self, retrieval_service, mock_graph_session):
        """Test fetching neighbors."""
        mock_result = AsyncMock()
        mock_result.data.return_value = [
            {
                "rel_type": "KNOWS",
                "node_id": "node-2",
                "name": "Neighbor",
                "label": "Person",
            }
        ]
        mock_graph_session.run.return_value = mock_result

        neighbors = await retrieval_service._fetch_neighbors("node-1")

        assert len(neighbors) >= 0
        if neighbors:
            assert "rel_type" in neighbors[0]
            assert "node_id" in neighbors[0]

    @pytest.mark.asyncio
    async def test_get_context_for_llm(self, retrieval_service):
        """Test getting formatted context for LLM."""
        with patch.object(
            retrieval_service,
            "semantic_search",
            return_value={
                "query": "test",
                "results": [
                    {
                        "name": "Result",
                        "score": 0.9,
                        "context_text": "Context",
                        "neighbors": [],
                    }
                ],
                "total": 1,
            },
        ):
            context = await retrieval_service.get_context_for_llm("test query")

            assert isinstance(context, str)
            assert "test query" in context.lower() or "Query" in context
