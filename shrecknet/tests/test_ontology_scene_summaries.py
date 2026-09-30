from __future__ import annotations

import pytest

from app.services.ontology_instance_service import OntologyInstanceService


class _Result:
    async def data(self) -> list[dict]:
        return [
            {
                "id": "scene-1",
                "instance_id": "page-1",
                "ontology_id": 7,
                "name": "Arrival",
                "description": "The party arrives.",
                "source_page_name": "Chapter One",
                "source_page_image": None,
                "milestone_count": 1,
                "perspective_count": 2,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]


class _Graph:
    def __init__(self) -> None:
        self.arguments: dict | None = None

    async def run(self, query: str, **kwargs):
        self.arguments = {"query": query, **kwargs}
        return _Result()


@pytest.mark.asyncio
async def test_list_scene_summaries_uses_non_conflicting_query_parameter() -> None:
    graph = _Graph()
    service = OntologyInstanceService(sql_session=None, graph_session=graph)

    summaries = await service.list_scene_summaries(
        ontology_id=7,
        query="arrival",
    )

    assert graph.arguments is not None
    assert "$scene_query" in graph.arguments["query"]
    assert graph.arguments["scene_query"] == "arrival"
    assert "query" not in {key for key in graph.arguments if key != "query"}
    assert summaries[0].name == "Arrival"
