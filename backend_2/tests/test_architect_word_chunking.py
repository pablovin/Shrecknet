"""Tests for word-based chunking in ArchitectOrchestrator."""

from __future__ import annotations

import pytest

from app.jobs.architect.architect import ArchitectOrchestrator


class TestWordBasedChunking:
    """Test suite for word-based text chunking."""

    def test_chunk_text_single_chunk(self):
        """Test that text shorter than chunk_size yields a single chunk."""
        text = " ".join([f"word{i}" for i in range(50)])  # 50 words
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=100, overlap=10)
        )

        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_text_multiple_chunks(self):
        """Test that longer text is split into multiple chunks."""
        text = " ".join([f"word{i}" for i in range(250)])  # 250 words
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=100, overlap=20)
        )

        # With 250 words, chunk_size=100, overlap=20:
        # Chunk 0: words 0-99 (100 words)
        # Chunk 1: words 80-179 (100 words)
        # Chunk 2: words 160-249 (90 words)
        assert len(chunks) == 3

        # Verify word counts
        assert len(chunks[0].split()) == 100
        assert len(chunks[1].split()) == 100
        assert len(chunks[2].split()) == 90

    def test_chunk_text_exact_chunk_size(self):
        """Test text that is exactly the chunk size."""
        text = " ".join([f"word{i}" for i in range(100)])  # 100 words
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=100, overlap=10)
        )

        assert len(chunks) == 1
        assert len(chunks[0].split()) == 100

    def test_chunk_text_with_overlap(self):
        """Test that overlap between chunks works correctly."""
        text = " ".join([f"word{i}" for i in range(150)])
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=100, overlap=20)
        )

        # Should have 2 chunks with 20-word overlap
        assert len(chunks) == 2

        words_chunk0 = chunks[0].split()
        words_chunk1 = chunks[1].split()

        # Last 20 words of chunk 0 should overlap with first 20 of chunk 1
        assert len(words_chunk0) == 100
        assert len(words_chunk1) == 70  # 150 - 80 = 70

        # Verify overlap exists
        overlap_from_chunk0 = words_chunk0[-20:]
        start_of_chunk1 = words_chunk1[:20]

        # The overlapping words should be the same
        assert overlap_from_chunk0 == start_of_chunk1

    def test_chunk_text_real_sentences(self):
        """Test chunking with real text containing sentences."""
        text = "This is a simple test with multiple words that should be chunked properly based on word count not character count."
        chunks = list(ArchitectOrchestrator._chunk_text(text, chunk_size=5, overlap=2))

        # Text has 18 words, so with chunk_size=5 and overlap=2:
        # We expect multiple chunks
        assert len(chunks) > 1

        # Each chunk should have at most 5 words
        for chunk in chunks:
            words = chunk.split()
            assert len(words) <= 5

    def test_chunk_text_realistic_story(self):
        """Test with realistic story size (5000 words)."""
        text = " ".join([f"word{i}" for i in range(5000)])
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=1000, overlap=100)
        )

        # With 5000 words, chunk_size=1000, overlap=100:
        # Chunk 0: 0-999 (1000 words)
        # Chunk 1: 900-1899 (1000 words)
        # Chunk 2: 1800-2799 (1000 words)
        # Chunk 3: 2700-3699 (1000 words)
        # Chunk 4: 3600-4599 (1000 words)
        # Chunk 5: 4500-4999 (500 words)
        assert len(chunks) == 6

    def test_chunk_text_large_story(self):
        """Test with large story size (8000 words)."""
        text = " ".join([f"word{i}" for i in range(8000)])
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=2000, overlap=200)
        )

        # With 8000 words, chunk_size=2000, overlap=200:
        # Should result in 5 chunks
        assert len(chunks) == 5

        # First 4 chunks should have 2000 words each
        for i in range(4):
            assert len(chunks[i].split()) == 2000

        # Last chunk should have remaining words
        assert len(chunks[4].split()) == 800

    def test_default_orchestrator_values(self):
        """Test that default values are set to word-based counts."""

        # Create a minimal stub to avoid import issues
        class StubLLM:
            async def chat(self, *args, **kwargs):
                return "{}"

        class StubPolicy:
            def get_model(self, task):
                return "fake-model"

        class StubRetriever:
            async def search(self, **kwargs):
                return []

        orchestrator = ArchitectOrchestrator(
            llm_client=StubLLM(),
            model_policy=StubPolicy(),
            graph_retriever=StubRetriever(),
        )

        # Verify defaults are word-based
        assert orchestrator.chunk_size == 1000  # 1000 words (was 1200 characters)
        assert orchestrator.chunk_overlap == 100  # 100 words (was 200 characters)

    def test_chunk_text_empty_string(self):
        """Test chunking an empty string."""
        text = ""
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=100, overlap=10)
        )

        assert len(chunks) == 1
        assert chunks[0] == ""

    def test_chunk_text_single_word(self):
        """Test chunking a single word."""
        text = "word"
        chunks = list(
            ArchitectOrchestrator._chunk_text(text, chunk_size=100, overlap=10)
        )

        assert len(chunks) == 1
        assert chunks[0] == "word"
