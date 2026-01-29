"""Test concurrent embedding model access to verify thread-safety."""

import asyncio
import concurrent.futures
import pytest

from app.graphrag.embedding_service import get_embedding_model


def test_concurrent_model_loading():
    """Test that concurrent calls to get_embedding_model() don't cause issues."""

    def load_model_and_encode():
        """Load model and encode a sample text."""
        model = get_embedding_model()
        # Encode a simple text to ensure model works
        result = model.encode(["test text"], normalize_embeddings=True)
        return result.shape

    # Run multiple concurrent threads trying to load/use the model
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(load_model_and_encode) for _ in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All results should be successful
    assert len(results) == 10
    # All should return embeddings with expected shape (1, 384)
    for result in results:
        assert result[0] == 1
        assert result[1] == 384


@pytest.mark.asyncio
async def test_concurrent_embedding_from_async():
    """Test concurrent embedding calls from async context."""
    from app.graphrag.embedding_service import EmbeddingService

    # Mock a graph session (we only need the embed_texts method which doesn't use it)
    class MockSession:
        pass

    service = EmbeddingService(MockSession())

    # Test concurrent embedding calls
    async def embed_batch(batch_id: int):
        loop = asyncio.get_event_loop()
        texts = [f"test text {batch_id}-{i}" for i in range(3)]
        embeddings = await loop.run_in_executor(None, service.embed_texts, texts)
        return embeddings

    # Run 5 concurrent batches
    results = await asyncio.gather(*[embed_batch(i) for i in range(5)])

    # All results should be successful
    assert len(results) == 5
    for embeddings in results:
        assert len(embeddings) == 3
        for emb in embeddings:
            assert len(emb) == 384
