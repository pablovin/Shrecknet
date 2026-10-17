from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.atlas_folio_service import AtlasFolioService


class Result:
    def __init__(self, rows):
        self.rows = rows

    async def data(self):
        return self.rows

    async def single(self):
        return self.rows[0] if self.rows else None


class Graph:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    async def run(self, query, **arguments):
        self.calls.append((query, arguments))
        return Result(next(self.results))


@pytest.mark.asyncio
async def test_detail_checks_world_page_and_skips_embedded_scenes_and_relationships():
    graph = Graph([{"id": "page-1"}])
    class InstanceDetail:
        def model_dump(self):
            return {
                "instance_id": "page-1",
                "ontology_id": 7,
                "name": "Page",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "entities": [],
            }

    instance_detail = InstanceDetail()
    instances = SimpleNamespace(
        graph_session=graph,
        sql_session=None,
        get_instance=AsyncMock(return_value=instance_detail),
    )

    result = await AtlasFolioService(instances).detail(7, "page-1")

    assert result.instance_id == "page-1"
    assert result.folio_projection.model_dump() == {
        "scenes": "omitted",
        "relationships": "omitted",
    }
    instances.get_instance.assert_awaited_once_with(
        "page-1", include_scenes=False, include_relationships=False
    )
    assert graph.calls[0][1] == {"ontology_id": 7, "page_id": "page-1"}


@pytest.mark.asyncio
async def test_relationship_projection_keeps_node_and_page_ids_and_exact_group_totals():
    graph = Graph(
        [{"id": "page-1"}],
        [
            {"definition_id": 8, "direction": "incoming", "total": 1},
            {"definition_id": 8, "direction": "outgoing", "total": 2},
        ],
        [{
            "id": "edge-3:node-1",
            "definition_id": 8,
            "direction": "incoming",
            "focus_entity_id": "node-1",
            "target": {
                "entity_id": "node-2",
                "page_id": "page-2",
                "definition_id": 34,
                "title": "Lady",
                "avatar_url": None,
            },
        }],
    )
    sql = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [(8, "Knows")]
            )
        )
    )
    service = AtlasFolioService(SimpleNamespace(graph_session=graph, sql_session=sql))

    page = await service.relationships(
        ontology_id=7, page_id="page-1", entity_id="node-1",
        group="8:incoming", query="Lady", skip=0, limit=30,
    )

    assert page.total == 1
    assert [(group.key, group.total) for group in page.groups] == [
        ("8:incoming", 1),
        ("8:outgoing", 2),
    ]
    assert page.results[0].focus_entity_id == "node-1"
    assert page.results[0].target.entity_id == "node-2"
    assert page.results[0].target.page_id == "page-2"
    assert page.results[0].target.title == "Lady"
    assert len(graph.calls) == 3
    count_query, count_args = graph.calls[1]
    rows_query, rows_args = graph.calls[2]
    assert "owner.ontology_id IN [$ontology_id, toString($ontology_id)]" in count_query
    assert "count(*) AS total" in count_query
    assert count_args["search_text"] == "lady"
    assert rows_args["group"] == "8:incoming"
    assert rows_args["skip"] == 0 and rows_args["limit"] == 30
    assert "SKIP $skip LIMIT $limit" in rows_query


@pytest.mark.asyncio
async def test_unavailable_or_cross_world_page_cannot_be_used_for_folio_reads():
    graph = Graph([])
    instances = SimpleNamespace(graph_session=graph, sql_session=None)

    with pytest.raises(ValueError, match="Page unavailable"):
        await AtlasFolioService(instances).detail(7, "foreign-page")
    assert len(graph.calls) == 1
