from __future__ import annotations

import pytest

from app.integrations.retrieval.neo4j_retriever import HybridNeo4jGraphRetriever


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    async def data(self):
        return self._rows

    async def single(self):
        return self._rows[0] if self._rows else None


class _FakeNode(dict):
    def __init__(self, labels: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.labels = labels


class _FakeSession:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def run(self, query: str, **params):
        self.queries.append(query)
        if "db.index.vector.queryNodes" in query:
            chunk = _FakeNode(
                ["SemanticDocument"],
                chunk_id="chunk-ent-1",
                chunk_type="text",
                chunk_index=0,
                text_chunk="Tamura is bound to the old promise.",
                ontology_id=4,
            )
            parent = _FakeNode(
                ["EntityInstance"],
                entity_instance_id="entity-1",
                alias="Tamura",
                name="Tamura",
                instance_id="inst-1",
                ontology_id=4,
            )
            return _FakeResult([{"chunk": chunk, "parent": parent, "score": 0.92, "source": "vector"}])
        if "db.index.fulltext.queryNodes" in query:
            chunk = _FakeNode(
                ["SemanticDocument"],
                chunk_id="chunk-scene-1",
                chunk_type="scene_main",
                text_chunk="Scene: Tamura remembers the old promise.",
                ontology_id=4,
            )
            parent = _FakeNode(
                ["Scene"],
                id="scene-1",
                name="Old Promise",
                description="Tamura remembers the old promise.",
                instance_id="inst-1",
                ontology_id=4,
                created_at="2026-01-01T00:00:00.000Z",
            )
            return _FakeResult([{"chunk": chunk, "parent": parent, "score": 3.0, "source": "fulltext"}])
        if "UNWIND $anchors AS anchor" in query:
            scene = _FakeNode(
                ["Scene"],
                id="scene-2",
                name="Later Promise",
                description="Tamura acts on the promise.",
                instance_id="inst-1",
                ontology_id=4,
                created_at="2026-01-02T00:00:00.000Z",
            )
            chunk = _FakeNode(
                ["SemanticDocument"],
                chunk_id="chunk-scene-2",
                chunk_type="scene_main",
                text_chunk="Scene: Tamura acts on the promise.",
            )
            return _FakeResult(
                [
                    {
                        "node": scene,
                        "node_label": "Scene",
                        "relation": "RELATES_TO",
                        "anchor_score": 0.85,
                        "chunk": chunk,
                        "has_order_edge": 1,
                        "temporal_value": "2026-01-02T00:00:00.000Z",
                    }
                ]
            )
        raise AssertionError(f"unexpected query: {query}")


class _FakeRuntime:
    async def embed_query(self, query: str, *, request_id: str):
        assert query
        assert request_id
        return [0.1, 0.2, 0.3]


class _TimelineSession:
    async def run(self, query: str, **_params):
        if "RETURN scene AS node" in query:
            return _FakeResult([
                {
                    "node": _FakeNode(
                        ["Scene"], id="older-edited", name="Older edited",
                        description="Edited most recently.", instance_id="inst-1",
                        ontology_id=4, created_at="2026-01-01T00:00:00Z",
                        updated_at="2026-01-05T00:00:00Z",
                    ),
                    "matched_entity_ids": ["ernst"],
                    "derived_from_entity_id": "ernst",
                },
                {
                    "node": _FakeNode(
                        ["Scene"], id="newer-created", name="Newer created",
                        description="Created later but not edited.", instance_id="inst-1",
                        ontology_id=4, created_at="2026-01-04T00:00:00Z",
                        updated_at="2026-01-04T00:00:00Z",
                    ),
                    "matched_entity_ids": ["ernst"],
                    "derived_from_entity_id": "ernst",
                },
                {
                    "node": _FakeNode(
                        ["Scene"], id="undated", name="Undated",
                        description="No comparable timestamp.", instance_id="inst-1",
                        ontology_id=4,
                    ),
                    "matched_entity_ids": ["ernst"],
                    "derived_from_entity_id": "ernst",
                },
            ])
        if "RETURN milestone AS node" in query:
            return _FakeResult([])
        raise AssertionError(f"unexpected query: {query}")


@pytest.mark.asyncio
async def test_hybrid_retriever_does_not_run_index_checks(monkeypatch) -> None:
    session = _FakeSession()
    retriever = HybridNeo4jGraphRetriever(session)

    async def _runtime():
        return _FakeRuntime()

    monkeypatch.setattr(
        "app.integrations.retrieval.neo4j_retriever.get_ready_embedding_runtime",
        _runtime,
    )

    chunks = await retriever.search(
        query="What happens after Tamura's promise?",
        ontology_ids=[4],
        top_k=5,
    )

    expansion_query = next(query for query in session.queries if "UNWIND $anchors AS anchor" in query)
    assert "CALL (a, anchor) {" in expansion_query
    assert "CALL {\n            WITH a, anchor" not in expansion_query

    assert {chunk.node_label for chunk in chunks} >= {"EntityInstance", "Scene"}
    assert any(chunk.source == "hybrid_temporal_expansion" for chunk in chunks)
    assert not any("SHOW INDEXES" in query for query in session.queries)
    assert not any("CREATE " in query for query in session.queries)
    assert retriever.last_search_stats[0]["debug_stats"]["temporal_mode"] == "after"


@pytest.mark.asyncio
async def test_timeline_expansion_orders_by_updated_then_created_and_marks_missing_dates() -> None:
    retriever = HybridNeo4jGraphRetriever(_TimelineSession())

    chunks = await retriever.expand_timeline_context(
        query="What happened to Ernst lately?",
        ontology_ids=[4],
        entity_scores={"ernst": 1.0},
        max_scenes=10,
        max_milestones=10,
        max_total=10,
        temporal_mode="latest",
        temporal_ordering="recency",
        temporal_direction="descending",
    )

    assert [chunk.node_id for chunk in chunks] == [
        "older-edited",
        "newer-created",
        "undated",
    ]
    assert chunks[0].properties["updated_at"] == "2026-01-05T00:00:00Z"
    assert chunks[0].properties["temporal_position"]["rank"] == 0
    assert chunks[2].properties["temporal_position"]["comparable"] is False


@pytest.mark.asyncio
async def test_timeline_expansion_honors_ascending_direction_and_total_limit() -> None:
    retriever = HybridNeo4jGraphRetriever(_TimelineSession())

    chunks = await retriever.expand_timeline_context(
        query="How has Ernst changed?",
        ontology_ids=[4],
        entity_scores={"ernst": 1.0},
        max_scenes=10,
        max_milestones=10,
        max_total=2,
        temporal_ordering="recency",
        temporal_direction="ascending",
    )

    assert [chunk.node_id for chunk in chunks] == ["newer-created", "older-edited"]
