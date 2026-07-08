from __future__ import annotations

import pytest

from app.graph.neo4j import ensure_elder_hybrid_indexes


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []
        self.consumed = False

    async def data(self):
        return self._rows

    async def single(self):
        return self._rows[0] if self._rows else None

    async def consume(self):
        self.consumed = True


class _FakeSession:
    def __init__(self, indexes: list[dict]) -> None:
        self.indexes = indexes
        self.queries: list[str] = []

    async def run(self, query: str, **params):
        del params
        self.queries.append(query)
        if "SHOW INDEXES" in query:
            return _FakeResult(self.indexes)
        if "MATCH (chunk:EntityChunk)" in query:
            return _FakeResult(
                [
                    {
                        "entity_chunk_count": 7,
                        "chunks_missing_text_chunk": 1,
                        "chunks_missing_text_embedding": 2,
                        "entity_parent_count": 3,
                        "scene_parent_count": 2,
                        "milestone_parent_count": 1,
                    }
                ]
            )
        return _FakeResult([])


def _vector_index() -> dict:
    return {
        "name": "entity_chunk_vec_idx",
        "type": "VECTOR",
        "labelsOrTypes": ["EntityChunk"],
        "properties": ["text_embedding"],
    }


def _fulltext_index() -> dict:
    return {
        "name": "entity_chunk_fulltext_idx",
        "type": "FULLTEXT",
        "labelsOrTypes": ["EntityChunk"],
        "properties": ["text_chunk"],
    }


@pytest.mark.asyncio
async def test_elder_hybrid_indexes_noop_when_present() -> None:
    session = _FakeSession([_vector_index(), _fulltext_index()])

    result = await ensure_elder_hybrid_indexes(session)

    assert result["vector_index"] == "present"
    assert result["fulltext_index"] == "present"
    assert result["write_performed"] is False
    assert not any("CREATE VECTOR INDEX" in query for query in session.queries)
    assert not any("CREATE FULLTEXT INDEX" in query for query in session.queries)


@pytest.mark.asyncio
async def test_elder_hybrid_indexes_creates_only_missing_fulltext() -> None:
    session = _FakeSession([_vector_index()])

    result = await ensure_elder_hybrid_indexes(session)

    assert result["vector_index"] == "present"
    assert result["fulltext_index"] == "created"
    assert result["write_performed"] is True
    assert not any("CREATE VECTOR INDEX" in query for query in session.queries)
    assert any("CREATE FULLTEXT INDEX" in query for query in session.queries)


@pytest.mark.asyncio
async def test_elder_hybrid_indexes_creates_only_missing_vector() -> None:
    session = _FakeSession([_fulltext_index()])

    result = await ensure_elder_hybrid_indexes(session)

    assert result["vector_index"] == "created"
    assert result["fulltext_index"] == "present"
    assert result["write_performed"] is True
    assert any("CREATE VECTOR INDEX" in query for query in session.queries)
    assert not any("CREATE FULLTEXT INDEX" in query for query in session.queries)
